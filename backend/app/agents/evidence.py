"""
Structured evidence + confidence scoring (AIQ-P3).

This module is ADDITIVE and pure-local. It provides a small ``EvidenceRef``
Pydantic v2 model that agents emit alongside their output, plus an
``aggregate_confidence`` helper that turns a list of refs into a single,
capped confidence score and an auditable breakdown.

Like the rest of the AIQ helpers it NEVER performs outbound calls, NEVER
writes to a database, NEVER mutates its inputs, and — by hard invariant —
NEVER raises on any input shape. All coercion lives in
``field_validator(mode="before")`` so a malformed field degrades to its
default rather than crashing. Excerpts are redacted at the model boundary
and truncated so no raw secret/PII text is ever carried in a contract.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.redaction_service import redact_text

# Weighting for strength tiers (drives the weighted-mean aggregate).
_STRENGTH_WEIGHTS = {"weak": 1, "medium": 2, "strong": 3}
_VALID_STRENGTHS = frozenset(_STRENGTH_WEIGHTS)

# Confidence above which a "supporting strong/medium evidence" bar applies.
_CAP_THRESHOLD = 70
# Max excerpt length carried in a contract (chars), before the ellipsis.
_EXCERPT_MAX = 240


class EvidenceRef(BaseModel):
    """A single, redaction-safe piece of evidence backing an agent decision."""

    model_config = ConfigDict(extra="ignore")

    source: str = ""
    ref_id: str = ""
    excerpt: str = ""
    strength: Literal["weak", "medium", "strong"] = "weak"
    contribution: int = 0

    @field_validator("source", "ref_id", mode="before")
    @classmethod
    def _coerce_str(cls, value) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("strength", mode="before")
    @classmethod
    def _coerce_strength(cls, value) -> str:
        try:
            lowered = str(value).lower()
        except Exception:
            return "weak"
        return lowered if lowered in _VALID_STRENGTHS else "weak"

    @field_validator("contribution", mode="before")
    @classmethod
    def _clamp_contribution(cls, value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, number))

    @field_validator("excerpt", mode="before")
    @classmethod
    def _redact_excerpt(cls, value) -> str:
        if value is None:
            return ""
        scrubbed = redact_text(str(value))
        if len(scrubbed) > _EXCERPT_MAX:
            return scrubbed[:_EXCERPT_MAX] + "…"
        return scrubbed

    def as_legacy_dict(self) -> dict:
        """Render as a plain JSON-safe dict for legacy ``evidence_refs`` lists."""
        return self.model_dump(mode="json")


def _zero_breakdown() -> dict:
    return {
        "raw_confidence": 0,
        "final_confidence": 0,
        "cap_applied": False,
        "cap_reason": "",
        "strength_tally": {"weak": 0, "medium": 0, "strong": 0},
        "evidence_count": 0,
        "per_source": [],
    }


def aggregate_confidence(refs: list[EvidenceRef]) -> tuple[int, dict]:
    """Aggregate evidence refs into a capped confidence score + breakdown.

    Weighted mean of ``contribution`` (weak=1, medium=2, strong=3), rounded
    and clamped to [0, 100]. A confidence above ``_CAP_THRESHOLD`` requires
    at least one strong OR two medium refs; otherwise it is capped to
    ``_CAP_THRESHOLD``. Empty / all-invalid input yields ``(0, zero-breakdown)``.
    Filters out non-``EvidenceRef`` items and never raises.
    """
    valid = [r for r in (refs or []) if isinstance(r, EvidenceRef)]
    if not valid:
        return 0, _zero_breakdown()

    tally = {"weak": 0, "medium": 0, "strong": 0}
    weighted_sum = 0
    total_weight = 0
    per_source: list[dict] = []
    for ref in valid:
        weight = _STRENGTH_WEIGHTS.get(ref.strength, 1)
        tally[ref.strength] = tally.get(ref.strength, 0) + 1
        weighted_sum += ref.contribution * weight
        total_weight += weight
        per_source.append({
            "source": ref.source,
            "ref_id": ref.ref_id,
            "strength": ref.strength,
            "contribution": ref.contribution,
        })

    raw = round(weighted_sum / total_weight) if total_weight else 0
    raw = max(0, min(100, raw))

    cap_applied = False
    cap_reason = ""
    final = raw
    has_sufficient_strength = tally["strong"] >= 1 or tally["medium"] >= 2
    if raw > _CAP_THRESHOLD and not has_sufficient_strength:
        final = _CAP_THRESHOLD
        cap_applied = True
        cap_reason = (
            f"confidence>{_CAP_THRESHOLD} requires >=1 strong or >=2 medium "
            "evidence sources"
        )

    breakdown = {
        "raw_confidence": raw,
        "final_confidence": final,
        "cap_applied": cap_applied,
        "cap_reason": cap_reason,
        "strength_tally": tally,
        "evidence_count": len(valid),
        "per_source": per_source,
    }
    return final, breakdown
