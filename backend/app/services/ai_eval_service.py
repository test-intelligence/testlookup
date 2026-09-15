"""
AI Evaluation Service — dataset management, metric computation, drift detection (OPS-02).

Computes precision, recall, F1, accuracy, and human-AI agreement from labeled datasets
and persisted AI feedback. Tracks quality drift over time via AIEvalRun records.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AIAnalysis,
    AIEvalRun,
    AIFeedback,
    ModelVersion,
)
from app.services.eval_label_provenance import EVAL_LABEL_HOLDOUT_DAYS

logger = logging.getLogger("services.ai_eval")


# ── Dataset from feedback ────────────────────────────────────────────────────


async def build_dataset_from_feedback(
    db: AsyncSession,
    task_type: str = "classification",
    min_confidence: int = 0,
    *,
    as_of: datetime | None = None,
) -> list[dict]:
    """
    Build a labeled evaluation dataset from human feedback records.

    Each item has: input (AI analysis data) and expected_output (human correction
    or agreement). The items are **classification-shaped** (input carries
    ``failure_category``/``confidence_score``/``is_flaky``); this builder only
    produces classification feedback, so the ``task_type`` argument is a label
    for the caller's dataset row, not a shape switch.

    ``min_confidence`` (0-100) keeps only feedback on analyses whose
    ``confidence_score`` is at or above the floor — useful to train/evaluate
    against the model's higher-confidence predictions. ``0`` (default) applies
    no floor and keeps rows with a NULL confidence.

    E9.10 applies a 14-day holdout and requires source-manifest provenance.
    The release gate rechecks both rules because a stored dataset can be reused
    against a later candidate manifest.
    """
    cutoff = (as_of or datetime.now(timezone.utc)) - timedelta(
        days=EVAL_LABEL_HOLDOUT_DAYS
    )
    query = (
        select(AIFeedback, AIAnalysis)
        .join(AIAnalysis, AIFeedback.analysis_id == AIAnalysis.id)
        .where(
            AIFeedback.rating.in_(["correct", "incorrect"]),
            AIFeedback.created_at <= cutoff,
            AIFeedback.eval_manifest_checksum.isnot(None),
        )
    )
    if min_confidence:
        # Was previously accepted but never applied — a silent no-op that let a
        # caller think they were filtering to high-confidence feedback.
        query = query.where(AIAnalysis.confidence_score >= min_confidence)
    result = await db.execute(query.order_by(AIFeedback.created_at.desc()).limit(1000))
    rows = result.all()

    items: list[dict] = []
    for feedback, analysis in rows:
        item: dict[str, Any] = {
            "input": {
                "root_cause_summary": analysis.root_cause_summary or "",
                "failure_category": str(analysis.failure_category) if analysis.failure_category else "UNKNOWN",
                "confidence_score": analysis.confidence_score or 0,
                "is_flaky": analysis.is_flaky,
            },
            "expected_output": {
                "failure_category": str(feedback.corrected_category) if feedback.corrected_category else str(analysis.failure_category),
                "correct": feedback.rating == "correct",
            },
            "metadata": {
                "feedback_id": str(feedback.id),
                "analysis_id": str(analysis.id),
                "source": feedback.source,
                "label_source": "feedback",
                "label_created_at": feedback.created_at.isoformat(),
                "eval_manifest_checksum": feedback.eval_manifest_checksum,
                # AI-4: which engine tier produced the prediction (llm / ml /
                # rules / human_corrected), recovered from the persisted
                # routing audit. None for rows written before routing
                # metadata existed — kind-precision-per-tier reports those
                # honestly as tier "unknown" instead of guessing.
                "analysis_tier": (analysis.routing_metadata or {}).get("analysis_mode"),
            },
        }
        items.append(item)

    return items


# ── Label health (AI-F1 honesty surfacing) ──────────────────────────────────


async def get_label_health(db: AsyncSession) -> dict:
    """Human-label coverage of the ML training pool + last-trained mix.

    Returns live counts by provenance bucket (``human_direct`` /
    ``human_indirect`` feedback labels, ``llm_pseudo`` candidate analyses),
    the configured human-label floor, and the ``label_composition`` the
    currently deployed model was actually trained on (from its metadata) —
    so the UI can state honestly whether ML mode learns from corrections or
    is still bootstrap (LLM-imitating).
    """
    from app.core.config import settings
    from app.services.ml.label_provenance import (
        HUMAN_DIRECT,
        model_maturity_from_metadata,
        provenance_for_feedback_source,
    )

    usable_label = or_(
        AIFeedback.rating == "correct",
        and_(
            AIFeedback.rating == "incorrect",
            AIFeedback.corrected_category.isnot(None),
        ),
    )

    # Human labels grouped by source → provenance bucket
    rows = await db.execute(
        select(AIFeedback.source, func.count(AIFeedback.id))
        .where(usable_label)
        .group_by(AIFeedback.source)
    )
    human_direct = 0
    human_indirect = 0
    for source, count in rows.all():
        if provenance_for_feedback_source(source) == HUMAN_DIRECT:
            human_direct += count
        else:
            human_indirect += count
    human_total = human_direct + human_indirect

    # Pseudo-label candidates: high-confidence analyses with no feedback row
    pseudo_count = (
        await db.execute(
            select(func.count(AIAnalysis.id)).where(
                AIAnalysis.confidence_score >= 80,
                AIAnalysis.failure_category.isnot(None),
                ~exists().where(AIFeedback.analysis_id == AIAnalysis.id),
            )
        )
    ).scalar() or 0

    # Last-trained composition from the deployed model's metadata (file read,
    # best-effort — the live counts above must not depend on it).
    last_composition = None
    maturity = "not_trained"
    try:
        from app.services.ml.classifier import MLClassifier

        info = MLClassifier.get_model_info()
        maturity = model_maturity_from_metadata(info, settings.ML_HUMAN_LABEL_FLOOR)
        composition = info.get("label_composition")
        if isinstance(composition, dict):
            last_composition = composition
    except Exception:  # pragma: no cover — metadata read is best-effort
        pass

    floor = settings.ML_HUMAN_LABEL_FLOOR
    pool_total = human_total + pseudo_count
    return {
        "human_direct": human_direct,
        "human_indirect": human_indirect,
        "human_label_total": human_total,
        "llm_pseudo_candidates": pseudo_count,
        "human_share_of_pool": round(human_total / pool_total, 4) if pool_total else None,
        "human_label_floor": floor,
        "meets_human_label_floor": human_total >= floor,
        "ml_maturity": maturity,
        "last_trained_composition": last_composition,
        "pseudo_label_cap": settings.ML_PSEUDO_LABEL_CAP,
        "pseudo_label_weight": settings.ML_PSEUDO_LABEL_WEIGHT,
    }


# ── Metrics computation ──────────────────────────────────────────────────────


def compute_classification_metrics(items: list[dict]) -> dict:
    """Compute precision, recall, F1, and accuracy for classification items."""
    if not items:
        return {"precision": None, "recall": None, "f1_score": None, "accuracy": None, "total": 0, "correct": 0}

    total = len(items)
    correct = sum(1 for item in items if item.get("expected_output", {}).get("correct", False))
    accuracy = correct / total if total > 0 else 0.0

    # For multi-class: compute macro-average precision/recall
    categories: set[str] = set()
    for item in items:
        categories.add(item.get("input", {}).get("failure_category", "UNKNOWN"))
        categories.add(item.get("expected_output", {}).get("failure_category", "UNKNOWN"))

    precisions: list[float] = []
    recalls: list[float] = []

    for cat in categories:
        tp = sum(1 for item in items
                 if item.get("input", {}).get("failure_category") == cat
                 and item.get("expected_output", {}).get("correct", False))
        fp = sum(1 for item in items
                 if item.get("input", {}).get("failure_category") == cat
                 and not item.get("expected_output", {}).get("correct", False))
        fn = sum(1 for item in items
                 if item.get("expected_output", {}).get("failure_category") == cat
                 and item.get("input", {}).get("failure_category") != cat)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precisions.append(precision)
        recalls.append(recall)

    avg_precision = sum(precisions) / len(precisions) if precisions else 0.0
    avg_recall = sum(recalls) / len(recalls) if recalls else 0.0
    f1 = 2 * avg_precision * avg_recall / (avg_precision + avg_recall) if (avg_precision + avg_recall) > 0 else 0.0

    return {
        "precision": round(avg_precision, 4),
        "recall": round(avg_recall, 4),
        "f1_score": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "total": total,
        "correct": correct,
        # AI-4: derived kind-triad precision (additive sub-key — existing
        # consumers read the flat keys above unchanged).
        "kind_metrics": compute_kind_classification_metrics(items),
    }


def compute_kind_classification_metrics(items: list[dict]) -> dict:
    """Kind-triad classification precision, overall and per engine tier (AI-4).

    The failure *kind* (product / test_code / infrastructure / unknown) is a
    pure function of the category (``services/failure_kind.py``), so kind
    precision is computable from any classification item that carries a
    category on BOTH sides — which the feedback-built datasets and the golden
    classification items both do. The AI-F4 provenance caveat still applies
    to what this measures: golden items score classification *agreement* on
    pre-labeled summaries, not raw-error classification.

    Per-tier attribution comes from ``metadata.analysis_tier`` (recorded from
    the persisted routing audit by ``build_dataset_from_feedback``). Items
    without a recorded tier — golden items, or feedback on pre-routing rows —
    land in tier ``"unknown"`` with an honest note; no tier is ever guessed.

    Honest empty state: ``{"computable": False, "reason": ...}`` when no item
    carries usable labels — never fake numbers.
    """
    from app.services.failure_kind import failure_kind

    usable = [
        item for item in items
        if (item.get("input") or {}).get("failure_category")
        and (item.get("expected_output") or {}).get("failure_category")
    ]
    if not usable:
        return {
            "computable": False,
            "reason": (
                "no items carry a failure_category on both the prediction and "
                "the expected label — kind precision is not computable"
            ),
            "total": len(items),
        }

    def _kinds(subset: list[dict]) -> list[tuple[str, str]]:
        return [
            (
                failure_kind(item["input"]["failure_category"], None),
                failure_kind(item["expected_output"]["failure_category"], None),
            )
            for item in subset
        ]

    def _metrics(pairs: list[tuple[str, str]]) -> dict:
        total = len(pairs)
        correct = sum(1 for predicted, expected in pairs if predicted == expected)
        kinds = {k for pair in pairs for k in pair}
        precisions: list[float] = []
        for kind in kinds:
            tp = sum(1 for p, e in pairs if p == kind and e == kind)
            fp = sum(1 for p, e in pairs if p == kind and e != kind)
            precisions.append(tp / (tp + fp) if (tp + fp) > 0 else 0.0)
        return {
            "precision": round(sum(precisions) / len(precisions), 4) if precisions else None,
            "accuracy": round(correct / total, 4) if total else None,
            "total": total,
            "correct": correct,
        }

    by_tier: dict[str, list[dict]] = {}
    for item in usable:
        tier = str((item.get("metadata") or {}).get("analysis_tier") or "unknown")
        by_tier.setdefault(tier, []).append(item)

    tiers = {tier: _metrics(_kinds(subset)) for tier, subset in sorted(by_tier.items())}
    if "unknown" in tiers:
        tiers["unknown"]["note"] = (
            "engine tier not recorded on these items (golden items or "
            "pre-routing-audit rows) — precision here is real but not "
            "attributable to a specific tier"
        )

    return {
        "computable": True,
        "overall": _metrics(_kinds(usable)),
        "by_tier": tiers,
        "total": len(items),
        "usable": len(usable),
    }


def compute_root_cause_metrics(items: list[dict]) -> dict:
    """Compute quality metrics for root cause extraction items."""
    if not items:
        return {"precision": None, "recall": None, "f1_score": None, "accuracy": None, "total": 0, "correct": 0}

    total = len(items)
    correct = 0

    for item in items:
        expected = item.get("expected_output", {})
        inp = item.get("input", {})

        # A root cause is "correct" if:
        # 1. It matches the expected has_root_cause state
        # 2. It matches the expected category (if provided)
        has_rc = bool(inp.get("root_cause_summary") or inp.get("error_message"))
        expected_has_rc = expected.get("has_root_cause", True)
        expected_cat = expected.get("category")
        actual_cat = inp.get("failure_category", inp.get("category"))

        if has_rc == expected_has_rc:
            if expected_cat and actual_cat:
                if str(expected_cat).upper() == str(actual_cat).upper():
                    correct += 1
            else:
                correct += 1

    accuracy = correct / total if total > 0 else 0.0
    # For root cause, precision ≈ accuracy (single-label evaluation)
    return {
        "precision": round(accuracy, 4),
        "recall": round(accuracy, 4),
        "f1_score": round(accuracy, 4),
        "accuracy": round(accuracy, 4),
        "total": total,
        "correct": correct,
    }


def compute_duplicate_detection_metrics(items: list[dict]) -> dict:
    """Compute precision/recall for duplicate detection items."""
    if not items:
        return {"precision": None, "recall": None, "f1_score": None, "accuracy": None, "total": 0, "correct": 0}

    total = len(items)
    tp = fp = fn = tn = 0

    for item in items:
        expected = item.get("expected_output", {})
        inp = item.get("input", {})

        expected_dup = expected.get("is_duplicate", False)
        # Simulate detection: same component + title overlap = duplicate
        title_a = (inp.get("title_a", "") or "").lower()
        title_b = (inp.get("title_b", "") or "").lower()
        comp_a = (inp.get("component_a", "") or "").lower()
        comp_b = (inp.get("component_b", "") or "").lower()

        # Simple word overlap similarity
        words_a = set(title_a.split())
        words_b = set(title_b.split())
        overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
        threshold = expected.get("similarity_threshold", 0.7)

        predicted_dup = overlap >= threshold and comp_a == comp_b

        if expected_dup and predicted_dup:
            tp += 1
        elif not expected_dup and predicted_dup:
            fp += 1
        elif expected_dup and not predicted_dup:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "total": total,
        "correct": tp + tn,
    }


def compute_release_decision_metrics(items: list[dict]) -> dict:
    """Compute accuracy for release decision (GO/NO_GO/CONDITIONAL_GO) items."""
    if not items:
        return {"precision": None, "recall": None, "f1_score": None, "accuracy": None, "total": 0, "correct": 0}

    total = len(items)
    correct = 0

    for item in items:
        expected = item.get("expected_output", {})
        inp = item.get("input", {})

        expected_rec = expected.get("recommendation", "")
        risk_score = inp.get("risk_score", 50)

        # Deterministic recommendation from risk score (matches release gate logic)
        if risk_score < 20:
            predicted = "GO"
        elif risk_score >= 55:
            predicted = "NO_GO"
        else:
            predicted = "CONDITIONAL_GO"

        if predicted == expected_rec:
            correct += 1

    accuracy = correct / total if total > 0 else 0.0
    return {
        "precision": round(accuracy, 4),
        "recall": round(accuracy, 4),
        "f1_score": round(accuracy, 4),
        "accuracy": round(accuracy, 4),
        "total": total,
        "correct": correct,
    }


def compute_metrics_for_task_type(task_type: str, items: list[dict]) -> dict:
    """Route evaluation to the correct metrics function based on task_type."""
    dispatch = {
        "classification": compute_classification_metrics,
        "root_cause": compute_root_cause_metrics,
        "duplicate_detection": compute_duplicate_detection_metrics,
        "release_decision": compute_release_decision_metrics,
    }
    fn = dispatch.get(task_type, compute_classification_metrics)
    return fn(items)


def compute_agent_report_quality(samples: list) -> dict:
    """Score recorded agent outputs into a JSON-safe quality report (AIQ-P5).

    Thin sync adapter over ``agent_eval_harness.evaluate_agent_outputs``. Each
    item may already be an ``AgentEvalSample`` (passed through) or a mapping that
    is coerced via ``AgentEvalSample(**item)``. The harness never raises; this
    helper mirrors that invariant and degrades a malformed item to defaults.
    The lazy import keeps the harness (pure-local, no DB/LLM) off the module's
    import path until a caller actually needs it.
    """
    from app.services.agent_eval_harness import (
        AgentEvalSample,
        evaluate_agent_outputs,
    )

    coerced: list[AgentEvalSample] = []
    for item in samples if isinstance(samples, (list, tuple)) else []:
        if isinstance(item, AgentEvalSample):
            coerced.append(item)
        elif isinstance(item, dict):
            coerced.append(AgentEvalSample(**item))
    return evaluate_agent_outputs(coerced).model_dump(mode="json")


# ── Human-AI agreement from feedback ────────────────────────────────────────


async def compute_agreement_rate(
    db: AsyncSession,
    days: int = 30,
) -> dict:
    """Compute human-AI agreement rate from recent feedback."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Single aggregate query with conditional counts instead of three separate
    # COUNTs over the same ``created_at >= cutoff`` window (3 round trips → 1).
    # ``count(case((cond, 1)))`` counts only the rows matching cond (the CASE
    # yields NULL otherwise, which COUNT skips) — identical to the per-rating
    # filtered COUNTs.
    row = (
        await db.execute(
            select(
                func.count(AIFeedback.id).label("total"),
                func.count(case((AIFeedback.rating == "correct", 1))).label("correct"),
                func.count(
                    case((AIFeedback.rating == "partially_correct", 1))
                ).label("partial"),
            ).where(AIFeedback.created_at >= cutoff)
        )
    ).one()
    total = row.total or 0
    correct = row.correct or 0
    partial = row.partial or 0

    agreement = (correct + partial * 0.5) / total if total > 0 else None

    return {
        "total_feedback": total,
        "correct": correct,
        "partially_correct": partial,
        "incorrect": total - correct - partial,
        "agreement_rate": round(agreement, 4) if agreement is not None else None,
        "period_days": days,
    }


# ── Drift detection ─────────────────────────────────────────────────────────


async def detect_quality_drift(
    db: AsyncSession,
    task_type: str = "classification",
    window_days: int = 7,
) -> dict:
    """
    Compare recent eval runs to detect quality drift.

    Returns current vs previous window metrics and drift direction.
    """
    now = datetime.now(timezone.utc)
    current_cutoff = now - timedelta(days=window_days)
    previous_cutoff = current_cutoff - timedelta(days=window_days)

    # Current window
    current_result = await db.execute(
        select(
            func.avg(AIEvalRun.accuracy).label("avg_accuracy"),
            func.avg(AIEvalRun.f1_score).label("avg_f1"),
            func.avg(AIEvalRun.agreement_rate).label("avg_agreement"),
            func.count(AIEvalRun.id).label("run_count"),
            func.coalesce(func.sum(AIEvalRun.correct_items), 0).label("correct_items"),
            func.coalesce(func.sum(AIEvalRun.total_items), 0).label("total_items"),
        )
        .where(AIEvalRun.task_type == task_type, AIEvalRun.evaluated_at >= current_cutoff)
    )
    current = current_result.one_or_none()

    # Previous window
    previous_result = await db.execute(
        select(
            func.avg(AIEvalRun.accuracy).label("avg_accuracy"),
            func.avg(AIEvalRun.f1_score).label("avg_f1"),
            func.avg(AIEvalRun.agreement_rate).label("avg_agreement"),
            func.count(AIEvalRun.id).label("run_count"),
            func.coalesce(func.sum(AIEvalRun.correct_items), 0).label("correct_items"),
            func.coalesce(func.sum(AIEvalRun.total_items), 0).label("total_items"),
        )
        .where(
            AIEvalRun.task_type == task_type,
            AIEvalRun.evaluated_at >= previous_cutoff,
            AIEvalRun.evaluated_at < current_cutoff,
        )
    )
    previous = previous_result.one_or_none()

    def _safe_float(v: Any) -> float | None:
        return round(float(v), 4) if v is not None else None

    current_accuracy = _safe_float(current.avg_accuracy) if current else None
    previous_accuracy = _safe_float(previous.avg_accuracy) if previous else None

    from app.services.online_drift_service import compare_rates  # noqa: PLC0415

    rate_comparison = compare_rates(
        "eval_accuracy",
        current_successes=int(current.correct_items) if current else 0,
        current_total=int(current.total_items) if current else 0,
        previous_successes=int(previous.correct_items) if previous else 0,
        previous_total=int(previous.total_items) if previous else 0,
    )
    drift = (
        round(current_accuracy - previous_accuracy, 4)
        if current_accuracy is not None and previous_accuracy is not None
        else None
    )
    drift_direction = rate_comparison["direction"]

    return {
        "task_type": task_type,
        "window_days": window_days,
        "current": {
            "accuracy": current_accuracy,
            "f1_score": _safe_float(current.avg_f1) if current else None,
            "agreement_rate": _safe_float(current.avg_agreement) if current else None,
            "eval_run_count": current.run_count if current else 0,
            "accuracy_ci95": rate_comparison["current"]["ci95"],
            "sample_count": rate_comparison["current"]["total"],
        },
        "previous": {
            "accuracy": previous_accuracy,
            "f1_score": _safe_float(previous.avg_f1) if previous else None,
            "agreement_rate": _safe_float(previous.avg_agreement) if previous else None,
            "eval_run_count": previous.run_count if previous else 0,
            "accuracy_ci95": rate_comparison["previous"]["ci95"],
            "sample_count": rate_comparison["previous"]["total"],
        },
        "drift": drift,
        "drift_direction": drift_direction,
        "measured": rate_comparison["measured"],
    }


# ── Model version history ───────────────────────────────────────────────────


async def get_model_version_history(
    db: AsyncSession,
    track: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Get model version history with evaluation metrics."""
    query = select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(limit)
    if track:
        query = query.where(ModelVersion.track == track)
    result = await db.execute(query)
    versions = result.scalars().all()

    return [
        {
            "id": str(v.id),
            "track": v.track,
            "model_name": v.model_name,
            "provider": v.provider,
            "status": v.status,
            "training_examples": v.training_examples,
            "eval_accuracy": v.eval_accuracy,
            "baseline_accuracy": v.baseline_accuracy,
            "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]
