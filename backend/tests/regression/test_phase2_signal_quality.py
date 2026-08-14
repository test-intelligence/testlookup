"""Phase 2: a score must degrade honestly, and a verdict must never act alone.

Roadmap Phase 2 (``architecture/TEST_INTELLIGENCE_PLAN.md``). Two properties are
load-bearing and everything here pins one of them.

**1. The score degrades honestly at low volume.** The Phase 0 census measured
every genuine project on the reference deployment at 12–15 fingerprints with a
median of 5–12 runs, which is why the Bayesian posterior was descoped: over that
much data a posterior mostly reports its prior back. The replacement must not
repeat the sin in a different shape — so a thin history yields no score and a
visible confidence band, never a confident-looking number.

**2. Nothing auto-suppresses (decision D2).** Google found that when a
previously stable test turned flaky, roughly 1 in 6 times the cause was a real
production bug. A wrongly-shown verdict costs a minute; a wrongly-suppressed
failure ships the bug.
"""
from __future__ import annotations

import pytest

from app.services.flaky_score_service import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    DEFAULT_WEIGHTS,
    MIN_OBSERVATIONS,
    compute_score,
    confidence_for,
    duration_variance_signal,
    environment_instability_signal,
)
from app.services.flaky_suppression_gate import (
    ADVISORY_SPECIFICITY_FLOOR,
    ALLOW_SUPPRESSION,
    MODE_ADVISORY,
    MODE_HINT,
    decide,
)


# ── 1. The score degrades honestly ───────────────────────────────────────────

def test_thin_history_yields_no_score_at_all():
    """THE Phase 2 guard. Below the evidence floor there is no number — not a
    small number, not a hedged number. A score computed from 3 runs is the same
    mistake as the posterior we descoped, wearing different clothes."""
    result = compute_score(
        test_fingerprint="fp",
        observation_count=MIN_OBSERVATIONS - 1,
        result_volatility=1.0,   # maximally damning inputs...
        retry_rate=1.0,
    )
    assert result.score is None, "no score may be emitted below the evidence floor"
    assert result.is_scored is False
    assert result.confidence == CONFIDENCE_NONE
    assert result.insufficient_reason


def test_components_are_still_returned_when_unscored():
    """Refusing to score is not refusing to show what was observed."""
    result = compute_score(test_fingerprint="fp", observation_count=1, retry_rate=0.5)
    assert result.components["retry_rate"] == 0.5


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, CONFIDENCE_NONE),
        (MIN_OBSERVATIONS - 1, CONFIDENCE_NONE),
        (MIN_OBSERVATIONS, CONFIDENCE_LOW),
        (9, CONFIDENCE_LOW),
        (10, CONFIDENCE_MEDIUM),
        (19, CONFIDENCE_MEDIUM),
        (20, CONFIDENCE_HIGH),
        (5000, CONFIDENCE_HIGH),
    ],
)
def test_confidence_bands_on_evidence_volume_alone(count, expected):
    assert confidence_for(count) == expected


def test_identical_scores_from_different_evidence_are_distinguishable():
    """A test seen 5 times and one seen 500 can produce the same 0.5.
    Collapsing that is how a guess starts looking like a measurement."""
    thin = compute_score(test_fingerprint="a", observation_count=5, result_volatility=0.5)
    thick = compute_score(test_fingerprint="b", observation_count=500, result_volatility=0.5)
    assert thin.score == thick.score
    assert thin.confidence != thick.confidence


def test_score_is_bounded_even_with_absurd_inputs():
    result = compute_score(
        test_fingerprint="fp",
        observation_count=50,
        result_volatility=99,
        retry_rate=-4,
        duration_variance=float("inf"),
        environment_instability="not a number",
    )
    assert 0.0 <= result.score <= 1.0


def test_a_stabilising_test_scores_lower_than_an_unstable_one():
    """The acceptance criterion: score decays as a test stabilises."""
    unstable = compute_score(
        test_fingerprint="fp", observation_count=40,
        result_volatility=0.8, retry_rate=0.6,
    )
    settled = compute_score(
        test_fingerprint="fp", observation_count=40,
        result_volatility=0.05, retry_rate=0.0,
    )
    assert settled.score < unstable.score


def test_score_is_reproducible_from_its_stored_components_and_weights():
    """Acceptance criterion: a score nobody can recompute cannot be audited
    when it disagrees with a human."""
    result = compute_score(
        test_fingerprint="fp", observation_count=30,
        result_volatility=0.4, retry_rate=0.2,
        duration_variance=0.1, environment_instability=0.6,
    )
    recomputed = sum(
        value * result.weights[name] for name, value in result.components.items()
    ) / sum(result.weights[name] for name in result.components)
    assert round(recomputed, 4) == result.score


def test_weights_are_stored_per_row_so_reweighting_cannot_rewrite_history():
    result = compute_score(test_fingerprint="fp", observation_count=30)
    assert result.weights == DEFAULT_WEIGHTS
    assert set(result.components) == set(DEFAULT_WEIGHTS)


def test_all_zero_weights_do_not_raise():
    result = compute_score(
        test_fingerprint="fp", observation_count=30,
        result_volatility=1.0,
        weights={k: 0.0 for k in DEFAULT_WEIGHTS},
    )
    assert result.score == 0.0


# ── The individual signals ───────────────────────────────────────────────────

def test_duration_signal_is_relative_not_absolute():
    """A 10s test varying by 1s is steadier than a 100ms test varying by 1s.
    Raw stddev would rank them the other way round."""
    slow_test = duration_variance_signal(mean_ms=10_000, stddev_ms=1_000)
    fast_test = duration_variance_signal(mean_ms=100, stddev_ms=1_000)
    assert slow_test < fast_test


def test_duration_signal_contributes_nothing_without_a_baseline():
    """No usable mean means no evidence — not invented spread."""
    assert duration_variance_signal(mean_ms=0, stddev_ms=500) == 0.0
    assert duration_variance_signal(mean_ms=None, stddev_ms=None) == 0.0


def test_environment_signal_needs_two_environments_to_say_anything():
    """With one environment there is nothing to compare across. That is an
    absence of evidence, never an assertion of stability."""
    assert environment_instability_signal({"ci": [True, False, True]}) == 0.0
    assert environment_instability_signal({}) == 0.0


def test_environment_signal_flags_a_test_that_only_fails_in_one_place():
    split = environment_instability_signal({
        "ci-linux": [True, True, True],
        "ci-windows": [False, False, False],
    })
    everywhere = environment_instability_signal({
        "ci-linux": [True, False],
        "ci-windows": [True, False],
    })
    assert split > everywhere
    assert 0.0 <= split <= 1.0


# ── 2. Decision D2: never auto-suppress ──────────────────────────────────────

def test_suppression_is_disabled_unconditionally():
    """Decision D2, pinned as a constant rather than config. Flipping this must
    be a deliberate, reviewable act — not a toggle someone finds in settings."""
    assert ALLOW_SUPPRESSION is False


@pytest.mark.parametrize("specificity", [None, 0.0, 0.5, 0.89, 0.9, 0.99, 1.0])
def test_no_measured_specificity_whatsoever_unlocks_suppression(specificity):
    """Even a perfect classifier does not earn the right to act. ~1 in 6
    newly-flaky tests reflected a real bug."""
    decision = decide("p", specificity=specificity, sample_count=10_000)
    assert decision.may_suppress is False


def test_unmeasured_classifier_is_treated_as_weak_not_strong():
    """Defaulting an unknown to trusted is how a silent wrong verdict ships."""
    assert decide("p", specificity=None, sample_count=0).mode == MODE_HINT


def test_weak_classifier_downgrades_the_verdict_to_a_hint():
    decision = decide("p", specificity=ADVISORY_SPECIFICITY_FLOOR - 0.01, sample_count=500)
    assert decision.mode == MODE_HINT
    assert "below" in decision.reason


def test_strong_classifier_earns_advisory_but_never_more():
    decision = decide("p", specificity=0.99, sample_count=500)
    assert decision.mode == MODE_ADVISORY
    assert decision.may_suppress is False


def test_decision_payload_states_the_never_suppress_policy():
    """A consumer must not be able to read 'advisory' as 'enforced once we
    trust it more'."""
    payload = decide("p", specificity=0.99, sample_count=500).to_dict()
    assert "never auto-suppress" in payload["policy"]
    assert payload["advisory_specificity_floor"] == ADVISORY_SPECIFICITY_FLOOR
    assert payload["may_suppress"] is False


# ── Review findings: provenance and bounded reads ────────────────────────────

def test_scoring_read_is_bounded():
    """A nightly sweep visits every active project, and a busy project's 30-day
    window can be hundreds of thousands of per-test rows. An unbounded read is
    a worker-memory risk, not a theoretical one."""
    import inspect

    from app.services.flaky_score_service import MAX_SCORING_ROWS, score_project

    source = inspect.getsource(score_project)
    assert ".limit(max_rows + 1)" in source, "the scoring window read must be capped"
    assert MAX_SCORING_ROWS > 0


def test_truncated_window_is_reported_not_silent():
    """Scoring a partial window as if it were whole would misstate the
    evidence the score rests on."""
    import inspect

    from app.services.flaky_score_service import score_project

    source = inspect.getsource(score_project)
    assert "flaky_score_window_truncated" in source


def test_truncation_keeps_recent_history():
    """A stability judgement is made from the recent past, so the cap must
    drop the OLDEST rows, not the newest."""
    import inspect

    from app.services.flaky_score_service import score_project

    source = inspect.getsource(score_project)
    assert "created_at.desc()" in source
    assert "reversed(rows)" in source, "run order must be restored for flip adjacency"


def test_stored_score_records_the_window_it_was_computed_over():
    """Regression: window_days was never assigned, so a 90-day score would sit
    in the table claiming the 30-day default — a provenance lie about the
    number beside it."""
    import inspect

    from app.services.flaky_score_service import store_scores

    source = inspect.getsource(store_scores)
    assert "existing.window_days = window_days" in source


def test_unscored_fingerprints_are_not_written_as_zero():
    """A stored 0.0 would read as 'measured, and clean' — the opposite of
    'not enough evidence to say'."""
    import inspect

    from app.services.flaky_score_service import store_scores

    source = inspect.getsource(store_scores)
    assert "if not result.is_scored:" in source and "continue" in source


def test_score_carries_its_test_name_for_display():
    result = compute_score(
        test_fingerprint="fp", test_name="test_checkout_total", observation_count=30
    )
    assert result.test_name == "test_checkout_total"
    assert result.to_dict()["test_name"] == "test_checkout_total"


# ── The query path must actually execute ─────────────────────────────────────
#
# Added after a live defect: ``score_project`` imported ``PerformanceBaseline``
# from ``app.models.postgres``, where the class is called ``PerfBaseline``. The
# import sat inside the function, its only caller wrapped every project in
# ``except Exception`` so one bad project cannot stop a nightly sweep, and the
# tests above only exercised the pure scoring functions. So the statement never
# ran in CI, and in production the whole feature computed nothing on every
# project while the sweep reported success.
#
# ``scripts/quality_gate.py::backend.model-imports-resolve`` catches the static
# shape. This catches the dynamic one: the query path is executed at least once.

class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None


class _FakeSession:
    """Minimal AsyncSession stand-in — enough to run the query path."""

    def __init__(self):
        self.executed = 0

    async def execute(self, *_args, **_kwargs):
        self.executed += 1
        return _FakeResult()

    async def flush(self):
        return None


async def test_score_project_query_path_executes_its_imports():
    """Runs the real ``score_project`` far enough to execute every
    function-local import. A misspelled model name fails here."""
    import uuid

    from app.services.flaky_score_service import score_project

    db = _FakeSession()
    result = await score_project(db, uuid.uuid4())
    # No rows in, no scores out — the assertion that matters is that we got
    # here at all rather than raising ImportError.
    assert result == []
    assert db.executed >= 1


def test_the_perf_baseline_model_is_named_what_the_scorer_imports():
    """Names the exact confusion that caused the outage, so a future rename of
    either side breaks loudly instead of silently."""
    from app.models import postgres as models

    assert hasattr(models, "PerfBaseline")
    assert not hasattr(models, "PerformanceBaseline")
    assert models.PerfBaseline.__tablename__ == "perf_baselines"
