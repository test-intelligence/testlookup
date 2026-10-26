"""
Evaluation Gate Service — Phase 5: Make Evaluation a Release Gate.

Evaluates agent quality against baselines and configurable thresholds.
Returns a shared evaluation verdict with per-rule results so that prompt, model, or routing
changes cannot ship without passing quality gates.

Gate flow:
  1. Load active baselines for the specified task_type/agent
  2. Run evaluation against the specified dataset
  3. Compare current metrics to baseline
  4. Apply threshold rules (min_accuracy, min_f1, max_regression_pct)
  5. Return structured gate result
"""
from __future__ import annotations

import logging
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalBaseline, AIEvalDataset, AIEvalGateRun, AIEvalRun
from app.services.ai_eval_service import compute_metrics_for_task_type
from app.services.eval_label_provenance import gate_eligible_items
from app.services.eval_verdict import EvalVerdict

logger = logging.getLogger("services.eval_gate")


GateStatus = EvalVerdict


DEFAULT_AGENT_STACK_GATES: tuple[dict[str, str], ...] = (
    {"task_type": "classification", "agent_name": "AnalysisAgent"},
    {"task_type": "root_cause", "agent_name": "AnalysisAgent"},
    {"task_type": "duplicate_detection", "agent_name": "DefectPromotionAgent"},
    {"task_type": "release_decision", "agent_name": "ReleaseRiskAgent"},
)
_BLOCKING_GATE_STATUSES = {EvalVerdict.FAIL, EvalVerdict.INSUFFICIENT_SAMPLES}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _manifest_checksum(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def _coerce_dataset_uuid(dataset_id: Optional[str]) -> Optional[uuid.UUID]:
    """Parse a dataset_id into a UUID, returning ``None`` on missing/malformed
    input. The request body types ``dataset_id`` as ``Optional[str]`` (not a
    UUID), so a malformed value would otherwise raise deep in the gate and 500
    the request; treat it as "no specific dataset" and let the caller fall back
    to the golden dataset instead."""
    if not dataset_id:
        return None
    try:
        return uuid.UUID(dataset_id)
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            "Ignoring malformed dataset_id %r — falling back to golden dataset",
            dataset_id,
        )
        return None


def build_agent_stack_gate_manifest(
    *,
    change_id: str,
    prompt_versions: Optional[dict[str, str]] = None,
    model_versions: Optional[dict[str, str]] = None,
    routing_versions: Optional[dict[str, str]] = None,
    required_gates: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Build a deterministic manifest for prompt/model/routing changes."""
    gates = required_gates or [dict(gate) for gate in DEFAULT_AGENT_STACK_GATES]
    gates = sorted(
        [
            {
                "task_type": str(gate["task_type"]),
                "agent_name": str(gate["agent_name"]),
                **({"dataset_id": str(gate["dataset_id"])} if gate.get("dataset_id") else {}),
            }
            for gate in gates
        ],
        key=lambda gate: (gate["task_type"], gate["agent_name"], gate.get("dataset_id", "")),
    )
    manifest = {
        "schema_version": 1,
        "change_id": change_id,
        "prompt_versions": dict(sorted((prompt_versions or {}).items())),
        "model_versions": dict(sorted((model_versions or {}).items())),
        "routing_versions": dict(sorted((routing_versions or {}).items())),
        "required_gates": gates,
    }
    manifest["manifest_checksum_sha256"] = _manifest_checksum(manifest)
    return manifest


def _version_change_summary(
    manifest: dict[str, Any],
    gate_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare requested prompt/model/routing versions to active baselines."""
    prompt_versions = manifest.get("prompt_versions") or {}
    model_versions = manifest.get("model_versions") or {}
    routing_versions = manifest.get("routing_versions") or {}
    changes: list[dict[str, Any]] = []

    for result in sorted(
        gate_results,
        key=lambda item: (str(item.get("task_type")), str(item.get("agent_name"))),
    ):
        agent_name = result.get("agent_name")
        baseline = result.get("baseline_metrics") or {}
        changes.append({
            "task_type": result.get("task_type"),
            "agent_name": agent_name,
            "prompt_version": {
                "candidate": prompt_versions.get(agent_name),
                "baseline": baseline.get("prompt_version"),
                "changed": prompt_versions.get(agent_name) != baseline.get("prompt_version"),
            },
            "model_name": {
                "candidate": model_versions.get(agent_name),
                "baseline": baseline.get("model_name"),
                "changed": model_versions.get(agent_name) != baseline.get("model_name"),
            },
            "routing_version": {
                "candidate": routing_versions.get(agent_name),
                "baseline": None,
                "changed": bool(routing_versions.get(agent_name)),
            },
        })
    return changes


def _overall_manifest_status(gate_results: list[dict[str, Any]]) -> str:
    statuses = [result.get("status") for result in gate_results]
    if any(status in _BLOCKING_GATE_STATUSES for status in statuses):
        return GateStatus.FAIL
    return EvalVerdict.PASS


def _verdict_value(value: Any) -> str:
    return value.value if isinstance(value, EvalVerdict) else str(value)


async def evaluate_agent_stack_release_gate(
    db: AsyncSession,
    *,
    change_id: str,
    prompt_versions: Optional[dict[str, str]] = None,
    model_versions: Optional[dict[str, str]] = None,
    routing_versions: Optional[dict[str, str]] = None,
    required_gates: Optional[list[dict[str, str]]] = None,
    evaluated_by: Optional[Any] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Evaluate all required agent-stack gates before shipping a change."""
    manifest = build_agent_stack_gate_manifest(
        change_id=change_id,
        prompt_versions=prompt_versions,
        model_versions=model_versions,
        routing_versions=routing_versions,
        required_gates=required_gates,
    )
    evaluated_at = datetime.now(timezone.utc)

    gate_results: list[dict[str, Any]] = []
    for gate in manifest["required_gates"]:
        gate_results.append(
            await evaluate_pre_release_gate(
                db,
                task_type=gate["task_type"],
                agent_name=gate["agent_name"],
                dataset_id=gate.get("dataset_id"),
                gate_manifest_checksum=manifest["manifest_checksum_sha256"],
                evaluated_at=evaluated_at,
            )
        )

    blocking_gates = [
        {
            "task_type": result.get("task_type"),
            "agent_name": result.get("agent_name"),
            "status": result.get("status"),
        }
        for result in gate_results
        if result.get("status") in _BLOCKING_GATE_STATUSES
    ]
    result = {
        "status": _overall_manifest_status(gate_results),
        "manifest": manifest,
        "gate_results": gate_results,
        "blocking_gates": blocking_gates,
        "version_changes": _version_change_summary(manifest, gate_results),
        "evaluated_at": evaluated_at.isoformat(),
    }
    if persist:
        gate_run = await persist_agent_stack_gate_run(
            db,
            gate_result=result,
            evaluated_by=evaluated_by,
        )
        result["gate_run_id"] = str(gate_run.id)
    return result


async def persist_agent_stack_gate_run(
    db: AsyncSession,
    *,
    gate_result: dict[str, Any],
    evaluated_by: Optional[Any] = None,
) -> AIEvalGateRun:
    """Persist an aggregate agent-stack release gate decision for audit."""
    manifest = gate_result.get("manifest") or {}
    row = AIEvalGateRun(
        change_id=str(manifest.get("change_id") or ""),
        status=_verdict_value(gate_result.get("status") or EvalVerdict.FAIL),
        manifest_checksum_sha256=str(manifest.get("manifest_checksum_sha256") or ""),
        manifest=manifest,
        gate_results=list(gate_result.get("gate_results") or []),
        blocking_gates=list(gate_result.get("blocking_gates") or []),
        version_changes=list(gate_result.get("version_changes") or []),
        evaluated_by=evaluated_by,
    )
    db.add(row)
    # Service receives an injected session — committing is the caller's
    # responsibility (the request handler's get_db dependency, or the
    # Celery task's session context manager). Flush so callers can read
    # ``row.id`` immediately.
    await db.flush()
    logger.info(
        "Persisted agent-stack release gate run",
        extra={
            "gate_run_id": str(row.id),
            "change_id": row.change_id,
            "status": row.status,
            "manifest_checksum_sha256": row.manifest_checksum_sha256,
        },
    )
    return row


async def ensure_golden_datasets(
    db: AsyncSession,
    *,
    created_by: Optional[uuid.UUID] = None,
) -> list[str]:
    """Create any golden reference dataset that is missing. Idempotent.

    The gate cannot evaluate anything without a dataset -- with none present it
    returns ``FAIL: no evaluation dataset found``, which reads as "quality
    regressed" when it actually means "nothing was measured". On a fresh
    deployment that is the permanent state: seeding existed only behind an
    ADMIN API route that nobody calls, so ``ai_eval_datasets`` sat empty and
    every gate was unrunnable.

    ``created_by`` is optional because a scheduled run has no user. The column
    is nullable for exactly that case.

    Returns the names created (empty when everything already exists), so a
    caller can tell "seeded now" from "already there" instead of guessing.
    """
    from app.services.golden_datasets import get_all_golden_datasets

    created: list[str] = []
    for spec in get_all_golden_datasets():
        existing = await db.execute(
            select(AIEvalDataset).where(
                AIEvalDataset.name == spec["name"],
                AIEvalDataset.task_type == spec["task_type"],
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(AIEvalDataset(
            name=spec["name"],
            description=spec["description"],
            task_type=spec["task_type"],
            items=spec["items"],
            item_count=spec["item_count"],
            created_by=created_by,
        ))
        created.append(str(spec["name"]))
    if created:
        await db.flush()
    return created


class CandidateRunRefused(Exception):
    """A candidate replay run that cannot be gated or made a baseline (W16 AGT-09).

    Carries the HTTP status, the stable code and the one fixed sentence; the
    router turns it into ``detail = {"code", "message"}``. Not a ``ValueError``:
    the baseline route maps ``ValueError`` to its legacy 404 string.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def _metric_kind_of(row: Any) -> str:
    """A row's metric kind; an unflushed or pre-0231 row reads as the legacy kind."""
    return str(getattr(row, "metric_kind", None) or "label_agreement")


async def _load_gateable_run(
    db: AsyncSession, *, candidate_run_id: uuid.UUID, task_type: str,
) -> AIEvalRun:
    """The completed replay run ``candidate_run_id`` of ``task_type``, or a refusal."""
    run = (
        await db.execute(select(AIEvalRun).where(AIEvalRun.id == candidate_run_id))
    ).scalar_one_or_none()
    if run is None:
        raise CandidateRunRefused(404, "candidate_run_not_found", "Candidate run not found.")
    run_status = str(getattr(run, "status", None) or "completed")
    if _metric_kind_of(run) != "replay" or run_status != "completed":
        shown = run_status if _metric_kind_of(run) == "replay" else "a legacy label-agreement run"
        raise CandidateRunRefused(
            409,
            "candidate_run_not_complete",
            f"Only a completed replay run can be gated; this one is {shown}.",
        )
    if run.task_type != task_type:
        raise CandidateRunRefused(
            422,
            "candidate_run_task_mismatch",
            f"The candidate run evaluated {run.task_type}, not {task_type}.",
        )
    return run


def _replay_run_metrics(run: AIEvalRun) -> dict[str, Any]:
    """The STORED scores of a replay run, in the gate's ``current`` shape.

    ``total`` is the SCORED item count (correct + incorrect): unscored and
    error items carry no verdict, so they never count towards sufficiency.
    """
    return {
        "accuracy": run.accuracy,
        "precision": run.precision,
        "recall": run.recall,
        "f1_score": run.f1_score,
        "total": int(getattr(run, "scored_items", None) or 0),
        "correct": int(run.correct_items or 0),
        "unscored_items": int(getattr(run, "unscored_items", None) or 0),
        "error_items": int(getattr(run, "error_items", None) or 0),
        "metrics": list(getattr(run, "metrics", None) or []),
    }


def _baseline_run_id(baseline: Optional[AIEvalBaseline]) -> Optional[str]:
    run_id = getattr(baseline, "eval_run_id", None) if baseline is not None else None
    return str(run_id) if run_id else None


def _quiet_dataset_id(dataset_id: Optional[str]) -> Optional[str]:
    """The requested dataset id as text, or None (``_coerce_dataset_uuid`` logs; this does not)."""
    if not dataset_id:
        return None
    try:
        return str(uuid.UUID(str(dataset_id)))
    except (ValueError, TypeError, AttributeError):
        return None


async def evaluate_pre_release_gate(
    db: AsyncSession,
    *,
    task_type: str,
    agent_name: str,
    dataset_id: Optional[str] = None,
    gate_manifest_checksum: str | None = None,
    evaluated_at: datetime | None = None,
    candidate_run_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """
    Run the pre-release evaluation gate for an agent.

    Steps:
      1. Load the active baseline for this task_type + agent_name
      2. Load the dataset (specified or golden)
      3. Compute metrics
      4. Compare against baseline thresholds
      5. Return structured gate result

    Returns:
        {
            "status": "pass" | "fail" | "insufficient_samples",
            "task_type": str,
            "agent_name": str,
            "current_metrics": {...},
            "baseline_metrics": {...} | None,
            "rule_results": [{"rule": str, "passed": bool, "detail": str}],
            "evaluated_at": str,
            "metric_kind": "label_agreement" | "replay",
            "candidate_run_id": str | None,
            "baseline_run_id": str | None,
            "dataset_id": str | None,
        }

    ``candidate_run_id`` (W16 AGT-09) gates a completed REPLAY run on its
    stored scores instead of the legacy label-agreement metric. Without it the
    answer is today's, plus ``metric_kind="label_agreement"`` and the three ids.
    """
    if candidate_run_id is not None:
        return await _evaluate_candidate_run_gate(
            db, task_type=task_type, agent_name=agent_name, candidate_run_id=candidate_run_id,
        )

    # 1. Load baseline
    baseline = await _load_active_baseline(db, task_type, agent_name)
    legacy_ids = {
        "metric_kind": "label_agreement",
        "candidate_run_id": None,
        "baseline_run_id": _baseline_run_id(baseline),
        "dataset_id": _quiet_dataset_id(dataset_id),
    }

    # 2. Load dataset
    items = await _load_dataset_items(
        db,
        task_type,
        dataset_id,
        gate_manifest_checksum=gate_manifest_checksum,
        evaluated_at=evaluated_at,
    )
    if not items:
        return {
            "status": EvalVerdict.INSUFFICIENT_SAMPLES,
            "task_type": task_type,
            "agent_name": agent_name,
            "current_metrics": None,
            "baseline_metrics": None,
            "rule_results": [{"rule": "dataset_exists", "passed": False, "detail": "No evaluation dataset found"}],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            **legacy_ids,
        }

    # 3. Compute current metrics
    current = compute_metrics_for_task_type(task_type, items)

    # 4. Compare against baseline and thresholds
    rule_results = _evaluate_rules(current, baseline)

    # 5. Determine overall status
    failures = [r for r in rule_results if not r["passed"]]
    if not baseline:
        status = EvalVerdict.INSUFFICIENT_SAMPLES
    elif failures:
        status = GateStatus.FAIL
    else:
        status = EvalVerdict.PASS

    baseline_metrics = None
    if baseline:
        baseline_metrics = {
            "accuracy": baseline.baseline_accuracy,
            "precision": baseline.baseline_precision,
            "recall": baseline.baseline_recall,
            "f1_score": baseline.baseline_f1,
            "min_accuracy": baseline.min_accuracy,
            "min_f1": baseline.min_f1,
            "max_regression_pct": baseline.max_regression_pct,
            "prompt_version": baseline.prompt_version,
            "model_name": baseline.model_name,
        }

    return {
        "status": status,
        "task_type": task_type,
        "agent_name": agent_name,
        "current_metrics": current,
        "baseline_metrics": baseline_metrics,
        "rule_results": rule_results,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        **legacy_ids,
    }


async def _evaluate_candidate_run_gate(
    db: AsyncSession,
    *,
    task_type: str,
    agent_name: str,
    candidate_run_id: uuid.UUID,
) -> dict[str, Any]:
    """Gate a completed replay run on its STORED scores (W16 AGT-09).

    The existing ``_evaluate_rules`` decide against the active baseline. A
    baseline that cannot be compared with a replay -- measured with the legacy
    label-agreement metric, or on another dataset -- is treated like no
    baseline (default thresholds, ``insufficient_samples``) with the failed
    rule saying why: a replay is never judged against a number of another kind.
    """
    run = await _load_gateable_run(db, candidate_run_id=candidate_run_id, task_type=task_type)
    baseline = await _load_active_baseline(db, task_type, agent_name)
    current = _replay_run_metrics(run)

    unusable: Optional[dict[str, Any]] = None
    if baseline is not None and _metric_kind_of(baseline) != "replay":
        unusable = {
            "rule": "baseline_metric_kind",
            "passed": False,
            "detail": (
                "The active baseline uses the legacy label-agreement metric; "
                "set a replay run as the baseline first."
            ),
        }
    elif (
        baseline is not None
        and getattr(baseline, "dataset_id", None) is not None
        and baseline.dataset_id != run.dataset_id
    ):
        unusable = {
            "rule": "baseline_dataset",
            "passed": False,
            "detail": "The baseline was measured on another dataset.",
        }

    if unusable is not None:
        rule_results = [unusable, *_evaluate_rules(current, None)]
        status = EvalVerdict.INSUFFICIENT_SAMPLES
    else:
        rule_results = _evaluate_rules(current, baseline)
        failures = [r for r in rule_results if not r["passed"]]
        if not baseline:
            status = EvalVerdict.INSUFFICIENT_SAMPLES
        elif failures:
            status = GateStatus.FAIL
        else:
            status = EvalVerdict.PASS

    baseline_metrics = None
    if baseline:
        baseline_metrics = {
            "accuracy": baseline.baseline_accuracy,
            "precision": baseline.baseline_precision,
            "recall": baseline.baseline_recall,
            "f1_score": baseline.baseline_f1,
            "min_accuracy": baseline.min_accuracy,
            "min_f1": baseline.min_f1,
            "max_regression_pct": baseline.max_regression_pct,
            "prompt_version": baseline.prompt_version,
            "model_name": baseline.model_name,
            "metric_kind": _metric_kind_of(baseline),
            "eval_run_id": _baseline_run_id(baseline),
            "dataset_id": str(baseline.dataset_id) if baseline.dataset_id else None,
        }

    return {
        "status": status,
        "task_type": task_type,
        "agent_name": agent_name,
        "current_metrics": current,
        "baseline_metrics": baseline_metrics,
        "rule_results": rule_results,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "metric_kind": "replay",
        "candidate_run_id": str(run.id),
        "baseline_run_id": _baseline_run_id(baseline),
        "dataset_id": str(run.dataset_id) if run.dataset_id else None,
    }


async def persist_nightly_eval_runs(
    db: AsyncSession,
    *,
    gate_results: list[dict[str, Any]],
    model_name: str,
) -> list[AIEvalRun]:
    """Write one ``AIEvalRun`` per evaluated task type for drift analysis."""
    rows: list[AIEvalRun] = []
    for result in gate_results:
        task_type = str(result.get("task_type") or "")
        dataset_result = await db.execute(
            select(AIEvalDataset).where(
                AIEvalDataset.task_type == task_type,
                AIEvalDataset.is_active.is_(True),
            ).order_by(AIEvalDataset.updated_at.desc()).limit(1)
        )
        dataset = dataset_result.scalar_one_or_none()
        if dataset is None:
            continue
        metrics = result.get("current_metrics") or {}
        total = int(metrics.get("total") or 0)
        correct = int(metrics.get("correct") or 0)
        row = AIEvalRun(
            dataset_id=dataset.id,
            model_name=model_name,
            task_type=task_type,
            precision=metrics.get("precision"),
            recall=metrics.get("recall"),
            f1_score=metrics.get("f1_score"),
            accuracy=metrics.get("accuracy"),
            agreement_rate=(correct / total) if total else None,
            total_items=total,
            correct_items=correct,
            fallback_used=False,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def set_baseline_from_eval(
    db: AsyncSession,
    *,
    task_type: str,
    agent_name: str,
    prompt_version: str = "v1",
    model_name: Optional[str] = None,
    dataset_id: Optional[str] = None,
    min_accuracy: float = 0.80,
    min_f1: float = 0.75,
    max_regression_pct: float = 5.0,
    created_by: Optional[Any] = None,
    eval_run_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """
    Compute metrics from a dataset and set as the active baseline.

    Deactivates any existing baseline for this task_type + agent_name.

    ``eval_run_id`` (W16 AGT-09) makes a completed REPLAY run the baseline:
    its stored scores are the metrics, ``prompt_version`` is
    ``replay-<tier>`` (one replay baseline per tier on the unique key), the
    model and dataset come from the run. Refusals raise ``CandidateRunRefused``.
    """
    baseline_dataset: Optional[uuid.UUID]
    baseline_run: Optional[uuid.UUID]
    if eval_run_id is not None:
        run = await _load_gateable_run(db, candidate_run_id=eval_run_id, task_type=task_type)
        stored = getattr(run, "candidate", None)
        candidate: dict[str, Any] = stored if isinstance(stored, dict) else {}
        tier = str(candidate.get("tier") or "unknown")
        run_metrics = _replay_run_metrics(run)
        metrics = {k: v for k, v in run_metrics.items() if k != "metrics"}
        prompt_version = f"replay-{tier}"[:50]
        model_name = candidate.get("model")
        metric_kind = "replay"
        baseline_dataset = run.dataset_id
        baseline_run = run.id
    else:
        items = await _load_dataset_items(db, task_type, dataset_id)
        if not items:
            raise ValueError(f"No dataset found for task_type={task_type}")

        metrics = compute_metrics_for_task_type(task_type, items)
        metric_kind = "label_agreement"
        baseline_dataset = _coerce_dataset_uuid(dataset_id)
        baseline_run = None

    # Upsert on the unique key. ``AIEvalBaseline`` has
    # UniqueConstraint(task_type, agent_name, prompt_version) which applies
    # regardless of ``is_active`` — so a deactivate-then-insert at the same
    # prompt_version (``"v1"`` default) would violate the constraint and 500
    # the re-baseline. Find the row for this exact key and refresh it in
    # place; deactivate only the *other* active baselines (different
    # prompt_versions) for this task_type+agent_name.
    existing = await db.execute(
        select(AIEvalBaseline).where(
            AIEvalBaseline.task_type == task_type,
            AIEvalBaseline.agent_name == agent_name,
        )
    )
    baseline: Optional[AIEvalBaseline] = None
    for b in existing.scalars().all():
        if b.prompt_version == prompt_version:
            baseline = b  # refresh this one in place
        elif b.is_active:
            b.is_active = False
            db.add(b)

    if baseline is None:
        baseline = AIEvalBaseline(
            task_type=task_type,
            agent_name=agent_name,
            prompt_version=prompt_version,
        )
        db.add(baseline)

    baseline.model_name = model_name
    baseline.baseline_accuracy = metrics.get("accuracy")
    baseline.baseline_precision = metrics.get("precision")
    baseline.baseline_recall = metrics.get("recall")
    baseline.baseline_f1 = metrics.get("f1_score")
    baseline.min_accuracy = min_accuracy
    baseline.min_f1 = min_f1
    baseline.max_regression_pct = max_regression_pct
    baseline.dataset_id = baseline_dataset
    # A row refreshed in place takes the kind of what refreshed it (a legacy
    # body on a ``replay-*`` key, or the reverse, never keeps a stale kind).
    baseline.metric_kind = metric_kind
    baseline.eval_run_id = baseline_run
    baseline.created_by = created_by
    baseline.is_active = True

    # Service receives an injected session — caller commits. Flush so the
    # response can read ``baseline.id`` immediately.
    await db.flush()

    return {
        "baseline_id": str(baseline.id),
        "task_type": task_type,
        "agent_name": agent_name,
        "metrics": metrics,
        "thresholds": {
            "min_accuracy": min_accuracy,
            "min_f1": min_f1,
            "max_regression_pct": max_regression_pct,
        },
        "metric_kind": metric_kind,
        "eval_run_id": str(baseline_run) if baseline_run else None,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _load_active_baseline(
    db: AsyncSession,
    task_type: str,
    agent_name: str,
) -> Optional[AIEvalBaseline]:
    """Load the active baseline for a task_type + agent_name."""
    result = await db.execute(
        select(AIEvalBaseline).where(
            AIEvalBaseline.task_type == task_type,
            AIEvalBaseline.agent_name == agent_name,
            AIEvalBaseline.is_active.is_(True),
        ).order_by(AIEvalBaseline.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _load_dataset_items(
    db: AsyncSession,
    task_type: str,
    dataset_id: Optional[str] = None,
    *,
    gate_manifest_checksum: str | None = None,
    evaluated_at: datetime | None = None,
) -> list[dict]:
    """Load dataset items — from specific dataset or golden defaults."""
    dataset_uuid = _coerce_dataset_uuid(dataset_id)
    if dataset_uuid is not None:
        result = await db.execute(
            select(AIEvalDataset).where(AIEvalDataset.id == dataset_uuid)
        )
        dataset = result.scalar_one_or_none()
        if dataset:
            items = dataset.items or []
            if gate_manifest_checksum is not None:
                return gate_eligible_items(
                    items,
                    gate_manifest_checksum=gate_manifest_checksum,
                    evaluated_at=evaluated_at,
                )
            return items

    # Fallback to golden dataset
    from app.services.golden_datasets import GOLDEN_DATASETS
    spec = GOLDEN_DATASETS.get(task_type)
    if spec:
        return spec["get_items"]()

    return []


def _evaluate_rules(
    current: dict,
    baseline: Optional[AIEvalBaseline],
) -> list[dict]:
    """Evaluate threshold and regression rules."""
    rules: list[dict] = []

    accuracy = current.get("accuracy")
    f1 = current.get("f1_score")

    if baseline:
        # Rule 1: Minimum accuracy threshold
        min_acc = baseline.min_accuracy
        if accuracy is not None:
            passed = accuracy >= min_acc
            rules.append({
                "rule": "min_accuracy",
                "passed": passed,
                "detail": f"Accuracy {accuracy:.4f} {'>='.rjust(2)} {min_acc:.2f} threshold" if passed
                else f"Accuracy {accuracy:.4f} below {min_acc:.2f} threshold",
            })

        # Rule 2: Minimum F1 threshold
        min_f1 = baseline.min_f1
        if f1 is not None:
            passed = f1 >= min_f1
            rules.append({
                "rule": "min_f1",
                "passed": passed,
                "detail": f"F1 {f1:.4f} {'>='.rjust(2)} {min_f1:.2f} threshold" if passed
                else f"F1 {f1:.4f} below {min_f1:.2f} threshold",
            })

        # Rule 3: No regression from baseline
        if accuracy is not None and baseline.baseline_accuracy is not None:
            drop_pct = (baseline.baseline_accuracy - accuracy) * 100
            max_drop = baseline.max_regression_pct
            passed = drop_pct <= max_drop
            rules.append({
                "rule": "no_regression",
                "passed": passed,
                "detail": f"Accuracy drop {drop_pct:.1f}% within {max_drop:.1f}% tolerance" if passed
                else f"Accuracy dropped {drop_pct:.1f}% (max allowed: {max_drop:.1f}%)",
                "severity": "warn" if 0 < drop_pct <= max_drop else None,
            })
    else:
        # No baseline — apply default thresholds
        if accuracy is not None:
            passed = accuracy >= 0.70
            rules.append({
                "rule": "default_min_accuracy",
                "passed": passed,
                "detail": f"Accuracy {accuracy:.4f} (no baseline — using 0.70 default)",
            })

    # Rule 4: Dataset has enough items
    total = current.get("total", 0)
    rules.append({
        "rule": "sufficient_data",
        "passed": total >= 3,
        "detail": f"Dataset has {total} items" + ("" if total >= 3 else " (minimum 3 required)"),
    })

    return rules


def evaluate_agent_report_quality_rules(report: dict) -> list[dict]:
    """Turn an ``AgentEvalReport`` dict into gate rule results (AIQ-P5).

    Pure, DB-free, never-raising. Emits one ``{"rule", "passed", "detail"}`` row
    per scored metric (coherence/completeness/actionability/accuracy/brier/ece)
    using the report's ``per_metric_pass`` map, plus an overall
    ``agent_report_quality`` rule mirroring the report's ``passed``. Matches the
    ``rule_results`` shape produced by ``_evaluate_rules``.
    """
    report = report if isinstance(report, dict) else {}
    per_metric = report.get("per_metric_pass")
    per_metric = per_metric if isinstance(per_metric, dict) else {}

    rules: list[dict] = []
    for metric in ("coherence", "completeness", "actionability", "accuracy", "brier", "ece"):
        passed = bool(per_metric.get(metric, False))
        value = report.get(metric)
        rules.append({
            "rule": f"agent_{metric}",
            "passed": passed,
            "detail": f"{metric}={value} ({'pass' if passed else 'fail'})",
        })

    sample_count = report.get("sample_count", 0)
    overall_passed = bool(report.get("passed", False))
    rules.append({
        "rule": "agent_report_quality",
        "passed": overall_passed,
        "detail": (
            f"agent report {'passed' if overall_passed else 'failed'} "
            f"on {sample_count} sample(s)"
        ),
    })
    return rules
