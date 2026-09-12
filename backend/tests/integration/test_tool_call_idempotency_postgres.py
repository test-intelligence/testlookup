"""Real PostgreSQL proof for E7.6: a tool call happens at most once.

The guarantee rests on two things a mocked session cannot see: the
``uq_agent_action_project_idempotency`` unique constraint deciding a concurrent
claim, and the ``executing`` row being durably committed before the external
call. Both are exercised here against the migrated schema.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def wired(monkeypatch):
    """Point the service's own sessions at the test database."""
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    async with engine.begin() as db:
        row = (await db.execute(text("SELECT project_id FROM test_runs ORDER BY created_at LIMIT 1"))).first()
    if row is None:
        await engine.dispose()
        pytest.skip("database has no test run fixture")
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    scope = uuid.uuid4()  # unique per test, so rows never collide across runs
    yield engine, row[0], scope
    async with engine.begin() as db:
        await db.execute(
            text("DELETE FROM agent_action_ledger WHERE project_id = :p AND target_id LIKE :s"),
            {"p": row[0], "s": f"e76-{scope}%"},
        )
    await engine.dispose()


async def _status(engine, project_id, key):
    async with engine.begin() as db:
        return (
            await db.execute(
                text("SELECT status, result_payload FROM agent_action_ledger "
                     "WHERE project_id = :p AND idempotency_key = :k"),
                {"p": project_id, "k": key},
            )
        ).first()


def _kwargs(project_id, scope, subject, call):
    return dict(
        project_id=project_id, tool="jira_ticket_creation", scope_id=scope,
        subject_id=f"e76-{scope}-{subject}", target_type="test_case",
        request_payload={"subject": subject}, call=call,
    )


async def test_the_claim_is_durable_before_the_call_and_executed_after(wired):
    from app.services import tool_call_idempotency as tci

    engine, project_id, scope = wired
    seen: list = []

    async def _file():
        key = tci.tool_call_key(tool="jira_ticket_creation", scope_id=scope, subject_id=f"e76-{scope}-a")
        seen.append((await _status(engine, project_id, key))[0])  # read from ANOTHER connection
        return {"ticket_key": "QA-1"}

    outcome = await tci.run_once(**_kwargs(project_id, scope, "a", _file))

    assert seen == ["executing"], "the claim was not committed before the external call"
    status, payload = await _status(engine, project_id, outcome.key)
    assert (status, payload) == ("executed", {"ticket_key": "QA-1"})


async def test_concurrent_attempts_make_the_call_exactly_once(wired):
    from app.services import tool_call_idempotency as tci

    engine, project_id, scope = wired
    calls = 0
    gate = asyncio.Event()

    async def _slow_file():
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"ticket_key": "QA-2"}

    async def _attempt():
        return await tci.run_once(**_kwargs(project_id, scope, "b", _slow_file))

    first = asyncio.ensure_future(_attempt())
    await asyncio.sleep(0.2)  # let the first attempt commit its claim
    second = await _attempt()
    gate.set()
    first_outcome = await first

    assert calls == 1
    assert {first_outcome.status, second.status} == {"executed", "outcome_unknown"}


async def test_crash_after_the_call_is_never_repeated(wired, monkeypatch):
    from app.services import tool_call_idempotency as tci

    engine, project_id, scope = wired
    filed: list[str] = []

    async def _file():
        filed.append("QA-3")
        return {"ticket_key": "QA-3"}

    async def _killed(**_kw):
        raise SystemExit("worker killed after Jira accepted the ticket")

    real_finish = tci._finish
    monkeypatch.setattr(tci, "_finish", _killed)
    with pytest.raises(SystemExit):
        await tci.run_once(**_kwargs(project_id, scope, "c", _file))
    monkeypatch.setattr(tci, "_finish", real_finish)

    retry = await tci.run_once(**_kwargs(project_id, scope, "c", _file))

    assert filed == ["QA-3"]
    assert retry.status == "outcome_unknown"
    assert (await _status(engine, project_id, retry.key))[0] == "executing"


async def test_stage_entry_lookup_sees_executed_and_executing_but_not_failed(wired):
    from app.services import tool_call_idempotency as tci

    engine, project_id, scope = wired

    async def _ok():
        return {"ticket_key": "QA-4"}

    async def _boom():
        raise RuntimeError("jira down")

    await tci.run_once(**_kwargs(project_id, scope, "done", _ok))
    with pytest.raises(RuntimeError):
        await tci.run_once(**_kwargs(project_id, scope, "failed", _boom))

    prior = await tci.prior_tool_calls(
        project_id=project_id, tool="jira_ticket_creation", scope_id=scope,
        subject_ids=[f"e76-{scope}-done", f"e76-{scope}-failed", f"e76-{scope}-never"],
    )

    assert set(prior) == {f"e76-{scope}-done"}
    assert prior[f"e76-{scope}-done"].status == "executed"
    assert prior[f"e76-{scope}-done"].result == {"ticket_key": "QA-4"}
