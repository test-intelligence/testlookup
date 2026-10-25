"""W13 TCL-07: the TM Suites aggregate lists a first-class suite before its first ingest.

``GET /api/v1/test-management/suites`` (``list_test_suites``) gains ONE fourth
statement after the manual one: the project's non-default ``test_suites``
rows. A suite whose trimmed, lower-cased name matches no bucket is added as an
``empty: true`` row (zero counts, ``pass_rate`` None, its ``suite_id``); every
aggregated row stays byte-identical (no ``empty`` key, contract §0.27). Only
with one project and no release filter. A hand-built session scripted for
three answers meets ``StopAsyncIteration`` there and degrades to today's list.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def all(self):
        return self._rows


class _NoopSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RecordingLogger:
    def __init__(self):
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, event, **kw):
        pass


def _admin():
    return SimpleNamespace(id=uuid.uuid4(), role="ADMIN")


def _session(answers):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=answers),
        rollback=AsyncMock(),
        begin_nested=lambda: _NoopSavepoint(),
    )


@pytest.fixture
def quiet_lookups(monkeypatch):
    """The owner lookup and the cumulative history are other code paths."""
    import app.services.suite_history_service as history
    import app.services.suite_review_service as reviews

    async def _no_owners(_db, _project_id, _names):
        return {}

    async def _no_history(_db, **_kw):
        return {}

    monkeypatch.setattr(reviews, "list_suite_owners", _no_owners)
    monkeypatch.setattr(history, "compute_suite_history", _no_history)


def _sql(call) -> str:
    stmt = call.args[0]
    return " ".join(str(stmt).split())


@pytest.mark.asyncio
async def test_an_unreported_first_class_suite_is_added_as_an_empty_row(quiet_lookups):
    from app.routers.test_management_exports import list_test_suites

    project_id = uuid.uuid4()
    reported_id, waiting_id = uuid.uuid4(), uuid.uuid4()
    auto = [SimpleNamespace(
        suite_name="Checkout API", test_count=4, passed_count=3, failed_count=1,
        last_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc), last_run_id=uuid.uuid4(),
    )]
    first_class = [
        # Named like the bucket in another case and with padding: already listed.
        (reported_id, " checkout API ", datetime(2026, 9, 1, tzinfo=timezone.utc)),
        (waiting_id, "Payments UI", datetime(2026, 10, 2, tzinfo=timezone.utc)),
    ]
    db = _session([_Result(auto), _Result([]), _Result([]), _Result(first_class)])

    result = await list_test_suites(project_id=project_id, db=db, current_user=_admin())

    assert [row["suite_name"] for row in result] == ["Checkout API", "Payments UI"]
    aggregated, added = result
    assert "empty" not in aggregated and "suite_id" not in aggregated
    assert added["empty"] is True
    assert added["suite_id"] == str(waiting_id)
    assert (added["test_count"], added["passed_count"], added["failed_count"]) == (0, 0, 0)
    assert added["pass_rate"] is None
    assert added["last_run_at"] is None and added["last_run_id"] is None
    assert added["run_count"] == 0 and added["total_executions"] == 0
    # The fourth statement reads the project's NON-default suites.
    fourth = _sql(db.execute.await_args_list[3])
    assert "FROM test_suites" in fourth
    assert "test_suites.project_id" in fourth and "test_suites.is_default IS false" in fourth


@pytest.mark.asyncio
async def test_a_release_filter_or_all_projects_adds_no_fourth_statement(quiet_lookups):
    from app.routers.test_management_exports import list_test_suites

    # A release-scoped catalogue holds what ran in that release: the manual
    # statement is skipped (as today) and no first-class read is made.
    db = _session([_Result([]), _Result([]), _Result([(uuid.uuid4(), "Waiting", None)])])
    result = await list_test_suites(
        project_id=uuid.uuid4(), db=db, current_user=_admin(), release_id=uuid.uuid4(),
    )
    assert result == []
    assert db.execute.await_count == 2
    assert not any("test_suites" in _sql(call) for call in db.execute.await_args_list)

    # All Projects (an admin, no project): grouped by name across projects,
    # nothing is added.
    db = _session([_Result([]), _Result([]), _Result([]), _Result([(uuid.uuid4(), "Waiting", None)])])
    result = await list_test_suites(project_id=None, db=db, current_user=_admin())
    assert result == []
    assert db.execute.await_count == 3
    assert not any("FROM test_suites" in _sql(call) for call in db.execute.await_args_list)


@pytest.mark.asyncio
async def test_a_three_answer_session_degrades_to_todays_list_with_a_warning(quiet_lookups, monkeypatch):
    import app.routers.test_management_exports as mod

    recorder = _RecordingLogger()
    monkeypatch.setattr(mod, "logger", recorder)
    manual = [SimpleNamespace(
        suite_name="Checkout", test_count=2, passed_count=0, failed_count=0, last_run_at=None,
    )]
    three = _session([_Result([]), _Result([]), _Result(manual)])
    four = _session([_Result([]), _Result([]), _Result(manual), _Result([])])

    degraded = await mod.list_test_suites(project_id=uuid.uuid4(), db=three, current_user=_admin())
    today = await mod.list_test_suites(project_id=uuid.uuid4(), db=four, current_user=_admin())

    assert degraded == today
    assert [row["suite_name"] for row in degraded] == ["Checkout"]
    assert "empty" not in degraded[0]
    warnings = [kw for event, kw in recorder.warnings if event == "first_class_suites_unavailable"]
    assert warnings == [{"error_type": "StopAsyncIteration"}]
