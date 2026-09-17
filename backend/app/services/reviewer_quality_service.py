"""G3 reviewer-quality scoring and the guarded second-model retirement rule.

Reviewer changes are evaluated against three independent signals: labelled
mutations, clean outputs, and human outcomes.  The retirement decision is
deliberately stricter than the release gate: every semantic class needs 30
observations in the trailing 30-day window, and family 4 must add less than
0.10 recall over the deterministic families before it may be disabled.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalReviewerQuality, AgentConfig
from app.services.agent_config_service import (
    AgentConfigV1,
    ConfigVersionConflict,
    put_config,
    serialize,
)
from app.services.agent_eval_samples import MutationClass
from app.services.eval_verdict import EvalVerdict

REVIEWER_AGENT_ID = "agent.reviewer.v1"
REVIEW_FAMILIES = frozenset({1, 2, 3, 4, 5})
DETERMINISTIC_FAMILIES = frozenset({1, 2, 5})
MODEL_REVIEW_FAMILIES = frozenset({3, 4})
SEMANTIC_MUTATION_CLASSES = (
    MutationClass.UNSUPPORTED_CAUSAL_CLAIM,
    MutationClass.PLAUSIBLE_WRONG_CATEGORY,
    MutationClass.CORRECT_NUMBERS_WRONG_CONCLUSION,
)
WINDOW_DAYS = 30
GATE_SAMPLE_FLOOR = 20
AUTO_DISABLE_SAMPLE_FLOOR = 30
MIN_REVIEW_RECALL = 0.95
MAX_FALSE_FLAG_RATE = 0.10
MAX_FALSE_OMISSION_RATE = 0.10
MIN_SECOND_MODEL_RECALL_DELTA = 0.10


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return value.astimezone(timezone.utc)


def _families(value: frozenset[int]) -> frozenset[int]:
    unknown = sorted(set(value) - REVIEW_FAMILIES)
    if unknown:
        raise ValueError(f"review families must be in 1..5; got {unknown}")
    return value


class ReviewerMutationObservation(_Strict):
    sample_id: str = Field(min_length=1, max_length=160)
    mutation_class: MutationClass
    detected_families: frozenset[int] = Field(default_factory=frozenset)
    observed_at: datetime

    _validate_families = field_validator("detected_families")(_families)
    _validate_time = field_validator("observed_at")(_aware)


class ReviewerCleanObservation(_Strict):
    sample_id: str = Field(min_length=1, max_length=160)
    flagged_families: frozenset[int] = Field(default_factory=frozenset)
    observed_at: datetime

    _validate_families = field_validator("flagged_families")(_families)
    _validate_time = field_validator("observed_at")(_aware)


class ReviewerHumanOutcome(_Strict):
    review_id: str = Field(min_length=1, max_length=160)
    reviewer_passed: bool
    human_rejected: bool
    observed_at: datetime

    _validate_time = field_validator("observed_at")(_aware)


def _wilson(successes: int, n: int) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    z = 1.959963984540054
    rate = successes / n
    denominator = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / denominator
    margin = z * ((rate * (1 - rate) / n + z * z / (4 * n * n)) ** 0.5) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _metric(name: str, successes: int, n: int, *, kind: str) -> dict[str, Any]:
    low, high = _wilson(successes, n)
    return {
        "name": name,
        "value": successes / n if n else None,
        "n": n,
        "ci_low": low,
        "ci_high": high,
        "kind": kind,
    }


def _deduplicated(items: Sequence[Any], key: str) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        value = str(getattr(item, key))
        if value in seen:
            raise ValueError(f"duplicate reviewer-quality observation: {value}")
        seen.add(value)
        result.append(item)
    return result


def evaluate_reviewer_quality(
    *,
    mutations: Sequence[ReviewerMutationObservation],
    clean: Sequence[ReviewerCleanObservation],
    human_outcomes: Sequence[ReviewerHumanOutcome],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Evaluate G3 and return one typed-vocabulary gate result."""
    ended_at = _aware(now or datetime.now(timezone.utc))
    started_at = ended_at - timedelta(days=WINDOW_DAYS)
    mutations = [
        item for item in _deduplicated(mutations, "sample_id")
        if started_at <= item.observed_at <= ended_at
    ]
    clean = [
        item for item in _deduplicated(clean, "sample_id")
        if started_at <= item.observed_at <= ended_at
    ]
    human_outcomes = [
        item for item in _deduplicated(human_outcomes, "review_id")
        if started_at <= item.observed_at <= ended_at
    ]

    per_class_family: list[dict[str, Any]] = []
    semantic_counts: dict[str, int] = {}
    semantic_model_recall: dict[str, float | None] = {}
    for mutation_class in MutationClass:
        class_rows = [item for item in mutations if item.mutation_class == mutation_class]
        if mutation_class in SEMANTIC_MUTATION_CLASSES:
            semantic_counts[mutation_class.value] = len(class_rows)
        for family in sorted(REVIEW_FAMILIES):
            detected = sum(family in item.detected_families for item in class_rows)
            per_class_family.append({
                "mutation_class": mutation_class.value,
                "family": family,
                **_metric("recall", detected, len(class_rows), kind="ground_truth"),
            })
        if mutation_class in SEMANTIC_MUTATION_CLASSES:
            detected = sum(bool(item.detected_families & MODEL_REVIEW_FAMILIES) for item in class_rows)
            semantic_model_recall[mutation_class.value] = (
                detected / len(class_rows) if class_rows else None
            )

    semantic_rows = [
        item for item in mutations if item.mutation_class in SEMANTIC_MUTATION_CLASSES
    ]
    deterministic_detected = sum(
        bool(item.detected_families & DETERMINISTIC_FAMILIES) for item in semantic_rows
    )
    second_model_detected = sum(4 in item.detected_families for item in semantic_rows)
    semantic_n = len(semantic_rows)
    deterministic_recall = deterministic_detected / semantic_n if semantic_n else None
    second_model_recall = second_model_detected / semantic_n if semantic_n else None
    recall_delta = (
        second_model_recall - deterministic_recall
        if second_model_recall is not None and deterministic_recall is not None
        else None
    )

    false_flags = sum(bool(item.flagged_families) for item in clean)
    reviewer_passes = [item for item in human_outcomes if item.reviewer_passed]
    false_omissions = sum(item.human_rejected for item in reviewer_passes)
    false_flag = _metric("clean_false_flag_rate", false_flags, len(clean), kind="ground_truth")
    false_omission = _metric(
        "human_false_omission_rate",
        false_omissions,
        len(reviewer_passes),
        kind="ground_truth",
    )

    missing = [
        f"{name}:{count}/{GATE_SAMPLE_FLOOR}"
        for name, count in semantic_counts.items()
        if count < GATE_SAMPLE_FLOOR
    ]
    if len(clean) < GATE_SAMPLE_FLOOR:
        missing.append(f"clean:{len(clean)}/{GATE_SAMPLE_FLOOR}")
    if len(reviewer_passes) < GATE_SAMPLE_FLOOR:
        missing.append(f"human_reviewer_pass:{len(reviewer_passes)}/{GATE_SAMPLE_FLOOR}")

    regressions: list[dict[str, Any]] = []
    for name, recall in semantic_model_recall.items():
        if recall is not None and recall < MIN_REVIEW_RECALL:
            regressions.append({
                "metric": f"semantic_recall:{name}",
                "candidate": recall,
                "threshold": MIN_REVIEW_RECALL,
            })
    if false_flag["value"] is not None and false_flag["value"] > MAX_FALSE_FLAG_RATE:
        regressions.append({
            "metric": "clean_false_flag_rate",
            "candidate": false_flag["value"],
            "threshold": MAX_FALSE_FLAG_RATE,
        })
    if (
        false_omission["value"] is not None
        and false_omission["value"] > MAX_FALSE_OMISSION_RATE
    ):
        regressions.append({
            "metric": "human_false_omission_rate",
            "candidate": false_omission["value"],
            "threshold": MAX_FALSE_OMISSION_RATE,
        })

    if missing:
        verdict = EvalVerdict.INSUFFICIENT_SAMPLES
        reason = "missing G3 evidence: " + ", ".join(missing)
    elif regressions:
        verdict = EvalVerdict.FAIL
        reason = "reviewer quality regressed beyond a G3 threshold"
    else:
        verdict = EvalVerdict.PASS
        reason = "reviewer meets the mutation, clean-corpus, and human-outcome thresholds"

    auto_disable_eligible = bool(
        recall_delta is not None
        and all(
            semantic_counts.get(mutation_class.value, 0) >= AUTO_DISABLE_SAMPLE_FLOOR
            for mutation_class in SEMANTIC_MUTATION_CLASSES
        )
        and recall_delta < MIN_SECOND_MODEL_RECALL_DELTA
    )
    manifest = {
        "schema_version": 1,
        "gate": "G3",
        "window_started_at": started_at.isoformat(),
        "window_ended_at": ended_at.isoformat(),
        "mutation_ids": sorted(item.sample_id for item in mutations),
        "clean_ids": sorted(item.sample_id for item in clean),
        "human_review_ids": sorted(item.review_id for item in reviewer_passes),
        "thresholds": {
            "gate_sample_floor": GATE_SAMPLE_FLOOR,
            "minimum_recall": MIN_REVIEW_RECALL,
            "maximum_false_flag_rate": MAX_FALSE_FLAG_RATE,
            "maximum_false_omission_rate": MAX_FALSE_OMISSION_RATE,
            "auto_disable_sample_floor": AUTO_DISABLE_SAMPLE_FLOOR,
            "minimum_second_model_recall_delta": MIN_SECOND_MODEL_RECALL_DELTA,
        },
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    metrics = [
        *per_class_family,
        false_flag,
        false_omission,
        _metric(
            "deterministic_semantic_recall",
            deterministic_detected,
            semantic_n,
            kind="ground_truth",
        ),
        _metric(
            "second_model_semantic_recall",
            second_model_detected,
            semantic_n,
            kind="ground_truth",
        ),
    ]
    return {
        "verdict": verdict.value,
        "status": verdict.value,
        "reason": reason,
        "manifest_checksum": checksum,
        "manifest": {**manifest, "manifest_checksum_sha256": checksum},
        "per_gate": {"G3": metrics},
        "regressions": regressions,
        "per_class_family_recall": per_class_family,
        "semantic_sample_counts": semantic_counts,
        "clean_sample_count": len(clean),
        "human_outcome_count": len(reviewer_passes),
        "false_flag_rate": false_flag["value"],
        "false_omission_rate": false_omission["value"],
        "deterministic_semantic_recall": deterministic_recall,
        "second_model_semantic_recall": second_model_recall,
        "second_model_recall_delta": recall_delta,
        "auto_disable_eligible": auto_disable_eligible,
        "auto_disable_applied": False,
        "window_started_at": started_at,
        "window_ended_at": ended_at,
    }


async def persist_reviewer_quality(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    result: Mapping[str, Any],
    mutations: Sequence[ReviewerMutationObservation],
    clean: Sequence[ReviewerCleanObservation],
    human_outcomes: Sequence[ReviewerHumanOutcome],
    source: Literal["observation_batch", "scheduled"],
    evaluated_by: Optional[uuid.UUID],
) -> AIEvalReviewerQuality:
    row = AIEvalReviewerQuality(
        project_id=project_id,
        agent_id=agent_id,
        source=source,
        status=str(result["verdict"]),
        manifest_checksum_sha256=str(result["manifest_checksum"]),
        window_started_at=result["window_started_at"],
        window_ended_at=result["window_ended_at"],
        mutation_observations=[item.model_dump(mode="json") for item in mutations],
        clean_observations=[item.model_dump(mode="json") for item in clean],
        human_outcomes=[item.model_dump(mode="json") for item in human_outcomes],
        metrics=list(result["per_gate"]["G3"]),
        regressions=list(result["regressions"]),
        semantic_sample_counts=dict(result["semantic_sample_counts"]),
        clean_sample_count=int(result["clean_sample_count"]),
        human_outcome_count=int(result["human_outcome_count"]),
        deterministic_recall=result["deterministic_semantic_recall"],
        second_model_recall=result["second_model_semantic_recall"],
        recall_delta=result["second_model_recall_delta"],
        false_flag_rate=result["false_flag_rate"],
        false_omission_rate=result["false_omission_rate"],
        auto_disable_eligible=bool(result["auto_disable_eligible"]),
        auto_disable_applied=bool(result["auto_disable_applied"]),
        evaluated_by=evaluated_by,
    )
    db.add(row)
    await db.flush()
    return row


async def disable_second_model_if_eligible(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    result: dict[str, Any],
    updated_by: Optional[uuid.UUID],
) -> bool:
    """Use the existing config writer; do nothing unless every G3 guard passed."""
    if not result["auto_disable_eligible"]:
        return False
    config_row = (
        await db.execute(
            select(AgentConfig).where(
                AgentConfig.project_id == project_id,
                AgentConfig.agent_id == agent_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if config_row is None:
        return False
    config = AgentConfigV1.model_validate(serialize(agent_id, config_row)["config"])
    if not config.review.second_model_check:
        return False
    updated = config.model_copy(update={
        "review": config.review.model_copy(update={"second_model_check": False})
    })
    try:
        row = await put_config(
            db,
            project_id,
            updated,
            updated_by=updated_by,
            expected_version=int(config_row.config_version),
        )
    except ConfigVersionConflict:
        return False
    result["auto_disable_applied"] = True
    result["auto_disable_config_version"] = int(row.config_version)
    return True


async def load_reviewer_quality_observations(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    now: Optional[datetime] = None,
) -> tuple[
    list[ReviewerMutationObservation],
    list[ReviewerCleanObservation],
    list[ReviewerHumanOutcome],
]:
    ended_at = _aware(now or datetime.now(timezone.utc))
    started_at = ended_at - timedelta(days=WINDOW_DAYS)
    rows = (
        await db.execute(
            select(AIEvalReviewerQuality).where(
                AIEvalReviewerQuality.project_id == project_id,
                AIEvalReviewerQuality.agent_id == agent_id,
                AIEvalReviewerQuality.source == "observation_batch",
                AIEvalReviewerQuality.evaluated_at >= started_at,
            )
        )
    ).scalars().all()
    mutations = [
        ReviewerMutationObservation.model_validate(item)
        for row in rows for item in (row.mutation_observations or [])
    ]
    clean = [
        ReviewerCleanObservation.model_validate(item)
        for row in rows for item in (row.clean_observations or [])
    ]
    human = [
        ReviewerHumanOutcome.model_validate(item)
        for row in rows for item in (row.human_outcomes or [])
    ]
    mutation_by_id = {item.sample_id: item for item in mutations}
    clean_by_id = {item.sample_id: item for item in clean}
    human_by_id = {item.review_id: item for item in human}
    return list(mutation_by_id.values()), list(clean_by_id.values()), list(human_by_id.values())


async def run_nightly_reviewer_quality(
    db: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> list[AIEvalReviewerQuality]:
    """Roll up each project's trailing G3 evidence and apply eligible retirement."""
    ended_at = _aware(now or datetime.now(timezone.utc))
    started_at = ended_at - timedelta(days=WINDOW_DAYS)
    projects = (
        await db.execute(
            select(AIEvalReviewerQuality.project_id)
            .where(
                AIEvalReviewerQuality.agent_id == REVIEWER_AGENT_ID,
                AIEvalReviewerQuality.source == "observation_batch",
                AIEvalReviewerQuality.evaluated_at >= started_at,
            )
            .distinct()
        )
    ).scalars().all()
    rows: list[AIEvalReviewerQuality] = []
    for project_id in projects:
        mutations, clean, human = await load_reviewer_quality_observations(
            db,
            project_id=project_id,
            agent_id=REVIEWER_AGENT_ID,
            now=ended_at,
        )
        result = evaluate_reviewer_quality(
            mutations=mutations,
            clean=clean,
            human_outcomes=human,
            now=ended_at,
        )
        await disable_second_model_if_eligible(
            db,
            project_id=project_id,
            agent_id=REVIEWER_AGENT_ID,
            result=result,
            updated_by=None,
        )
        row = await persist_reviewer_quality(
            db,
            project_id=project_id,
            agent_id=REVIEWER_AGENT_ID,
            result=result,
            mutations=[],
            clean=[],
            human_outcomes=[],
            source="scheduled",
            evaluated_by=None,
        )
        from app.services.activity.service import ActorRef, record

        actor = ActorRef.system("daily-reviewer-quality")
        await record(
            db,
            project_id=project_id,
            event_type="ai_eval.reviewer_evaluated",
            actor=actor,
            entity_id=project_id,
            entity_label=REVIEWER_AGENT_ID,
            context={
                "verdict": result["verdict"],
                "sample_count": sum(result["semantic_sample_counts"].values()),
                "auto_disable_applied": result["auto_disable_applied"],
                "reviewer_quality_id": str(row.id),
            },
        )
        if result["auto_disable_applied"]:
            await record(
                db,
                project_id=project_id,
                event_type="agent_config.updated",
                actor=actor,
                entity_id=project_id,
                entity_label=REVIEWER_AGENT_ID,
                changed_fields=["review"],
                context={
                    "config_version": result["auto_disable_config_version"],
                    "changed": "review.second_model_check",
                },
            )
        rows.append(row)
    return rows


__all__ = [
    "AUTO_DISABLE_SAMPLE_FLOOR",
    "DETERMINISTIC_FAMILIES",
    "GATE_SAMPLE_FLOOR",
    "MIN_SECOND_MODEL_RECALL_DELTA",
    "REVIEWER_AGENT_ID",
    "SEMANTIC_MUTATION_CLASSES",
    "ReviewerCleanObservation",
    "ReviewerHumanOutcome",
    "ReviewerMutationObservation",
    "disable_second_model_if_eligible",
    "evaluate_reviewer_quality",
    "load_reviewer_quality_observations",
    "persist_reviewer_quality",
    "run_nightly_reviewer_quality",
]
