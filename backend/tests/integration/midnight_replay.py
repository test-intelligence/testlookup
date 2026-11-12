"""Replay a CI run that crossed midnight UTC: collect before it, run after it.

CI run 43122547657 (main, 2026-11-12) collected the integration suite at
23:53:25 UTC and reached the coverage-snapshot and trend-summary tests at
00:00:34. Anything a test computed once at import time ("today") was a day
behind the product's, and a seed instant clamped to "a minute ago" fell on the
previous day. As a plugin, this module replays that on demand, on any machine,
at any hour:

    pytest -p tests.integration.midnight_replay -p no:testlookup -m integration \\
        tests/integration/test_coverage_snapshot_postgres.py

The Python clock reads :data:`BEFORE` while pytest imports and collects, and
:data:`AFTER` from the first test on. Postgres's ``now()`` is not moved, so it
suits tests whose day-dependent reads are Python's, which is every read the
coverage-snapshot and trend-summary modules make.

Its hooks run only under ``-p``. Tests import :func:`ticking_clock` from it.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from freezegun import freeze_time

_DAY = datetime.now(timezone.utc).date()
#: Thirty seconds before midnight UTC: imports and collection.
BEFORE = datetime.combine(_DAY, time(23, 59, 30), tzinfo=timezone.utc)
#: Thirty seconds after it: every test.
AFTER = datetime.combine(_DAY + timedelta(days=1), time(0, 0, 30), tzinfo=timezone.utc)


def ticking_clock(instant: datetime):
    """The Python clock at ``instant``, ticking on from there (freezegun).

    asyncio keeps the real monotonic clock (``real_asyncio``), so the event
    loop's timeouts and sleeps still work, and so does pytest's own timer
    (``_pytest.timing``): a timer started on the real clock and read on this
    one reports a test that took 1.79e9 seconds.
    """
    return freeze_time(instant, tick=True, real_asyncio=True, ignore=["_pytest.timing"])


_active: list = []


def _move_to(instant: datetime) -> None:
    while _active:
        _active.pop().stop()
    freezer = ticking_clock(instant)
    freezer.start()
    _active.append(freezer)


def pytest_configure(config) -> None:
    _move_to(BEFORE)


def pytest_collection_finish(session) -> None:
    _move_to(AFTER)


def pytest_unconfigure(config) -> None:
    while _active:
        _active.pop().stop()
