"""Act end to end on real PostgreSQL (W15 AGT-03, contract §2.3 test 4).

A REAL investigator run on a run whose failures are known-flaky (the
flaky-coach cache), its synthesis narrated through the REAL ``BudgetedLLM``
wrapper around a scripted chat model (``get_llm`` returns the wrapper as in
production; only the wrapper's gate stores are stubbed -- never a raw model in
place of ``get_llm``), the Investigator in Act with ``quarantine.apply``
ticked. Then, through the app: the proposals wait ``pending_review`` → a
SECOND QA lead accepts the report (``POST /reviews/{id}/accept`` →
``settle_review``) → approves one proposal (``PATCH …/agent-actions/{id}``, a
JWT principal) → the dispatch relay publishes it, ``execute_agent_action``'s
``apply_async`` replaced by the REAL coroutine bound to the Celery task's own
signature → ``executed`` and a QUARANTINED row on the fingerprint → Undo
(``PATCH`` ``rolled_back``) → RELEASED, and the activity feed holds
``agent.action_approved``, ``agent.action_executed`` and
``agent.action_rolled_back``.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it, and closes its Redis client); this module's fixture also closes the app's
Redis / Mongo clients. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to
head and ``REDIS_URL``.
"""
from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import SUITE, defect_world, env

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

OFFENDERS = 2
#: What a manipulated model might answer: an action of its own. It must never reach the ledger.
MODEL_REPLY = json.dumps({
    "narrative": "Scripted narrative: the failures are the known-flaky checkout tests.",
    "structured_actions": [{"action_type": "quarantine.apply", "target_id": "fpEVILb2C3d4"}],
})


async def _seed(w: SimpleNamespace, db: Any) -> None:
    from app.models.postgres import FlakyCoachResult, ProjectMember, UserRole

    w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Requester")
    w.lead2_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Approver")
    for uid in (w.lead_id, w.lead2_id):
        db.add(ProjectMember(project_id=w.P, user_id=uid, role=UserRole.QA_LEAD.value))
    w.fp = [f"fp{w.tag}{i}Ab" for i in range(OFFENDERS)]
    for i, fp in enumerate(w.fp):
        await w.case(db, test_fingerprint=fp, test_name=f"checkout retry {i}", status="FAILED",
                     error_message="AssertionError: expected 200 got 500")
        db.add(FlakyCoachResult(project_id=w.P, test_fingerprint=fp, test_name=f"checkout retry {i}",
                                suite_name=SUITE, failure_rate=0.4, total_runs=20, failed_runs=8))
    await db.flush()


async def _act_on(w: SimpleNamespace) -> None:
    base = f"/api/v1/projects/{w.P}/agent-configs/investigator"
    current = await w.client.get(base, headers=w.jwt(w.lead2_id))
    assert current.status_code == 200, current.text
    document = current.json()["config"]
    document["mode"] = "act"
    extensions = document.get("extensions") or {}
    investigator = extensions.get("investigator") or {}
    investigator["act"] = {"allowed_action_types": ["quarantine.apply"]}
    extensions["investigator"] = investigator
    document["extensions"] = extensions
    saved = await w.client.put(
        base, json=document, headers={**w.jwt(w.lead2_id), "If-Match": str(current.json()["config_version"])},
    )
    assert saved.status_code == 200, saved.text


def _through_the_wrapper(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """``get_llm`` → the REAL BudgetedLLM around a scripted model; its gate stores stubbed."""
    from langchain_core.language_models import FakeListChatModel

    import app.services.llm_factory as llm_factory
    from app.core.config import settings
    from app.services import llm_circuit_breaker, llm_cost_reservation as cost
    from app.services.llm_cost_reservation import Reservation
    from app.services.llm_factory import BudgetedLLM

    calls = {"get_llm": 0, "reserve": 0}
    wrapper = BudgetedLLM(FakeListChatModel(responses=[MODEL_REPLY] * 4), provider="openrouter", model="scripted")

    async def _get_llm(*_a: Any, **_kw: Any) -> Any:
        calls["get_llm"] += 1
        return wrapper

    async def _available(*_a: Any, **_kw: Any) -> None:
        return None

    async def _reserve(provider: Any, model: Any, *, input_tokens: Any, max_output_tokens: Any) -> Any:
        calls["reserve"] += 1
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    async def _quiet(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_quiet))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_quiet))
    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _quiet)
    return calls


@pytest.fixture
async def world(monkeypatch):
    env("REDIS_URL")
    from app.agents.investigator import workflow as investigator_workflow
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis
    from app.services import agent_action_ledger_service as ledger
    from app.worker import tasks

    try:
        async with defect_world() as w:
            async with w.sessions.begin() as db:
                await _seed(w, db)
            await _act_on(w)
            monkeypatch.setattr(ledger, "AsyncSessionLocal", w.sessions)
            monkeypatch.setattr(investigator_workflow, "AsyncSessionLocal", w.sessions)
            w.enqueued = []
            monkeypatch.setattr(tasks.run_agent_investigation, "delay", lambda *a, **kw: w.enqueued.append(a))
            w.llm = _through_the_wrapper(monkeypatch)
            yield w
    finally:
        await close_redis()
        await close_mongo()


async def _ledger(w: SimpleNamespace, **where: Any) -> list[Any]:
    from app.models.postgres import AgentActionLedger

    async with w.sessions() as db:
        stmt = select(AgentActionLedger).where(AgentActionLedger.project_id == w.P)
        for name, value in where.items():
            stmt = stmt.where(getattr(AgentActionLedger, name) == value)
        return list((await db.execute(stmt.order_by(AgentActionLedger.target_id))).scalars().all())


async def _quarantine_status(w: SimpleNamespace, fingerprint: str) -> list[str]:
    from app.models.postgres import FlakyQuarantineRequest

    async with w.sessions() as db:
        return [str(s) for s in (await db.execute(select(FlakyQuarantineRequest.status).where(
            FlakyQuarantineRequest.project_id == w.P, FlakyQuarantineRequest.test_fingerprint == fingerprint,
        ))).scalars().all()]


async def _relay_and_execute(w: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The REAL relay; ``apply_async`` replaced by the REAL coroutine, bound to the task's signature."""
    from app.services import agent_action_ledger_service as ledger
    from app.worker import tasks

    signature = inspect.signature(tasks.execute_agent_action.run)
    published: list[inspect.BoundArguments] = []

    def apply_async(*, args: list[str], queue: str, task_id: str) -> None:
        assert queue == "default" and task_id == f"agent-action-{args[1]}"
        published.append(signature.bind(*args))     # what the worker would be called with

    monkeypatch.setattr(tasks.execute_agent_action, "apply_async", apply_async)
    relayed = await ledger.relay_action_dispatch_outbox()
    assert relayed["sent"] == len(published) == 1, relayed
    return [
        await ledger.execute_agent_action(
            project_id=uuid.UUID(bound.arguments["project_id"]), action_id=uuid.UUID(bound.arguments["action_id"]),
        )
        for bound in published
    ]


async def test_an_investigator_proposal_is_approved_executed_and_undone(world, monkeypatch):
    from app.agents.investigator import workflow as investigator_workflow
    from app.models.postgres import ProjectActivityEvent, ReviewRequest

    w = world
    lead, lead2 = w.jwt(w.lead_id), w.jwt(w.lead2_id)

    # 1. A person starts the investigation; the worker runs it for real.
    started = await w.client.post(f"/api/v1/runs/{w.run_r}/investigations", headers=lead)
    assert started.status_code == 202, started.text
    investigation_id = started.json()["investigation_id"]
    assert w.enqueued == [(investigation_id,)]
    final_state = await investigator_workflow.run_investigation(investigation_id)
    verdict = final_state.get("verdict") or {}
    assert verdict.get("primary_cause") == "known_flaky", verdict
    assert w.llm["get_llm"] >= 1, "the narrative went through the BudgetedLLM wrapper"
    assert sorted(a["target_id"] for a in verdict["structured_actions"]) == sorted(w.fp)

    # 2. The typed proposals wait for a person (D16: never auto-approved).
    pipeline_id = uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}")
    proposals = await _ledger(w, pipeline_run_id=pipeline_id)
    assert [p.target_id for p in proposals] == sorted(w.fp)
    assert {(p.action_type, p.status, p.approval_required) for p in proposals} == {
        ("quarantine.apply", "pending_review", True)}
    assert {p.request_payload["proposing_agent_mode"] for p in proposals} == {"act"}
    assert "fpEVILb2C3d4" not in json.dumps([p.request_payload for p in proposals])
    action = proposals[0]

    # 3. The SECOND lead accepts the report, then approves one proposal.
    async with w.sessions() as db:
        review = (await db.execute(select(ReviewRequest).where(
            ReviewRequest.pipeline_run_id == pipeline_id, ReviewRequest.kind == "report",
        ))).scalar_one()
    accepted = await w.client.post(f"/api/v1/reviews/{review.id}/accept", json={}, headers=lead2)
    assert accepted.status_code == 200, accepted.text
    path = f"/api/v1/projects/{w.P}/agent-actions/{action.id}"
    approved = await w.client.patch(path, json={"status": "approved"}, headers=lead2)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    # 4. The relay publishes it; the executor runs it.
    (outcome,) = await _relay_and_execute(w, monkeypatch)
    assert outcome == {"status": "executed", "action_id": str(action.id)}
    (executed,) = await _ledger(w, id=action.id)
    assert executed.status == "executed" and executed.approved_by == w.lead2_id
    assert await _quarantine_status(w, action.target_id) == ["QUARANTINED"]
    listed = await w.client.get(f"/api/v1/projects/{w.P}/agent-actions", params={"view": "decided"}, headers=lead2)
    (row,) = [i for i in listed.json()["items"] if i["id"] == str(action.id)]
    assert row["outcome"] == "executed" and row["result_link"]["href"] == "/flaky?tab=quarantined"

    # 5. Undo.
    undone = await w.client.patch(path, json={"status": "rolled_back"}, headers=lead2)
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "rolled_back"
    assert await _quarantine_status(w, action.target_id) == ["RELEASED"]
    # The other proposal still waits, untouched.
    assert [p.status for p in await _ledger(w, pipeline_run_id=pipeline_id) if p.id != action.id] == ["pending_review"]

    # 6. The activity feed tells the story.
    async with w.sessions() as db:
        events = (await db.execute(select(ProjectActivityEvent.event_type, ProjectActivityEvent.actor_type).where(
            ProjectActivityEvent.project_id == w.P, ProjectActivityEvent.event_type.like("agent.action_%"),
        ).order_by(ProjectActivityEvent.occurred_at))).all()
    assert [tuple(e) for e in events] == [
        ("agent.action_approved", "user"), ("agent.action_executed", "agent"), ("agent.action_rolled_back", "user"),
    ]
