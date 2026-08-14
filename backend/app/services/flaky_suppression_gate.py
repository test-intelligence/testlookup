"""Whether a flaky verdict may act, or may only advise.

Phase 2 (P2-A) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## The measurement this exists to respect

The product classifies failures partly by matching error signatures. Published
measurement of that exact technique (ICST 2024 — 230,439 failures across 22
projects) found specificity ranging from **100% on some projects to no better
than random on others**, driven by whether a project's failures carry
distinctive exception types. A project drowning in bare ``AssertionError``
cannot be classified the way one throwing ``UnknownHostException`` can.

So "is our classifier trustworthy here?" has no global answer. Phase 0 measured
it per project; this module is what consumes that measurement.

## Decision D2: advisory always

The gate has two levers — whether a verdict may be *shown*, and whether it may
*act* (suppress a failure, auto-close, hide from a gate). **Acting is disabled
unconditionally**, not merely when specificity is low.

That is a product decision recorded in the plan, and it rests on evidence:
Google found that when a previously stable test turned flaky, roughly **1 in 6
times the cause was a real production bug**. Suppression is the one
irreversible mistake available here — a wrongly-shown verdict wastes a minute,
a wrongly-suppressed failure ships the bug. The asymmetry is not close.

``ALLOW_SUPPRESSION`` is therefore a module constant pinned by a test, not a
setting. Flipping it is a deliberate, reviewable act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger("services.flaky_suppression_gate")

# Decision D2. Nothing in the product may auto-suppress a failure on a flaky
# verdict. See the module docstring for why this is a constant and not config.
ALLOW_SUPPRESSION = False

# Below this measured specificity the classifier is too weak on this project for
# its verdicts to be presented as conclusions; they are shown as hints instead.
# 0.9 is a deliberate, arguable line — published in the payload so it can be
# argued with rather than discovered.
ADVISORY_SPECIFICITY_FLOOR = 0.90

# Presentation modes, weakest first.
MODE_HINT = "hint"            # weak or unmeasured classifier — suggest only
MODE_ADVISORY = "advisory"    # strong classifier — state the verdict, never act
MODE_ENFORCING = "enforcing"  # would allow acting; unreachable while D2 holds


@dataclass(frozen=True)
class SuppressionDecision:
    """How much authority a flaky verdict carries on this project."""

    project_id: Any
    mode: str
    may_suppress: bool
    specificity: Optional[float]
    sample_count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "mode": self.mode,
            "may_suppress": self.may_suppress,
            "measured_specificity": self.specificity,
            "sample_count": self.sample_count,
            "reason": self.reason,
            "advisory_specificity_floor": ADVISORY_SPECIFICITY_FLOOR,
            # Stated in the payload so a consumer cannot mistake "advisory" for
            # "will be enforced once we trust it more".
            "policy": (
                "Flaky verdicts never auto-suppress a failure. A previously "
                "stable test that turns flaky reflects a real bug often enough "
                "that suppression is the one irreversible mistake here."
            ),
        }


def decide(
    project_id: Any,
    *,
    specificity: Optional[float],
    sample_count: int = 0,
) -> SuppressionDecision:
    """Decide how much authority a flaky verdict carries for one project.

    ``specificity`` is the measured value from ``flaky_classifier_calibration``,
    or ``None`` when the project has too little history to have been measured.
    Unmeasured is treated exactly like weak — never like strong. An unmeasured
    classifier is an unknown one, and defaulting an unknown to trusted is how a
    silent wrong verdict ships.
    """
    count = max(0, int(sample_count or 0))

    if specificity is None:
        decision_mode = MODE_HINT
        reason = (
            "classifier specificity has not been measured on this project yet "
            "— verdicts are shown as hints until it has"
        )
    elif float(specificity) < ADVISORY_SPECIFICITY_FLOOR:
        decision_mode = MODE_HINT
        reason = (
            f"measured specificity {float(specificity):.2f} is below the "
            f"{ADVISORY_SPECIFICITY_FLOOR:.2f} floor on this project's own "
            "history — its failures may not carry distinctive enough signatures"
        )
    else:
        decision_mode = MODE_ADVISORY
        reason = (
            f"measured specificity {float(specificity):.2f} meets the "
            f"{ADVISORY_SPECIFICITY_FLOOR:.2f} floor — the verdict is stated, "
            "but still never acts on its own"
        )

    return SuppressionDecision(
        project_id=project_id,
        mode=decision_mode,
        # Belt and braces: even MODE_ENFORCING could not turn this True while
        # D2 holds, and the constant is pinned by a test.
        may_suppress=bool(ALLOW_SUPPRESSION) and decision_mode == MODE_ENFORCING,
        specificity=float(specificity) if specificity is not None else None,
        sample_count=count,
        reason=reason,
    )
