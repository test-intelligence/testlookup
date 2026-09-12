"""E7.6: an external tool call happens at most once per (tool, scope, subject).

The acceptance case from the architecture (section 7.5): a stage files a ticket
and then crashes before recording it; the retry must not file a second one.
The SQL itself and the concurrent-claim race are proven against real Postgres
in ``tests/integration/test_tool_call_idempotency_postgres.py``.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import tool_call_idempotency as tci

PROJECT = uuid.uuid4()
RUN = uuid.uuid4()


# ── the key ──────────────────────────────────────────────────────────────────


def test_the_key_is_stable_across_attempts():
    a = tci.tool_call_key(tool="jira_ticket_creation", scope_id=RUN, subject_id="tc-1")
    b = tci.tool_call_key(tool="jira_ticket_creation", scope_id=str(RUN), subject_id="tc-1")
    assert a == b


def test_the_key_separates_tool_scope_and_subject():
    base = tci.tool_call_key(tool="t", scope_id="s", subject_id="x")
    assert base != tci.tool_call_key(tool="u", scope_id="s", subject_id="x")
    assert base != tci.tool_call_key(tool="t", scope_id="s2", subject_id="x")
    assert base != tci.tool_call_key(tool="t", scope_id="s", subject_id="y")


def test_the_key_fits_the_column_and_is_readable():
    key = tci.tool_call_key(tool="jira_ticket_creation", scope_id=RUN, subject_id="x" * 5000)
    assert len(key) <= 128
    assert key.startswith("toolcall:jira_ticket_creation:")


@pytest.mark.parametrize("field", ["tool", "scope_id", "subject_id"])
def test_an_incomplete_identity_is_refused(field):
    kwargs = {"tool": "t", "scope_id": "s", "subject_id": "x", field: ""}
    with pytest.raises(ValueError):
        tci.tool_call_key(**kwargs)


# ── run_once orchestration (claim / finish scripted) ─────────────────────────


def _script(monkeypatch, claim_state, prior=None, finish_raises=None):
    finishes: list[dict] = []

    async def _claim(**_kw):
        return claim_state, dict(prior or {})

    async def _finish(**kw):
        finishes.append(kw)
        if finish_raises is not None:
            raise finish_raises

    monkeypatch.setattr(tci, "_claim", _claim)
    monkeypatch.setattr(tci, "_finish", _finish)
    return finishes


async def _run(call):
    return await tci.run_once(
        project_id=PROJECT, tool="jira_ticket_creation", scope_id=RUN,
        subject_id="tc-1", target_type="test_case", request_payload={"k": "v"},
        call=call,
    )


@pytest.mark.asyncio
async def test_a_fresh_claim_makes_the_call_and_records_executed(monkeypatch):
    finishes = _script(monkeypatch, "claimed")
    call = AsyncMock(return_value={"ticket_key": "QA-1"})

    outcome = await _run(call)

    call.assert_awaited_once()
    assert outcome.status == "executed"
    assert outcome.result == {"ticket_key": "QA-1"}
    assert [f["status"] for f in finishes] == ["executed"]
    assert finishes[0]["result_payload"] == {"ticket_key": "QA-1"}


@pytest.mark.asyncio
async def test_an_executed_row_is_replayed_without_calling_again(monkeypatch):
    finishes = _script(monkeypatch, "executed", prior={"ticket_key": "QA-1"})
    call = AsyncMock()

    outcome = await _run(call)

    call.assert_not_awaited()
    assert outcome.status == "replayed"
    assert outcome.result == {"ticket_key": "QA-1"}
    assert finishes == []


@pytest.mark.asyncio
async def test_an_executing_row_is_outcome_unknown_and_is_never_retried(monkeypatch):
    """The previous attempt may have filed the ticket. A missing ticket is
    visible; a duplicate is permanent."""
    finishes = _script(monkeypatch, "executing")
    call = AsyncMock()

    outcome = await _run(call)

    call.assert_not_awaited()
    assert outcome.status == "outcome_unknown"
    assert finishes == []


@pytest.mark.asyncio
async def test_a_failing_call_is_recorded_failed_and_re_raised(monkeypatch):
    finishes = _script(monkeypatch, "claimed")
    call = AsyncMock(side_effect=RuntimeError("jira down"))

    with pytest.raises(RuntimeError, match="jira down"):
        await _run(call)
    assert [f["status"] for f in finishes] == ["failed"]
    assert finishes[0]["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_failing_to_record_a_failure_does_not_mask_the_call_error(monkeypatch):
    _script(monkeypatch, "claimed", finish_raises=OSError("db gone"))
    with pytest.raises(RuntimeError, match="jira down"):
        await _run(AsyncMock(side_effect=RuntimeError("jira down")))


# ── the acceptance case, end to end over an in-memory ledger ─────────────────


class _Ledger:
    """Rows keyed by (project, key). Drives the REAL _claim and _finish through
    a fake session, so the claim branches are exercised, not re-implemented."""

    def __init__(self):
        self.rows: dict[tuple, SimpleNamespace] = {}
        self.commits = 0
        self.fail_next_commit: Exception | None = None

    def session_factory(self):
        ledger = self

        @asynccontextmanager
        async def _session():
            pending: list = []

            class _S:
                async def execute(self, stmt):
                    params = stmt.compile().params
                    key = next(v for v in params.values() if isinstance(v, str) and v.startswith("toolcall:"))
                    row = ledger.rows.get((PROJECT, key))
                    return SimpleNamespace(scalar_one_or_none=lambda: row)

                def add(self, row):
                    pending.append(row)

                async def commit(self):
                    if ledger.fail_next_commit is not None:
                        exc, ledger.fail_next_commit = ledger.fail_next_commit, None
                        raise exc
                    for row in pending:
                        ledger.rows[(row.project_id, row.idempotency_key)] = row
                    pending.clear()
                    ledger.commits += 1

            yield _S()

        return _session


@pytest.fixture
def ledger(monkeypatch):
    store = _Ledger()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", store.session_factory())
    return store


@pytest.mark.asyncio
async def test_crash_after_the_ticket_is_filed_never_files_a_second_one(ledger, monkeypatch):
    """Section 7.5 acceptance: the call succeeds, recording it crashes, the
    stage re-runs -- and the ticket is not filed again."""
    filed: list[str] = []

    async def _file():
        filed.append("QA-1")
        return {"ticket_key": "QA-1"}

    real_finish = tci._finish

    async def _crashing_finish(**kw):
        raise SystemExit("worker killed after Jira accepted the ticket")

    monkeypatch.setattr(tci, "_finish", _crashing_finish)
    with pytest.raises(SystemExit):
        await _run(_file)
    assert filed == ["QA-1"]

    monkeypatch.setattr(tci, "_finish", real_finish)
    retry = await _run(_file)

    assert filed == ["QA-1"], "the retry filed a second ticket"
    assert retry.status == "outcome_unknown"


@pytest.mark.asyncio
async def test_crash_after_recording_replays_the_ticket_on_retry(ledger):
    """The other crash point: the ticket was recorded, but the stage died
    before writing it onto the defect. The retry gets the ticket back."""
    call = AsyncMock(return_value={"ticket_key": "QA-7"})

    first = await _run(call)
    second = await _run(call)

    assert call.await_count == 1
    assert first.status == "executed"
    assert second.status == "replayed"
    assert second.result == {"ticket_key": "QA-7"}


@pytest.mark.asyncio
async def test_a_failed_call_is_tried_again(ledger):
    call = AsyncMock(side_effect=[RuntimeError("transient"), {"ticket_key": "QA-9"}])

    with pytest.raises(RuntimeError):
        await _run(call)
    retry = await _run(call)

    assert call.await_count == 2
    assert retry.status == "executed"


@pytest.mark.asyncio
async def test_losing_the_insert_race_reports_executing_not_a_second_call(ledger):
    """Two attempts insert the same key; the unique constraint rejects one."""
    ledger.fail_next_commit = IntegrityError("insert", {}, Exception("uq_agent_action_project_idempotency"))
    call = AsyncMock()

    outcome = await _run(call)

    call.assert_not_awaited()
    assert outcome.status == "outcome_unknown"


@pytest.mark.asyncio
async def test_the_claim_is_committed_before_the_call(ledger):
    """Durable before irreversible: the executing row must already be in the
    ledger when the external call starts."""
    seen: list[str] = []

    async def _file():
        row = next(iter(ledger.rows.values()))
        seen.append(row.status)
        return {"ticket_key": "QA-2"}

    await _run(_file)
    assert seen == ["executing"]


@pytest.mark.asyncio
async def test_a_reclaimed_attempt_cannot_start_a_call(ledger, monkeypatch):
    from app.services.pipeline_lease import LeaseLost

    async def _fenced_out(db, pipeline_run_id, token):
        raise LeaseLost(pipeline_run_id, token)

    monkeypatch.setattr("app.services.pipeline_lease.fence_or_raise", _fenced_out)
    call = AsyncMock()

    with pytest.raises(LeaseLost):
        await tci.run_once(
            project_id=PROJECT, tool="jira_ticket_creation", scope_id=RUN,
            subject_id="tc-1", target_type="test_case", request_payload={},
            call=call, pipeline_run_id=uuid.uuid4(), fencing_token="old",
        )
    call.assert_not_awaited()
    assert ledger.rows == {}
