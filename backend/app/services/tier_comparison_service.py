"""G2 paired model-tier comparison and bounded shadow evidence (E9.3).

The comparison consumes two outputs for each version-controlled capability
sample.  Both outputs are scored against the same label, so the deciding
quantity is a paired accuracy difference rather than two unrelated rates.
Below 100 samples G2 is a smoke gate: validity must be perfect and the Wilson
accuracy intervals must overlap.  At 100 or more samples the lower 95% bound
of the paired difference must be no worse than ``-delta``.

Live shadow outputs are stored separately as labelling candidates.  They do
not become gate evidence merely because a model produced them: only a human
label or a match to a golden input may promote a pair into G2 evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalGateRun, AIEvalShadowPair
from app.services.agent_eval_samples import (
    ANALYSIS_SAMPLE_FLOOR,
    CAPABILITY_EVAL_SAMPLES,
    SMOKE_SAMPLE_FLOOR,
    CapabilityEvalSampleV1,
    LabelKind,
)
from app.services.agent_capability_registry import CAPABILITY_REGISTRY, DEFAULT_TIERS
from app.services.eval_verdict import EvalVerdict

TIER_ORDER: tuple[str, ...] = ("deterministic", "slm", "llm", "auto")
DEFAULT_NON_INFERIORITY_DELTA = 0.05


class TierComparisonRejected(ValueError):
    """A config change has no passing G2 evidence."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        super().__init__(str(self.report.get("reason") or "tier comparison rejected"))


@dataclass(frozen=True)
class TierOutputPair:
    sample_id: str
    incumbent_output: Any
    candidate_output: Any
    incumbent_cost_usd: float = 0.0
    candidate_cost_usd: float = 0.0
    incumbent_latency_ms: int = 0
    candidate_latency_ms: int = 0
    incumbent_tokens: int = 0
    candidate_tokens: int = 0


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _checksum(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _lookup(output: Any, names: Sequence[str]) -> Any:
    if not isinstance(output, Mapping):
        return None
    lowered = {str(key).lower(): value for key, value in output.items()}
    for name in names:
        if name in lowered:
            return lowered[name]
    for value in output.values():
        found = _lookup(value, names)
        if found is not None:
            return found
    return None


def score_output(sample: CapabilityEvalSampleV1, output: Any) -> tuple[bool, bool]:
    """Return ``(correct, valid)`` for one output against its frozen label."""
    label = sample.label
    if label.kind == LabelKind.CLASSIFICATION:
        actual = _lookup(
            output,
            ("value", "classification", "category", "failure_category", "verdict", "risk"),
        )
        valid = actual is not None
        return valid and str(actual).lower() == label.value.lower(), valid
    if label.kind == LabelKind.STRUCTURED:
        valid = isinstance(output, Mapping) and all(field in output for field in label.required_fields)
        correct = valid and all(_lookup(output, (field.lower(),)) == value for field, value in label.exact_values.items())
        return bool(correct), bool(valid)
    text = output if isinstance(output, str) else _canonical(output)
    lowered = text.lower()
    valid = bool(text.strip()) and all(claim.lower() in lowered for claim in label.required_claims)
    correct = valid and not any(claim.lower() in lowered for claim in label.prohibited_claims)
    return bool(correct), bool(valid)


def _wilson(successes: int, n: int) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    z = 1.959963984540054
    rate = successes / n
    denominator = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _paired_interval(differences: Sequence[int]) -> tuple[float, float, float]:
    n = len(differences)
    mean = sum(differences) / n
    if n < 2:
        return mean, mean, mean
    variance = sum((value - mean) ** 2 for value in differences) / (n - 1)
    margin = 1.959963984540054 * math.sqrt(variance / n)
    return mean, max(-1.0, mean - margin), min(1.0, mean + margin)


def compare_tier_outputs(
    *,
    capability: str,
    incumbent_tier: str,
    candidate_tier: str,
    pairs: Sequence[TierOutputPair],
    delta: float = DEFAULT_NON_INFERIORITY_DELTA,
) -> dict[str, Any]:
    """Score a paired corpus and return the shared G2 gate vocabulary."""
    if incumbent_tier not in TIER_ORDER or candidate_tier not in TIER_ORDER:
        raise ValueError(f"tiers must be one of {TIER_ORDER}")
    if not 0 <= delta <= 1:
        raise ValueError("delta must be between 0 and 1")
    corpus = {sample.sample_id: sample for sample in CAPABILITY_EVAL_SAMPLES.get(capability, ())}
    if not corpus:
        raise ValueError(f"capability has no evaluation corpus: {capability}")
    seen: set[str] = set()
    scored: list[dict[str, Any]] = []
    for pair in pairs:
        if pair.sample_id in seen:
            raise ValueError(f"duplicate paired sample: {pair.sample_id}")
        seen.add(pair.sample_id)
        sample = corpus.get(pair.sample_id)
        if sample is None:
            raise ValueError(f"sample {pair.sample_id!r} is not in the {capability!r} corpus")
        incumbent_correct, incumbent_valid = score_output(sample, pair.incumbent_output)
        candidate_correct, candidate_valid = score_output(sample, pair.candidate_output)
        scored.append({
            "sample_id": pair.sample_id,
            "incumbent_correct": incumbent_correct,
            "candidate_correct": candidate_correct,
            "incumbent_valid": incumbent_valid,
            "candidate_valid": candidate_valid,
            "incumbent_cost_usd": max(0.0, float(pair.incumbent_cost_usd)),
            "candidate_cost_usd": max(0.0, float(pair.candidate_cost_usd)),
            "incumbent_latency_ms": max(0, int(pair.incumbent_latency_ms)),
            "candidate_latency_ms": max(0, int(pair.candidate_latency_ms)),
            "incumbent_tokens": max(0, int(pair.incumbent_tokens)),
            "candidate_tokens": max(0, int(pair.candidate_tokens)),
        })

    n = len(scored)
    incumbent_successes = sum(int(row["incumbent_correct"]) for row in scored)
    candidate_successes = sum(int(row["candidate_correct"]) for row in scored)
    incumbent_validity = sum(int(row["incumbent_valid"]) for row in scored)
    candidate_validity = sum(int(row["candidate_valid"]) for row in scored)
    incumbent_ci = _wilson(incumbent_successes, n)
    candidate_ci = _wilson(candidate_successes, n)
    mean_delta, delta_low, delta_high = _paired_interval([
        int(row["candidate_correct"]) - int(row["incumbent_correct"]) for row in scored
    ]) if n else (0.0, 0.0, 0.0)
    floor = ANALYSIS_SAMPLE_FLOOR if capability == "root_cause_analysis" else SMOKE_SAMPLE_FLOOR
    smoke_mode = n < ANALYSIS_SAMPLE_FLOOR
    validity_passed = n > 0 and incumbent_validity == n and candidate_validity == n
    incumbent_low, incumbent_high = incumbent_ci
    candidate_low, candidate_high = candidate_ci
    intervals_overlap = bool(
        n
        and incumbent_low is not None
        and incumbent_high is not None
        and candidate_low is not None
        and candidate_high is not None
        and candidate_high >= incumbent_low
        and incumbent_high >= candidate_low
    )
    if n < floor:
        verdict = EvalVerdict.INSUFFICIENT_SAMPLES
        reason = f"need at least {floor} paired samples; got {n}"
    elif not validity_passed:
        verdict = EvalVerdict.FAIL
        reason = "both tiers must produce contract-valid output for every paired sample"
    elif smoke_mode and not intervals_overlap:
        verdict = EvalVerdict.FAIL
        reason = "candidate and incumbent 95% accuracy intervals do not overlap"
    elif not smoke_mode and delta_low < -delta:
        verdict = EvalVerdict.FAIL
        reason = f"paired accuracy CI lower bound {delta_low:.4f} is below {-delta:.4f}"
    else:
        verdict = EvalVerdict.PASS
        reason = "candidate tier is non-inferior on the paired corpus"

    def average(field: str) -> float:
        return sum(float(row[field]) for row in scored) / n if n else 0.0

    manifest = {
        "schema_version": 1,
        "gate": "G2",
        "capability": capability,
        "incumbent_tier": incumbent_tier,
        "candidate_tier": candidate_tier,
        "sample_ids": sorted(seen),
        "delta": delta,
    }
    checksum = _checksum(manifest)
    metrics = [
        {"name": "incumbent_accuracy", "value": incumbent_successes / n if n else None, "n": n,
         "ci_low": incumbent_ci[0], "ci_high": incumbent_ci[1], "kind": "ground_truth"},
        {"name": "candidate_accuracy", "value": candidate_successes / n if n else None, "n": n,
         "ci_low": candidate_ci[0], "ci_high": candidate_ci[1], "kind": "ground_truth"},
        {"name": "paired_accuracy_delta", "value": mean_delta if n else None, "n": n,
         "ci_low": delta_low if n else None, "ci_high": delta_high if n else None, "kind": "ground_truth"},
        {"name": "candidate_validity", "value": candidate_validity / n if n else None, "n": n,
         "ci_low": None, "ci_high": None, "kind": "validity"},
        {"name": "cost_delta_usd", "value": average("candidate_cost_usd") - average("incumbent_cost_usd"),
         "n": n, "ci_low": None, "ci_high": None, "kind": "cost"},
        {"name": "latency_delta_ms", "value": average("candidate_latency_ms") - average("incumbent_latency_ms"),
         "n": n, "ci_low": None, "ci_high": None, "kind": "cost"},
    ]
    regressions = [] if verdict == EvalVerdict.PASS else [{
        "metric": "paired_accuracy", "baseline": incumbent_successes / n if n else None,
        "candidate": candidate_successes / n if n else None,
        "ci_low": delta_low if n else None, "ci_high": delta_high if n else None,
        "tolerance": delta,
    }]
    return {
        "verdict": verdict.value,
        "status": verdict.value,
        "manifest_checksum": checksum,
        "manifest": {**manifest, "manifest_checksum_sha256": checksum},
        "per_gate": {"G2": metrics},
        "regressions": regressions,
        "smoke_mode": smoke_mode,
        "reason": reason,
        "sample_count": n,
        "scored_pairs": scored,
    }


async def persist_tier_comparison(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    result: Mapping[str, Any],
    evaluated_by: Optional[uuid.UUID],
) -> AIEvalGateRun:
    manifest = dict(result["manifest"])
    row = AIEvalGateRun(
        change_id=f"tier:{project_id}:{agent_id}:{manifest['candidate_tier']}",
        status=str(result["verdict"]),
        manifest_checksum_sha256=str(result["manifest_checksum"]),
        manifest=manifest,
        gate_results=list(result["per_gate"]["G2"]),
        blocking_gates=[] if result["verdict"] == EvalVerdict.PASS.value else [{"gate": "G2"}],
        version_changes=list(result["regressions"]),
        evaluated_by=evaluated_by,
        gate_type="tier_comparison",
        project_id=project_id,
        agent_id=agent_id,
        baseline_tier=str(manifest["incumbent_tier"]),
        candidate_tier=str(manifest["candidate_tier"]),
        sample_count=int(result["sample_count"]),
    )
    db.add(row)
    await db.flush()
    return row


def _quality_tier(config: Any) -> str:
    tier = str(config.model.tier)
    if tier != "auto":
        return tier
    stage = next(
        (
            stage
            for stage, spec in CAPABILITY_REGISTRY.items()
            if spec.capability_id == config.agent_id
        ),
        None,
    )
    if stage is None:
        raise ValueError(f"unknown configured agent: {config.agent_id}")
    return DEFAULT_TIERS[stage]


def is_quality_downgrade(before: Any, after: Any) -> bool:
    quality_order = ("deterministic", "slm", "llm")
    if quality_order.index(_quality_tier(after)) < quality_order.index(_quality_tier(before)):
        return True
    old, new = before.model.escalation, after.model.escalation
    return (
        (old.on_validation_failure and not new.on_validation_failure)
        or new.on_confidence_below < old.on_confidence_below
        or new.max_escalations < old.max_escalations
    )


def _g2_change(before: Any, after: Any) -> bool:
    return bool(before.model.tier != after.model.tier or before.model.escalation != after.model.escalation)


async def enforce_config_tier_gate(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    before: Any,
    after: Any,
) -> None:
    """Refuse failed G2 changes and downgrades without conclusive evidence."""
    if not _g2_change(before, after):
        return
    if is_quality_downgrade(before, after):
        from app.services.online_drift_service import has_active_drift_pin  # noqa: PLC0415

        if await has_active_drift_pin(db, project_id, agent_id):
            raise TierComparisonRejected({
                "verdict": EvalVerdict.FAIL.value,
                "reason": "tier downgrade is pinned until the eval-drift review is closed",
                "agent_id": agent_id,
                "candidate_tier": after.model.tier,
                "gate_run_id": None,
            })
    query = (
        select(AIEvalGateRun)
        .where(
            AIEvalGateRun.gate_type == "tier_comparison",
            AIEvalGateRun.project_id == project_id,
            AIEvalGateRun.agent_id == agent_id,
            AIEvalGateRun.candidate_tier == after.model.tier,
        )
        .order_by(AIEvalGateRun.evaluated_at.desc())
        .limit(1)
    )
    evidence = (await db.execute(query)).scalar_one_or_none()
    if evidence is not None and evidence.status == EvalVerdict.PASS.value:
        return
    verdict = evidence.status if evidence is not None else EvalVerdict.INSUFFICIENT_SAMPLES.value
    if verdict == EvalVerdict.INSUFFICIENT_SAMPLES.value and not is_quality_downgrade(before, after):
        return
    raise TierComparisonRejected({
        "verdict": verdict,
        "reason": (
            "tier downgrade requires a passing G2 comparison"
            if evidence is None else "latest G2 comparison does not permit this config change"
        ),
        "agent_id": agent_id,
        "candidate_tier": after.model.tier,
        "gate_run_id": str(evidence.id) if evidence is not None else None,
    })


def shadow_sample_selected(sample_key: str, sample_rate: float) -> bool:
    """Stable sampling: retries make the same decision and cannot amplify cost."""
    if sample_rate <= 0:
        return False
    if sample_rate >= 1:
        return True
    bucket = int.from_bytes(hashlib.sha256(sample_key.encode("utf-8")).digest()[:8], "big") / 2**64
    return bucket < sample_rate


def enqueue_shadow_pair(
    *,
    config: Any,
    project_id: uuid.UUID,
    agent_id: str,
    sample_key: str,
    incumbent_tier: str,
    candidate_tier: str,
    incumbent_output: Any,
    candidate_output: Any,
    incumbent_tokens: int,
    candidate_tokens: int,
) -> Optional[str]:
    """Send a selected, budget-bounded pair to the low-priority default queue.

    This hook accepts already-produced outputs so it never hides a second model
    call inside the pure ModelRouter.  E5.2/E5.3 call it after their incumbent
    and candidate inference paths are wired.
    """
    if config.mode != "shadow" or not shadow_sample_selected(sample_key, config.shadow.sample_rate):
        return None
    total = max(0, int(incumbent_tokens)) + max(0, int(candidate_tokens))
    if total > config.shadow.daily_token_budget:
        return None
    from app.worker.tasks import persist_ai_eval_shadow_pair

    task = persist_ai_eval_shadow_pair.apply_async(
        kwargs={
            "project_id": str(project_id),
            "agent_id": agent_id,
            "sample_key": sample_key,
            "incumbent_tier": incumbent_tier,
            "candidate_tier": candidate_tier,
            "incumbent_output": incumbent_output,
            "candidate_output": candidate_output,
            "incumbent_tokens": max(0, int(incumbent_tokens)),
            "candidate_tokens": max(0, int(candidate_tokens)),
            "daily_token_budget": int(config.shadow.daily_token_budget),
        },
        queue="default",
    )
    return str(task.id)


async def store_shadow_pair(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: str,
    sample_key: str,
    incumbent_tier: str,
    candidate_tier: str,
    incumbent_output: Any,
    candidate_output: Any,
    incumbent_tokens: int,
    candidate_tokens: int,
    daily_token_budget: int,
) -> Optional[AIEvalShadowPair]:
    """Store one pending label candidate without exceeding today's token cap."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Serialize this project's agent/day budget check inside the transaction.
    # Python's hash is process-randomized, so derive the signed bigint key from
    # the stable project/agent/day identity.
    lock_bytes = hashlib.sha256(
        f"shadow:{project_id}:{agent_id}:{start.date().isoformat()}".encode("utf-8")
    ).digest()[:8]
    lock_key = int.from_bytes(lock_bytes, "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    used = await db.scalar(
        select(func.coalesce(func.sum(AIEvalShadowPair.total_tokens), 0)).where(
            AIEvalShadowPair.project_id == project_id,
            AIEvalShadowPair.agent_id == agent_id,
            AIEvalShadowPair.created_at >= start,
        )
    )
    total_tokens = max(0, incumbent_tokens) + max(0, candidate_tokens)
    if int(used or 0) + total_tokens > max(0, daily_token_budget):
        return None
    row = AIEvalShadowPair(
        project_id=project_id,
        agent_id=agent_id,
        sample_key=sample_key,
        incumbent_tier=incumbent_tier,
        candidate_tier=candidate_tier,
        incumbent_output=incumbent_output,
        candidate_output=candidate_output,
        incumbent_tokens=max(0, incumbent_tokens),
        candidate_tokens=max(0, candidate_tokens),
        total_tokens=total_tokens,
        label_status="pending",
    )
    db.add(row)
    await db.flush()
    return row


__all__ = [
    "DEFAULT_NON_INFERIORITY_DELTA",
    "TierComparisonRejected",
    "TierOutputPair",
    "compare_tier_outputs",
    "enqueue_shadow_pair",
    "enforce_config_tier_gate",
    "persist_tier_comparison",
    "score_output",
    "shadow_sample_selected",
    "store_shadow_pair",
]
