"""Flaky-sentinel verdict soundness — onset detection + recommendation reconcile.

Two AI-quality bugs in ``flaky_sentinel_agent`` (2026-07):

* Onset was "first status flip", which mislabels a *single permanent
  transition* (a fix ``[F,F,F,P,P,P]`` reported the FIX build as flaky_since; a
  plain regression ``[P,P,P,F,F,F]`` or a one-off blip) as a flakiness onset.
  ``_detect_flaky_onset`` now requires the flip to begin a genuinely
  oscillating region.

* The human ``recommendation`` was a raw failure-rate ladder computed
  independently of the structured ``verdict``, so a consistently-failing real
  bug (high failure rate, ``is_flaky=False``) was told to "QUARANTINE" while
  its own verdict said it wasn't flaky. ``_reconcile_recommendation`` derives
  the recommendation from the verdict, so the two can never contradict — and a
  persistent regression is flagged as a bug to fix, not quarantined.
"""
from __future__ import annotations

from app.agents.flaky_sentinel_agent import (
    _detect_flaky_onset,
    _reconcile_recommendation,
)


# ── _detect_flaky_onset ───────────────────────────────────────────────────

def test_onset_none_for_fix_transition():
    """Was broken, then fixed — the fix point is NOT a flaky onset."""
    assert _detect_flaky_onset(["FAILED", "FAILED", "FAILED", "PASSED", "PASSED"]) is None


def test_onset_none_for_regression_transition():
    """Was green, then broke and stayed broken — a regression, not flaky."""
    assert _detect_flaky_onset(["PASSED", "PASSED", "PASSED", "FAILED", "FAILED"]) is None


def test_onset_none_for_single_trailing_blip():
    """One recent failure that hasn't flipped back is not (yet) sustained."""
    assert _detect_flaky_onset(["PASSED", "PASSED", "PASSED", "PASSED", "FAILED"]) is None


def test_onset_none_for_all_stable():
    assert _detect_flaky_onset(["PASSED", "PASSED", "PASSED"]) is None
    assert _detect_flaky_onset(["FAILED", "FAILED", "FAILED"]) is None


def test_onset_detects_sustained_oscillation():
    """Genuine flip-flopping — onset is the first flip of the oscillating run."""
    assert _detect_flaky_onset(["PASSED", "FAILED", "PASSED", "FAILED"]) == 1


def test_onset_ignores_stable_prefix_then_oscillates():
    """A stable green prefix, then real oscillation → onset at the first flip
    of the oscillating region (index 3), not earlier."""
    assert _detect_flaky_onset(
        ["PASSED", "PASSED", "PASSED", "FAILED", "PASSED", "FAILED"]
    ) == 3


def test_onset_bounded_flaky_episode():
    """A blip that flips into AND back out of failure is a bounded flaky
    episode — its onset is the entry flip."""
    assert _detect_flaky_onset(["PASSED", "FAILED", "PASSED", "PASSED"]) == 1


# ── _reconcile_recommendation ─────────────────────────────────────────────

def _verdict(is_flaky: bool, cause_code: str) -> dict:
    return {"is_flaky": is_flaky, "likely_cause_code": cause_code}


def test_recommendation_persistent_regression_is_not_quarantine():
    """High failure rate but NOT flaky (persistent regression) → investigate as
    a bug, never 'quarantine as flaky'."""
    rec = _reconcile_recommendation(0.9, _verdict(False, "likely_regression"))
    assert not rec.startswith("QUARANTINE")  # the action verb, not a caveat mention
    assert "BUG" in rec.upper()
    assert "fix" in rec.lower()


def test_recommendation_insufficient_evidence_monitors():
    rec = _reconcile_recommendation(0.9, _verdict(False, "insufficient_data"))
    assert rec.startswith("MONITOR")


def test_recommendation_flaky_high_rate_quarantines():
    rec = _reconcile_recommendation(0.6, _verdict(True, "environment"))
    assert rec.startswith("QUARANTINE")


def test_recommendation_flaky_mid_rate_investigates():
    rec = _reconcile_recommendation(0.3, _verdict(True, "environment"))
    assert rec.startswith("INVESTIGATE URGENTLY")


def test_recommendation_flaky_low_rate_monitors():
    rec = _reconcile_recommendation(0.1, _verdict(True, "environment"))
    assert rec.startswith("MONITOR")


def test_recommendation_never_contradicts_verdict():
    """The core invariant: whenever the verdict says NOT flaky, the
    recommendation must never tell the user to quarantine it as flaky —
    regardless of how high the raw failure rate is."""
    for rate in (0.21, 0.5, 0.75, 1.0):
        rec = _reconcile_recommendation(rate, _verdict(False, "likely_regression"))
        assert not rec.startswith("QUARANTINE")
