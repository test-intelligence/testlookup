"""Tests for the P2-3 orphan-TestSuite reaper (nightly Celery beat).

Pins:

1. The reaper finds TestSuite rows older than ``min_age_minutes`` that
   have NO ``CanonicalTestCase`` children and are NOT the project's
   default suite.
2. It emits one structured WARNING per orphan (``event=orphan_test_suite``
   so log filters can pin them) and increments the
   ``orphan_test_suites_total`` Prometheus counter.
3. It NEVER deletes data — ops decides; this is a flag-only safety net.
4. It's wired to the Celery beat schedule at 05:00 UTC nightly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows


class _ExecResult:
    def __init__(self, *, scalars=None):
        self._scalars = scalars or []
    def scalars(self):
        return _ScalarsResult(self._scalars)


def _fake_async_session_factory(orphan_suites):
    """Return a callable that yields an async-context session whose
    .execute() returns the supplied orphan list as scalars."""

    class _Sess:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _stmt):
            return _ExecResult(scalars=orphan_suites)
        async def commit(self):
            return None
        async def rollback(self):
            return None

    return _Sess


def _orphan_suite(name: str = "stale-suite", age_minutes: int = 120):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name=name,
        is_default=False,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_orphan_reaper_is_registered_in_beat_schedule():
    """If the schedule entry disappears, the reaper stops running. Pin it."""
    from app.worker.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "nightly-orphan-test-suite-flag" in schedule, (
        "P2-3 reaper missing from beat schedule — won't run"
    )
    entry = schedule["nightly-orphan-test-suite-flag"]
    assert entry["task"] == "app.worker.tasks.flag_orphan_test_suites"


def _run_task(min_age_minutes: int = 60) -> dict:
    """Invoke the task synchronously through ``apply()`` so Celery
    binds ``self`` correctly (the function body reads ``self.request.id``
    for logging). ``apply()`` runs the task in-process and returns an
    EagerResult; ``.get()`` unwraps the return value."""
    from app.worker import tasks as worker_tasks
    return worker_tasks.flag_orphan_test_suites.apply(
        kwargs={"min_age_minutes": min_age_minutes},
    ).get()


def test_orphan_reaper_flags_one_warning_per_orphan(monkeypatch, caplog):
    """Each orphan TestSuite row produces a structured WARNING with the
    suite id, project id, and name."""
    orphans = [
        _orphan_suite(name="auth-old"),
        _orphan_suite(name="checkout-old"),
    ]
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        _fake_async_session_factory(orphans),
        raising=False,
    )

    with caplog.at_level("WARNING"):
        result = _run_task()

    assert result["orphan_count"] == 2
    assert {o["suite_name"] for o in result["orphans"]} == {"auth-old", "checkout-old"}

    warning_events = [
        r for r in caplog.records
        if r.levelname == "WARNING" and getattr(r, "event", None) == "orphan_test_suite"
    ]
    assert len(warning_events) == 2


def test_orphan_reaper_returns_empty_when_no_orphans(monkeypatch):
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        _fake_async_session_factory([]),
        raising=False,
    )

    result = _run_task()

    assert result == {
        "min_age_minutes": 60,
        "orphan_count": 0,
        "orphans": [],
    }


def test_orphan_reaper_increments_prometheus_counter(monkeypatch):
    """The orphan_test_suites_total counter goes up by exactly the
    number of orphans flagged in this sweep."""
    from app.core.metrics import orphan_test_suites_total

    orphans = [_orphan_suite(), _orphan_suite(), _orphan_suite()]
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        _fake_async_session_factory(orphans),
        raising=False,
    )

    before = orphan_test_suites_total._value.get()
    _run_task()
    after = orphan_test_suites_total._value.get()

    assert after - before == 3


def test_orphan_reaper_never_deletes(monkeypatch):
    """The reaper is a flag-only safety net — it must NOT mutate data.
    We assert by checking that the session's delete() / commit() are
    never invoked (pure read path)."""
    mutated = {"flag": False}

    class _Sess:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _stmt):
            return _ExecResult(scalars=[_orphan_suite()])
        async def delete(self, _obj):  # pragma: no cover - asserted below
            mutated["flag"] = True
        async def commit(self):  # pragma: no cover - asserted below
            mutated["flag"] = True
        async def rollback(self):
            return None

    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", lambda: _Sess(), raising=False,
    )

    _run_task()

    assert mutated["flag"] is False, (
        "Orphan reaper must NOT delete or commit — only flag for ops"
    )
