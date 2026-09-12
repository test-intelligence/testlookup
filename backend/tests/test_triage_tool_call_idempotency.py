"""E7.6: triage files at most one Jira ticket per failure, however often it re-runs.

Two defects are pinned here.

1. **The retry duplicate** (architecture section 7.5). ``_triage_one`` inserts
   the defect, files the Jira ticket, then writes the ticket onto the defect.
   A crash between the last two left an open defect with no ticket, and the
   next attempt took its ``jira_retry`` branch and filed a second ticket.

2. **The call that could never succeed.** Triage passed ``labels=[...]`` to
   ``create_jira_issue``, which has never accepted a ``labels`` argument. The
   ``TypeError`` was caught and logged as "Jira ticket creation skipped", so a
   policy-approved triage could not file a ticket at all. Every existing test
   mocked the client loosely and could not see it (memory: mocking hides the
   argument check). The client here is ``create_autospec``'d from the real
   function, so the old call fails these tests.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from app.services import jira_client
from app.services.action_policy import ActionStatus

PROJECT = str(uuid.uuid4())
RUN = str(uuid.uuid4())
TC = str(uuid.uuid4())
DEFECT = uuid.uuid4()
TICKET = {"ticket_id": "10001", "ticket_key": "QA-1", "ticket_url": "https://jira/browse/QA-1"}


class _Ledger:
    """In-memory agent_action_ledger behind the guard's own sessions."""

    def __init__(self):
        self.rows: dict[str, SimpleNamespace] = {}

    def factory(self):
        ledger = self

        @asynccontextmanager
        async def _session():
            pending: list = []

            class _S:
                async def execute(self, stmt):
                    params = stmt.compile().params
                    keys = [v for v in params.values() if isinstance(v, str) and v.startswith("toolcall:")]
                    row = ledger.rows.get(keys[0]) if keys else None
                    return SimpleNamespace(scalar_one_or_none=lambda: row, all=lambda: [])

                def add(self, row):
                    pending.append(row)

                async def commit(self):
                    for row in pending:
                        ledger.rows[row.idempotency_key] = row
                    pending.clear()

            yield _S()

        return _session


class _TriageDB:
    """The agent's own sessions: test-case lookup, defect insert, ticket link.

    ``defect_exists`` switches the insert to the ON CONFLICT no-op, which is
    what a re-run after a crash sees. ``crash_on_link`` makes the commit that
    writes the ticket onto the defect fail once.
    """

    def __init__(self):
        self.defect_exists = False
        self.linked_ticket = None
        self.crash_on_link = False

    def factory(self):
        db = self

        @asynccontextmanager
        async def _session():
            state = {"updating": False, "values": None}

            class _S:
                async def execute(self, stmt):
                    sql = str(stmt).upper()
                    if sql.startswith("UPDATE"):
                        state["updating"] = True
                        state["values"] = stmt.compile().params
                        return SimpleNamespace()
                    if sql.startswith("INSERT"):
                        row = None if db.defect_exists else (DEFECT,)
                        return SimpleNamespace(first=lambda: row)
                    if "DEFECTS" in sql:
                        existing = SimpleNamespace(id=DEFECT, jira_ticket_id=db.linked_ticket, jira_ticket_url=None)
                        return SimpleNamespace(first=lambda: existing)
                    return SimpleNamespace(first=lambda: SimpleNamespace(test_name="test_login", suite_name="auth"))

                async def commit(self):
                    if state["updating"]:
                        if db.crash_on_link:
                            db.crash_on_link = False
                            raise RuntimeError("worker died after Jira accepted the ticket")
                        db.linked_ticket = state["values"].get("jira_ticket_id")

            yield _S()

        return _session


@pytest.fixture
def wired(monkeypatch):
    from app.agents import triage_agent
    from app.core.config import settings

    ledger, triage_db = _Ledger(), _TriageDB()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", ledger.factory())
    monkeypatch.setattr(triage_agent, "AsyncSessionLocal", triage_db.factory())
    monkeypatch.setattr(settings, "JIRA_ENABLED", True)
    monkeypatch.setattr(
        triage_agent,
        "check_jira_ticket_creation_policy",
        AsyncMock(return_value={"requires_approval": False, "initial_status": ActionStatus.APPROVED,
                                "policy_reasons": []}),
    )
    client = create_autospec(jira_client.create_jira_issue, return_value=dict(TICKET))
    monkeypatch.setattr(triage_agent, "create_jira_issue", client)
    agent = triage_agent.DefectTriageAgent()
    monkeypatch.setattr(agent, "_get_jira_key", AsyncMock(return_value="QA"))
    return SimpleNamespace(agent=agent, client=client, ledger=ledger, db=triage_db)


def _state():
    return {"pipeline_run_id": None, "project_id": PROJECT, "test_run_id": RUN}


ANALYSIS = {"confidence_score": 95, "failure_category": "PRODUCT_BUG", "root_cause_summary": "boom",
            "recommended_actions": ["fix it"]}


@pytest.mark.asyncio
async def test_the_ticket_call_matches_the_real_client_signature(wired):
    """Would have failed before E7.6: ``labels=`` raised TypeError."""
    result = await wired.agent._triage_one(TC, ANALYSIS, PROJECT, {}, _state())

    wired.client.assert_awaited_once()
    assert result["action"] == "created"
    assert result.get("ticket_key", TICKET["ticket_key"]) == "QA-1"
    assert wired.db.linked_ticket == "10001"


@pytest.mark.asyncio
async def test_crash_after_ticket_creation_retry_files_no_second_ticket(wired):
    """Section 7.5 acceptance test, through the real agent."""
    wired.db.crash_on_link = True
    with pytest.raises(RuntimeError, match="worker died"):
        await wired.agent._triage_one(TC, ANALYSIS, PROJECT, {}, _state())
    assert wired.client.await_count == 1
    assert wired.db.linked_ticket is None, "the crash left the defect unlinked"

    # The stage re-runs. The defect exists with no ticket, so triage takes its
    # jira_retry branch -- which used to file a second ticket.
    wired.db.defect_exists = True
    retry = await wired.agent._triage_one(TC, ANALYSIS, PROJECT, {}, _state())

    assert wired.client.await_count == 1, "the retry filed a second Jira ticket"
    assert retry["action"] == "jira_retry"
    assert wired.db.linked_ticket == "10001", "the replayed ticket must repair the defect link"


@pytest.mark.asyncio
async def test_a_call_whose_outcome_was_never_recorded_is_not_repeated(wired, monkeypatch):
    from app.services import tool_call_idempotency as tci

    async def _killed(**_kw):
        raise SystemExit("killed between the Jira call and recording it")

    real_finish = tci._finish
    monkeypatch.setattr(tci, "_finish", _killed)
    with pytest.raises(SystemExit):
        await wired.agent._triage_one(TC, ANALYSIS, PROJECT, {}, _state())
    monkeypatch.setattr(tci, "_finish", real_finish)

    wired.db.defect_exists = True
    retry = await wired.agent._triage_one(TC, ANALYSIS, PROJECT, {}, _state())

    assert wired.client.await_count == 1
    assert retry["action"] == "jira_outcome_unknown"


@pytest.mark.asyncio
async def test_stage_entry_takes_unknown_outcomes_off_the_work_list(monkeypatch):
    """Section 7.5: the ledger is read before the stage plans its work."""
    from app.agents import triage_agent
    from app.core.config import settings
    from app.services.tool_call_idempotency import PriorToolCall

    monkeypatch.setattr(settings, "JIRA_ENABLED", True)
    monkeypatch.setattr(
        triage_agent, "prior_tool_calls",
        AsyncMock(return_value={TC: PriorToolCall(status="executing")}),
    )
    agent = triage_agent.DefectTriageAgent()
    for name in ("mark_stage_running", "broadcast_progress", "log_decision", "mark_stage_done"):
        monkeypatch.setattr(agent, name, AsyncMock())
    triage_one = AsyncMock()
    monkeypatch.setattr(agent, "_triage_one", triage_one)

    out = await agent.run({**_state(), "pipeline_run_id": str(uuid.uuid4()), "analyses": {TC: ANALYSIS}})

    triage_one.assert_not_awaited()
    results = out["triage_results"] if isinstance(out, dict) else out.triage_results
    assert [r["action"] for r in results] == ["jira_outcome_unknown"]


@pytest.mark.asyncio
async def test_a_reclaimed_lease_stops_triage_instead_of_being_logged(monkeypatch):
    from app.agents import triage_agent
    from app.core.config import settings
    from app.services.pipeline_lease import LeaseLost

    monkeypatch.setattr(settings, "JIRA_ENABLED", False)
    agent = triage_agent.DefectTriageAgent()
    for name in ("mark_stage_running", "broadcast_progress", "log_decision", "mark_stage_done"):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_triage_one", AsyncMock(side_effect=LeaseLost("p1", "tok")))

    with pytest.raises(LeaseLost):
        await agent.run({**_state(), "pipeline_run_id": "p1", "analyses": {TC: ANALYSIS}})
