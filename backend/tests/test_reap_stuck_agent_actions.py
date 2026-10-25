"""The agent-action reaper (W15 AGT-03, contract §2.3 test 7; D-13).

An executor killed after its durable claim leaves ``executing``; past the
10-minute lease the beat task settles it as ``failed`` / ``outcome_unknown``
and never retries it. The task is driven through ``.run()`` over a scripted
session with the REAL ``reap_stuck_actions``; which rows Postgres selects
(the lease cut-off, SKIP LOCKED, a tool call's own row left alone) is proven
in ``integration/test_agent_action_executor_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("celery")

from app.models.postgres import AccessAuditLog  # noqa: E402
from app.services import agent_action_ledger_service as service  # noqa: E402
from app.services.agent_action_types import EXECUTION_LEASE_SECONDS, REAPER_BATCH  # noqa: E402
from app.worker import tasks  # noqa: E402


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, stmt: Any) -> _Rows:
        self.statements.append(stmt)
        return _Rows(self.rows)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


def _stuck(started: datetime, action_type: str = "quarantine.apply") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), action_type=action_type, status="executing",
        execution_started_at=started, execution_completed_at=None, error_code=None,
        approved_by=uuid.uuid4(),
    )


def _pg_sql(stmt: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_the_task_reaps_and_commits_once(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [_stuck(now - timedelta(seconds=EXECUTION_LEASE_SECONDS + 1)) for _ in range(3)]
    session = _Session(rows)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: session)

    result = tasks.reap_stuck_agent_actions.run()

    assert result == {"reaped": 3}
    assert session.commits == 1
    for row in rows:
        assert row.status == "failed" and row.error_code == "outcome_unknown"
        assert row.execution_completed_at is not None
    audits = [a for a in session.added if isinstance(a, AccessAuditLog)]
    assert [a.action for a in audits] == ["agent_action.failed"] * 3
    assert {a.after_value["error_code"] for a in audits} == {"outcome_unknown"}
    assert {a.after_value["canonical_type"] for a in audits} == {"quarantine.apply"}


def test_nothing_stuck_is_a_clean_no_op(monkeypatch):
    session = _Session([])
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: session)
    assert tasks.reap_stuck_agent_actions.run() == {"reaped": 0}
    assert session.commits == 1 and session.added == []


async def test_the_sweep_selects_only_claims_past_the_lease_and_is_bounded():
    now = datetime.now(timezone.utc)
    session = _Session([])
    await service.reap_stuck_actions(session, now=now, limit=REAPER_BATCH * 5)
    (stmt,) = session.statements
    sql = _pg_sql(stmt)
    params = stmt.compile().params
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "agent_action_ledger.status = " in sql and "executing" in params.values()
    # The lease: a row started inside it (cut-off = now - 600 s) is not selected.
    assert now - timedelta(seconds=EXECUTION_LEASE_SECONDS) in params.values()
    assert "agent_action_ledger.execution_started_at < " in sql
    # REAPER_BATCH bounds a pass whatever the caller asks.
    assert REAPER_BATCH in params.values() and REAPER_BATCH * 5 not in params.values()


async def test_a_tool_calls_own_executing_row_is_never_reaped():
    """``tool_call_idempotency`` re-claims a FAILED key: reaping its row would call the tool twice."""
    session = _Session([])
    await service.reap_stuck_actions(session, now=datetime.now(timezone.utc))
    (stmt,) = session.statements
    types = next(v for v in stmt.compile().params.values() if isinstance(v, (list, tuple)))
    assert set(types) == {
        "decision.acknowledge", "decision_report_action", "defect.promote",
        "failure.reassign", "quarantine.apply",
    }


def test_the_task_is_scheduled_every_five_minutes_and_never_retried():
    from celery.schedules import crontab

    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["reap-stuck-agent-actions"]
    assert entry["task"] == tasks.reap_stuck_agent_actions.name == "app.worker.tasks.reap_stuck_agent_actions"
    assert entry["schedule"] == crontab(minute="*/5")
    task = celery_app.tasks["app.worker.tasks.reap_stuck_agent_actions"]
    assert task.max_retries == 0
    assert task.queue == "default"
    assert task.time_limit == 120


def test_a_failing_sweep_raises_its_own_scrubbed_error(monkeypatch):
    class _Boom(ConnectionRefusedError):
        pass

    async def boom(*_a: Any, **_kw: Any) -> int:
        raise _Boom("db host:5432 refused")

    monkeypatch.setattr(service, "reap_stuck_actions", boom)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session([]))
    with pytest.raises(RuntimeError, match="agent action reaper failed") as raised:
        tasks.reap_stuck_agent_actions.run()
    assert raised.value.__cause__ is None and raised.value.__suppress_context__ is True
    assert "5432" not in str(raised.value)
