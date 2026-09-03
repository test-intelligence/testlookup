"""Regression: execution time is bounded, not trusted (S3a).

Why this exists
---------------
``TestRun.start_time`` is read by ~43 call sites for ordering, time-window
filtering and duration, and all of them assume it means *when the run
executed*. On the upload paths it did not — both ingest paths stamped
``datetime.now()`` — so for a JUnit archive uploaded the morning after a
nightly, the column was a day wrong.

That became load-bearing with release attribution: ladder rungs 4 and 5 both
predicate on execution time, so ingest time in that column silently degrades
both to "whatever release is current", which is exactly the mis-attribution the
as-of design exists to prevent.

The value now comes from the client, which makes it untrusted input with an
unusual failure mode: a bad timestamp is not a validation error, it is a run
that **disappears**. ``start_time`` drives the 30-day window every dashboard
defaults to, so a value far in the past removes the run from every default view
and one in the future pins it to the top of every ordering — neither raising
anything. These tests pin the bounds that stop that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.execution_time import (
    MAX_BACKDATE,
    QUIET_FUTURE_SKEW,
    resolve_execution_time,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_absent_value_keeps_the_previous_behaviour():
    """Omitting the field must not change anything for existing clients.

    Every SDK and CI job in the field today sends no execution time. If the
    absent case did anything other than fall back to ingest time, this feature
    would be a breaking change disguised as an enhancement.
    """
    assert resolve_execution_time(None, now=NOW) == NOW


def test_a_genuinely_old_run_is_preserved():
    """The whole point. Uploading yesterday's archive must record yesterday.

    If this regressed to ingest time, rungs 4 and 5 would resolve against the
    wrong moment and the run would land in whichever release happens to be
    active now — with no error, and no way to tell from the data.
    """
    executed = NOW - timedelta(days=2)
    assert resolve_execution_time(executed, now=NOW) == executed


def test_a_future_timestamp_is_clamped_rather_than_stored():
    """A future run is always wrong, and always clock skew.

    Stored as-is it would sort above every real run forever in the
    ``ORDER BY start_time DESC`` that the run list, compliance pack and live
    view all use — a permanently pinned row that no amount of new data
    displaces.
    """
    assert resolve_execution_time(NOW + timedelta(hours=3), now=NOW) == NOW
    # Small skew is clamped too — the difference is only whether it is logged.
    assert resolve_execution_time(NOW + timedelta(seconds=30), now=NOW) == NOW


def test_the_backdate_bound_is_asymmetric_with_the_future_bound():
    """Past and future are bounded differently ON PURPOSE.

    Future is always an error. Past is usually legitimate — re-ingesting an old
    archive is the case this feature was built for — so the past bound is
    generous, and only rejects values so old they are almost certainly a unit
    error (seconds read as milliseconds, or a zero epoch).
    """
    assert MAX_BACKDATE > timedelta(days=90), "must tolerate re-ingesting old archives"
    assert QUIET_FUTURE_SKEW < timedelta(hours=1), "future tolerance must stay tight"

    just_inside = NOW - MAX_BACKDATE + timedelta(days=1)
    assert resolve_execution_time(just_inside, now=NOW) == just_inside

    way_outside = NOW - MAX_BACKDATE - timedelta(days=1)
    assert resolve_execution_time(way_outside, now=NOW) == NOW


def test_epoch_zero_does_not_silently_hide_a_run():
    """The classic unit bug: a millisecond timestamp parsed as seconds.

    Stored verbatim the run sits in 1970 and vanishes from every window in the
    product. Falling back to ingest time makes it visible and wrong-by-hours
    rather than invisible and wrong-by-decades.
    """
    assert resolve_execution_time(datetime(1970, 1, 1, tzinfo=timezone.utc), now=NOW) == NOW


def test_a_naive_datetime_is_treated_as_utc_not_guessed():
    """Guessing a zone would be a silent error of up to a day.

    Assuming UTC is what every client sending a naive value means, and it is
    logged so a client sending local time can be found rather than quietly
    mis-recorded.
    """
    naive = datetime(2026, 9, 1, 12, 0)
    got = resolve_execution_time(naive, now=NOW)
    assert got.tzinfo is not None
    assert got == naive.replace(tzinfo=timezone.utc)


def test_resolution_never_raises():
    """An ingest must not fail over a bad timestamp.

    The test results are the thing of value; the timestamp is metadata. Losing
    a run's results because its clock was wrong would be a far worse outcome
    than recording a slightly wrong time and saying so in the log.
    """
    for candidate in (
        None,
        NOW,
        NOW + timedelta(days=9999),
        NOW - timedelta(days=9999),
        datetime(1, 1, 1, tzinfo=timezone.utc),
        datetime(9999, 12, 31, tzinfo=timezone.utc),
    ):
        assert isinstance(resolve_execution_time(candidate, now=NOW), datetime)


def test_the_ingest_paths_actually_use_the_resolver():
    """A bounded resolver nothing calls is decorative.

    Both upload paths previously hard-coded ``datetime.now()``; the point of
    S3a is that they no longer do.
    """
    import inspect

    from app.services import ingestion_pipeline

    src = inspect.getsource(ingestion_pipeline.create_run_from_payload)
    assert "resolve_execution_time(executed_at" in src
    assert "start_time=datetime.now" not in src, (
        "the upload path must not stamp ingest time as execution time"
    )
