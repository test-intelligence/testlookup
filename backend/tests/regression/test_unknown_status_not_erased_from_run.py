"""A test result the server cannot interpret must not vanish into a green run.

Reproduced end-to-end against the live homelab before this fix, on a throwaway
project. Four events were streamed to ``POST /api/v1/stream/ingest``; three
``PASSED`` and one ``"FAIL"`` — a plausible typo for ``FAILED``, and the exact
spelling several frameworks use natively. ``LiveEvent.status`` is a free-form
``Optional[str]`` (documented as ``PASSED | FAILED | SKIPPED | BROKEN`` but not
validated), so the batch was accepted:

    {"accepted": 4, "run_id": "zz-statusvocab-run-1", "created_session": true}

The run that came out of it::

    status=PASSED  total_tests=3  passed=3  failed=0  skipped=0  broken=0
    pass_rate=100.0

while its own ``test_cases`` rows were::

    test_alpha                 PASSED
    test_beta                  PASSED
    test_gamma                 PASSED
    test_delta_REALLY_FAILED   UNKNOWN     <-- persisted, and uncounted

A release gate reading that run would green-light a build whose test failed.

The mechanism is a vocabulary mismatch, the same class as F-074:

* ``live_session_drainer._event_to_row`` maps an unrecognised status to
  ``TestStatus.UNKNOWN`` and **writes the row**;
* ``RedisLiveRunState.record_test_event`` had a four-entry ``field_map`` and did
  ``counter_field = field_map.get(status_upper)`` — ``None`` for anything else,
  and the increment sat behind ``if counter_field:``, so **no counter moved at
  all**, not even ``total``;
* ``total`` then fell back to ``passed + failed + skipped + broken`` = 3, so the
  fourth test was erased from the run's own arithmetic;
* ``terminal_run_status(executed=3, failed=0, broken=0)`` → ``PASSED``.

The file-upload path had a milder form of the same hole: ``total`` there is a
``COUNT(*)``, so UNKNOWN rows were inside the total with no column reporting
them — the four status columns simply did not sum to ``total_tests``.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import LaunchStatus, TestStatus  # noqa: E402
from app.services import ingestion, live_session_drainer, stream_service  # noqa: E402
from app.services.run_status import terminal_run_status  # noqa: E402
from app.streams.live_run_state import RedisLiveRunState  # noqa: E402

pytestmark = pytest.mark.regression


def test_every_test_status_has_a_live_counter():
    """The counter vocabulary must cover the status vocabulary.

    Written against the *enum* rather than a hard-coded list, so a sixth
    TestStatus member fails the build instead of being silently uncounted —
    the class, not the instance.
    """
    src = inspect.getsource(RedisLiveRunState.record_test_event)
    for member in TestStatus:
        assert f'"{member.value}"' in src, (
            f"{member.value} increments no live counter, so a result with that "
            f"status is erased from the run's totals"
        )


def test_an_unrecognised_status_still_increments_something():
    """The bug was the ``None`` default, not a missing enum member.

    ``"FAIL"``, ``"error"``, ``""`` — anything outside the vocabulary must land
    in a bucket rather than incrementing nothing.
    """
    src = inspect.getsource(RedisLiveRunState.record_test_event)
    assert 'field_map.get(status_upper, "unknown")' in src, (
        "an unrecognised status falls back to no counter at all"
    )


def test_unknown_is_included_in_the_live_total():
    """``total`` must not be derivable as a sum that omits unknown."""
    drainer = inspect.getsource(live_session_drainer.drain_run_buffer)
    assert "agg_unknown" in drainer
    assert (
        "agg_passed + agg_failed + agg_skipped + agg_broken + agg_unknown" in drainer
    ), "the live total still sums only the four recognised buckets"

    upsert = inspect.getsource(stream_service.upsert_test_run)
    assert "passed + failed + skipped + broken + unknown" in upsert, (
        "the close-path total fallback still omits unknown"
    )


def test_the_file_path_counts_unknown_too():
    """Both ingest paths, or the two disagree — the F-054 class of skew."""
    src = inspect.getsource(ingestion._update_run_aggregates)
    assert "TestStatus.UNKNOWN" in src
    assert '"unknown_tests"' in src


def test_a_run_with_uninterpretable_results_does_not_grade_passed():
    """The core harm: 3 passed + 1 uninterpretable was PASSED at 100%."""
    assert terminal_run_status(3, 0, 0, 1) is not LaunchStatus.PASSED
    assert terminal_run_status(3, 0, 0, 1) is LaunchStatus.STOPPED


def test_real_failures_still_outrank_unknown():
    """A genuine failure must still grade FAILED, not be downgraded to the
    neutral state by the presence of an uninterpretable result."""
    assert terminal_run_status(4, 1, 0, 1) is LaunchStatus.FAILED
    assert terminal_run_status(4, 0, 1, 1) is LaunchStatus.FAILED


def test_clean_runs_are_unaffected():
    """No unknown results — the grading is exactly what it was."""
    assert terminal_run_status(3, 0, 0, 0) is LaunchStatus.PASSED
    assert terminal_run_status(3, 0, 0) is LaunchStatus.PASSED  # default arg
    assert terminal_run_status(0, 0, 0, 0) is LaunchStatus.STOPPED
    assert terminal_run_status(5, 2, 0, 0) is LaunchStatus.FAILED


def test_no_path_grades_a_run_with_an_inline_ternary():
    """Three sites graded runs, and only one used the shared helper.

    ``live_consumer`` and the live-persist Celery task each carried their own
    ``FAILED if (failed + broken) > 0 else PASSED``, so fixing
    ``terminal_run_status`` alone would have left two false-green paths open.
    They also bypassed the helper's empty-run rule, grading a run that executed
    nothing as PASSED — the exact thing its docstring says must not happen.
    """
    from app.streams import live_consumer
    from app.worker import tasks

    for mod in (live_consumer, tasks):
        src = inspect.getsource(mod)
        assert "LaunchStatus.FAILED if (failed + broken) > 0 else" not in src, (
            f"{mod.__name__} still grades runs inline instead of via "
            f"terminal_run_status, so it ignores unknown results"
        )
        assert "terminal_run_status" in src


def test_placeholder_rows_cover_unknown():
    """The synthesised-placeholder path must not drop the bucket either.

    It fabricates one row per counted result when the event buffer is empty; a
    bucket it does not know about is a row that never appears.
    """
    src = inspect.getsource(__import__("app.worker.tasks", fromlist=["x"]))
    assert "(int(unknown), TestStatus.UNKNOWN.value)" in src


def test_the_run_row_can_report_unknown():
    """Without a column the four counts still would not sum to total_tests."""
    from app.models.postgres import TestRun

    assert "unknown_tests" in TestRun.__table__.columns, (
        "test_runs has no unknown_tests column, so an uninterpretable result "
        "is still invisible in every per-status breakdown"
    )
