"""Keep quarantine a loop, not a landfill.

Phase 5 (P5-B) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

The lifecycle already had the parts everyone builds: an SLA, auto-created
defects, auto-promotion after a pass streak, and quarantined tests that keep
running to collect health signals. The published closed-loop spec agrees with
all of that.

What was missing is everything that stops the pile growing quietly:

1. **A cap with a visible warning.** Quarantine otherwise becomes "delay with
   documentation" — the look-at-it-later pile accumulates and the tests are
   eventually deleted along with the feature they covered. The cap **warns and
   never blocks**: refusing to quarantine a genuinely broken test just pushes
   the noise back into the build.
2. **An unmasking safeguard.** Quarantine "could easily mask a real race
   condition or some other bug in the code being tested". A quarantined test
   whose failure *signature changes* is no longer failing the way it was
   triaged for — that is new information, and it is invisible by construction
   because nobody is looking at quarantined tests.
3. **A visible population trend**, so growth is a number someone sees rather
   than something discovered a year later.

None of this suppresses or force-releases anything. It reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import structlog

logger = structlog.get_logger("services.quarantine_health")

# Fowler's rule of thumb, used only when a project has no policy row yet.
DEFAULT_MAX_ACTIVE = 8

# Severity vocabulary for anything this service reports.
SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_ALERT = "alert"


@dataclass(frozen=True)
class QuarantineWarning:
    """Something a human should look at. Never an action taken on their behalf."""

    kind: str
    severity: str
    message: str
    subject: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class QuarantineHealth:
    """The state of a project's quarantine, and what is worth noticing."""

    project_id: Any
    active_count: int
    max_active: int
    over_cap: bool
    stale_count: int
    warnings: list[QuarantineWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "active_count": self.active_count,
            "max_active": self.max_active,
            "over_cap": self.over_cap,
            "stale_count": self.stale_count,
            "warnings": [w.to_dict() for w in self.warnings],
            # Restated so a cap can never be mistaken for an admission control.
            "policy": (
                "Warnings only. The cap is not enforced — refusing to "
                "quarantine a genuinely broken test would push the noise back "
                "into the build. Nothing here releases or suppresses a test."
            ),
        }


def evaluate_cap(active_count: int, max_active: int) -> Optional[QuarantineWarning]:
    """Warn when the pile is at or over its ceiling. ``0`` means unlimited."""
    limit = max(0, int(max_active or 0))
    count = max(0, int(active_count or 0))
    if limit == 0 or count < limit:
        return None
    severity = SEVERITY_ALERT if count > limit else SEVERITY_WARN
    return QuarantineWarning(
        kind="cap_reached",
        severity=severity,
        message=(
            f"{count} tests are quarantined against a limit of {limit}. "
            "Quarantine is meant to be temporary — a pile this size usually "
            "means tests are being parked rather than fixed."
        ),
    )


def signature_changed(previous: Any, current: Any) -> bool:
    """Has a quarantined test started failing a DIFFERENT way?

    Compared on the normalised error signature, not the raw message, so a
    changed line number or object address is not mistaken for a new fault.
    Missing data on either side means "cannot tell" — reported as no change,
    because a warning nobody can act on is noise.
    """
    from app.services.flaky_signals import error_signature

    if not previous or not current:
        return False
    return error_signature(previous) != error_signature(current)


def evaluate_unmasking(
    test_name: str,
    previous_error: Any,
    current_error: Any,
) -> Optional[QuarantineWarning]:
    """Flag a quarantined test that is now failing a different way.

    This is the safeguard against quarantine masking a real bug. Nobody watches
    quarantined tests by definition, so a fault that appears *after* the
    quarantine decision is invisible unless something looks for it.
    """
    if not signature_changed(previous_error, current_error):
        return None
    return QuarantineWarning(
        kind="signature_changed",
        severity=SEVERITY_ALERT,
        subject=test_name,
        message=(
            f"{test_name} is quarantined but is now failing with a different "
            "error than it was quarantined for. Quarantine can mask a real "
            "bug — this one is no longer the failure it was triaged as."
        ),
    )


def summarize_health(
    project_id: Any,
    *,
    active_count: int,
    max_active: int = DEFAULT_MAX_ACTIVE,
    stale_count: int = 0,
    unmasking: Iterable[QuarantineWarning] = (),
) -> QuarantineHealth:
    """Compose the warnings for one project. Pure; never raises."""
    warnings: list[QuarantineWarning] = []

    cap_warning = evaluate_cap(active_count, max_active)
    if cap_warning is not None:
        warnings.append(cap_warning)

    stale = max(0, int(stale_count or 0))
    if stale:
        warnings.append(QuarantineWarning(
            kind="sla_breached",
            severity=SEVERITY_WARN,
            message=(
                f"{stale} quarantined test(s) are past their resolution "
                "deadline. A deadline nobody surfaces is a deadline nobody meets."
            ),
        ))

    warnings.extend(w for w in (unmasking or ()) if w is not None)

    limit = max(0, int(max_active or 0))
    count = max(0, int(active_count or 0))
    return QuarantineHealth(
        project_id=project_id,
        active_count=count,
        max_active=limit,
        over_cap=bool(limit and count > limit),
        stale_count=stale,
        warnings=warnings,
    )
