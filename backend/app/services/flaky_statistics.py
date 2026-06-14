"""FLK-P2 — Wilson score confidence interval for the flaky failure ratio.

Pure, no-DB, never-raise. Gives every flaky verdict a *statistical*-confidence
band on its failure ratio so the leaderboard can distinguish a well-evidenced
flake (e.g. 30/100) from a thin one (3/10) that share the same point estimate.

Closed-form Wilson score interval (no scipy): for ``k`` failures in ``n`` runs
at confidence level ``z`` (default ``z≈1.96`` for two-sided 95 %),

    p̂      = k / n
    center = (p̂ + z²/2n) / (1 + z²/n)
    half   = (z / (1 + z²/n)) · sqrt( p̂(1-p̂)/n + z²/4n² )
    [low, high] = clamp([center - half, center + half], 0, 1)

The Wilson interval is preferred over the normal (Wald) approximation because
it stays inside ``[0, 1]`` and is well-behaved for small ``n`` and extreme ``p̂``
— exactly the regime flaky tests live in. The lower bound is a principled
"statistical strength" ranking key: for a fixed point estimate it grows with the
sample size (30/100 outranks 3/10), so a flake confirmed over many runs sorts
above one inferred from a handful.

This module is pure (no DB, no I/O, no outbound calls) so it is safe under
``AI_OFFLINE_MODE`` by construction and is reused by both the refresh path
(persisting the interval) and the read path (surfacing it).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# z for a two-sided 95 % interval (standard normal quantile). A closed-form
# constant so this module needs no scipy dependency.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class FailureRatioConfidence:
    """Wilson confidence band on the failure ratio for one test fingerprint."""

    failures: int = 0
    total: int = 0
    point: float = 0.0   # p̂ = failures / total, 0..1
    low: float = 0.0     # Wilson lower bound, 0..1
    high: float = 0.0    # Wilson upper bound, 0..1
    z: float = Z_95

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce_count(value) -> int:
    """Coerce an input to a non-negative int; degrade malformed values to 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def wilson_failure_confidence(
    failures,
    total,
    z: float = Z_95,
) -> FailureRatioConfidence:
    """Closed-form Wilson interval on the failure ratio. Never raises.

    Degenerate inputs degrade gracefully rather than raising:
      * ``total <= 0`` (or non-numeric) → a neutral zero-width band at 0.
      * ``failures > total`` → clamped to the observable maximum (``total``).
      * a non-finite / non-positive ``z`` → falls back to the 95 % constant.
    """
    f = _coerce_count(failures)
    n = _coerce_count(total)
    if n <= 0:
        return FailureRatioConfidence(failures=f, total=0, point=0.0, low=0.0, high=0.0, z=Z_95)

    # A malformed window can report more failures than runs — clamp so p̂ stays
    # in [0, 1] instead of producing a nonsensical interval.
    if f > n:
        f = n

    try:
        zz = float(z)
        if not math.isfinite(zz) or zz <= 0:
            zz = Z_95
        p = f / n
        z2 = zz * zz
        denom = 1.0 + z2 / n
        center = (p + z2 / (2.0 * n)) / denom
        half = (zz / denom) * math.sqrt(
            max(0.0, p * (1.0 - p) / n + z2 / (4.0 * n * n))
        )
        low = center - half
        high = center + half
    except (ValueError, ZeroDivisionError, OverflowError):
        # Should be unreachable given the guards above, but never raise from a
        # scorer that sits on the refresh + read paths.
        pt = f / n if n else 0.0
        return FailureRatioConfidence(failures=f, total=n, point=round(pt, 4), low=0.0, high=1.0, z=Z_95)

    low = min(1.0, max(0.0, low))
    high = min(1.0, max(0.0, high))
    if high < low:  # numerical guard — keep the band ordered.
        low, high = high, low

    return FailureRatioConfidence(
        failures=f,
        total=n,
        point=round(p, 4),
        low=round(low, 4),
        high=round(high, 4),
        z=zz,
    )
