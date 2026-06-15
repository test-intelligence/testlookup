"""FLK-P5 — granular step-level failure attribution (pure, no-DB, never-raise).

Where FLK-P1..P4 reason at the *test* level, this module drills into the
granular ``test_steps`` snapshot to answer "**which step / assertion is the
failure**" — so the recommendation can be a *surgical fix* of one step instead
of quarantining the whole test.

Data-model note: ``test_steps`` is a LATEST-RUN-ONLY snapshot (one per
``canonical_test_cases``; delete+reinsert on each ingest), so cross-run
step-flip history is not retained. FLK-P5 therefore attributes the failure in
the most recent snapshot (the actionable "what to fix now") and fingerprints
*where* it fails. (Cross-run step-flip would require per-run step retention — a
schema change left for future work.) Stack/assertion-trace fingerprinting at the
test level already ships in FLK-P1 (``stack_trace_diversity``) and FLK-P4.

Pure: no DB, no I/O, no outbound calls; never raises. Assertion text is redacted
+ truncated at the boundary so no raw secret/PII rides in a contract.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

from app.services.redaction_service import redact_text

_FAILED = {"FAILED", "BROKEN"}

# Run-specific noise stripped before fingerprinting a step (same shape as the
# FLK-P1 error/stack denoiser) so a stable step keeps a stable fingerprint.
_NOISE = re.compile(
    r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b|\d{4}-\d{2}-\d{2}[T \d:.,+]*|\d+"
)
_SUMMARY_MAX = 200


@dataclass(frozen=True)
class StepFailureAttribution:
    """Surgical attribution of a flaky/failing test to one step."""

    has_failing_step: bool = False
    ordinal: int = -1
    step_name: str = ""
    keyword: str = ""
    is_assertion_failure: bool = False
    assertion_summary: str = ""
    step_fingerprint: str = ""
    total_steps: int = 0
    failing_step_count: int = 0
    surgical_recommendation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_status(value: Any) -> str:
    try:
        text = str(value).upper().strip()
    except Exception:
        return "UNKNOWN"
    return text.rsplit(".", 1)[-1] if "." in text else text


def _clip(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    try:
        text = redact_text(str(value)).strip()
    except Exception:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _assertion_summary(step: Mapping[str, Any]) -> str:
    """Human one-liner for *why* the step failed (expected/actual or message)."""
    expected = _clip(step.get("expected_value"))
    actual = _clip(step.get("actual_value"))
    if expected or actual:
        return f"expected {expected or '∅'}, got {actual or '∅'}"
    msg = _clip(step.get("assertion_message"), _SUMMARY_MAX)
    return msg


def _step_fingerprint(step: Mapping[str, Any]) -> str:
    """Stable SHA over the denoised step name + assertion trace — identifies
    *where* the failure happens so a consistent step reads as deterministic.
    """
    name = str(step.get("name") or "")
    trace = str(step.get("assertion_trace") or step.get("assertion_message") or "")
    head = _NOISE.sub("#", (name + "\n" + trace)[:500]).strip().lower()
    if not head:
        return ""
    return hashlib.sha256(head.encode("utf-8", "replace")).hexdigest()[:16]


def build_step_attribution(
    first_failing_step: Optional[Mapping[str, Any]],
    total_steps: int = 0,
    failing_step_count: int = 0,
) -> StepFailureAttribution:
    """Build the surgical attribution from the first failing step of the latest
    snapshot + the step counts. ``first_failing_step`` is ``None`` when the test
    has no captured steps (or no failing step). Never raises.
    """
    try:
        total = int(total_steps)
    except (TypeError, ValueError):
        total = 0
    try:
        fails = int(failing_step_count)
    except (TypeError, ValueError):
        fails = 0

    if not isinstance(first_failing_step, Mapping):
        return StepFailureAttribution(total_steps=max(0, total), failing_step_count=max(0, fails))

    name = str(first_failing_step.get("name") or "").strip()
    keyword = str(first_failing_step.get("keyword") or "").strip()
    try:
        ordinal = int(first_failing_step.get("ordinal"))
    except (TypeError, ValueError):
        ordinal = -1
    summary = _assertion_summary(first_failing_step)
    is_assertion = bool(
        first_failing_step.get("assertion_message")
        or first_failing_step.get("expected_value")
        or first_failing_step.get("actual_value")
    )
    fingerprint = _step_fingerprint(first_failing_step)

    label = name or (f"step #{ordinal}" if ordinal >= 0 else "the failing step")
    kind = "assertion" if is_assertion else "step"
    scope = f" ({fails} of {total} steps failed)" if total > 0 else ""
    detail = f" — {summary}" if summary else ""
    if total > 1:
        rec = (
            f"Surgical fix: {kind} '{label}'{scope} is the failure point{detail}. "
            "Fix this step rather than quarantining the whole test."
        )
    else:
        rec = f"Failing {kind}: '{label}'{detail}."

    return StepFailureAttribution(
        has_failing_step=True,
        ordinal=ordinal,
        step_name=name,
        keyword=keyword,
        is_assertion_failure=is_assertion,
        assertion_summary=summary,
        step_fingerprint=fingerprint,
        total_steps=max(0, total),
        failing_step_count=max(0, fails),
        surgical_recommendation=rec,
    )
