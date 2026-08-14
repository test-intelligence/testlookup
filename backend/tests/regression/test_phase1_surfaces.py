"""Phase 1 surfaces must show evidence, not erase it.

Roadmap Phase 1 (``architecture/TEST_INTELLIGENCE_PLAN.md``). Each test pins a
property that, if it regressed, would silently remove information a user needs
to judge stability — the failure mode all three Phase 1 items exist to fix.

1. **The per-test history payload must carry run context.** A timeline that
   cannot say *which environment* is just a list of ticks.
2. **Retry evidence must survive to the client.** ``retry_count`` and
   ``is_flaky_run`` were persisted at ingest and never surfaced; a bare green
   tick on a test that only passed on attempt 3 erases the one fact that
   mattered.
3. **Flake load must never read as a debt that trends to zero.** Insertion rate
   tracks fix rate, so a "remaining" framing is a promise that cannot be kept.
"""
from __future__ import annotations

import inspect

import pytest

from app.services.flake_load_service import (
    MIN_RUNS_FOR_LOAD,
    summarize_flake_load,
)


# ── 1 + 2. The canonical run-history payload ─────────────────────────────────

# Every field the timeline needs. Guarding the SET rather than one field means
# adding a column to the timeline without exposing it fails here.
REQUIRED_TIMELINE_FIELDS = {
    "test_case_id",
    "test_run_id",
    "status",
    "duration_ms",
    "suite_name",
    "created_at",
    "build_number",
    "branch",
    "environment",
    "environment_source",
    "retry_count",
    "is_flaky_run",
}


def test_canonical_run_history_exposes_every_timeline_field():
    """The endpoint builds bare dicts by hand, so a field is only present if
    someone typed it. Pin the whole set."""
    from app.routers.suites import list_canonical_run_history

    source = inspect.getsource(list_canonical_run_history)
    missing = {f for f in REQUIRED_TIMELINE_FIELDS if f'"{f}"' not in source}
    # environment/environment_source arrive via resolve_environment().to_dict()
    if "resolve_environment" in source:
        missing -= {"environment", "environment_source"}
    assert not missing, f"canonical run-history payload drops timeline fields: {sorted(missing)}"


def test_canonical_run_history_resolves_environment_rather_than_reading_it_raw():
    """A raw ``run.environment`` read would return NULL for every pre-0129 run
    and every caller that never sends one. The resolver is what turns that into
    an honest 'unknown' plus a derived best-effort key."""
    from app.routers.suites import list_canonical_run_history

    source = inspect.getsource(list_canonical_run_history)
    assert "resolve_environment(run)" in source


def test_run_history_service_returns_the_run_not_just_the_case():
    """Regression: the service joined TestRun purely to order by its timestamp
    and then discarded it, so the router had no way to report environment,
    branch or build without a second query."""
    from app.services.test_suite_service import list_runs_for_canonical

    source = inspect.getsource(list_runs_for_canonical)
    assert "select(TestCase, TestRun)" in source, (
        "the timeline needs the run row, not just the test case"
    )


# ── 3. Flake load is a load, never a burndown ────────────────────────────────

def test_thin_window_reports_insufficient_rather_than_a_jumpy_share():
    load = summarize_flake_load(
        MIN_RUNS_FOR_LOAD - 1, 1, project_id="p", window_days=30
    )
    assert load.flake_load is None
    assert load.insufficient_reason


def test_flake_load_is_a_share_between_zero_and_one():
    load = summarize_flake_load(100, 7, project_id="p", window_days=30)
    assert load.flake_load == 0.07


def test_flake_load_cannot_exceed_one_even_on_inconsistent_counts():
    """A subset cannot exceed its superset. If the two queries ever disagree
    (a run deleted between them, say), clamp rather than emit 130%."""
    load = summarize_flake_load(10, 13, project_id="p", window_days=30)
    assert load.flake_load == 1.0
    assert load.runs_with_flake_noise == 10


def test_zero_runs_is_insufficient_not_zero_percent():
    """0/0 is not 'a perfectly clean project' — it is no evidence."""
    load = summarize_flake_load(0, 0, project_id="p", window_days=30)
    assert load.flake_load is None
    assert load.insufficient_reason


def test_payload_states_the_load_framing_so_it_cannot_read_as_a_backlog():
    """THE Phase 1 framing guard. Flaky-test insertion rate tracks the fix
    rate, so anything implying a remaining count trending to zero is a promise
    that will never be kept."""
    payload = summarize_flake_load(100, 5, project_id="p", window_days=30).to_dict()
    framing = payload["framing"].lower()
    assert "not a debt" in framing and "zero" in framing
    for forbidden in ("remaining", "burndown", "burn-down", "backlog"):
        assert forbidden not in framing, (
            f"flake-load framing must not imply a backlog: found {forbidden!r}"
        )


@pytest.mark.parametrize("field", ["flake_load", "insufficient_reason", "total_runs",
                                   "runs_with_flake_noise", "min_runs_for_load"])
def test_payload_publishes_what_it_measured_and_judged_against(field):
    payload = summarize_flake_load(3, 1, project_id="p", window_days=30).to_dict()
    assert field in payload


def test_flake_load_query_counts_distinct_runs():
    """The numerator is RUNS carrying noise, not test cases. Counting cases
    would let one run with 40 retried tests read as 40 noisy runs."""
    from app.services.flake_load_service import get_flake_load

    source = inspect.getsource(get_flake_load)
    assert "distinct(TestCase.test_run_id)" in source
