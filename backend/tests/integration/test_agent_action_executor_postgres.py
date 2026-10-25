"""The Act executor, its adapters and the reaper on real PostgreSQL (W15 AGT-03, contract §2.3 test 3).

What only SQL shows: the durable claim is visible to a SECOND session before
the effect; two concurrent dispatches of one id produce exactly ONE quarantine
(``FOR UPDATE`` + the claim); a worker killed after the claim leaves
``executing`` and the reaper settles it as ``outcome_unknown`` without a retry,
while a claim inside its lease and a tool call's own ``executing`` row stay
untouched; every adapter's target is re-loaded inside the action's project (a
run of another project is refused, nothing created); and each undo restores
the real rows -- the quarantine released, a NULL previous assignee restored,
the defect closed ``INVALID``. Real rows and REAL services throughout
(``propose_quarantine`` / ``approve`` / ``release`` commit on their own).

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it, and closes its Redis client); this module's fixture also closes the app's
Redis / Mongo clients (the quarantine approval's notifications and the
executor's pipeline event bind them). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``
migrated to head and ``REDIS_URL``. Fingerprints start with letters (the
persistence sanitizer never mistakes them for phone or card numbers).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text

from tests.integration.defect_w06_world import SUITE, StatementCounter, defect_world, env

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _seed(w: SimpleNamespace, db: Any) -> None:
    """Two leads, a suite owner, failing cases, a cluster, an accepted pipeline (timestamps built HERE)."""
    from app.models.postgres import (
        AgentPipelineRun, FailureCluster, LaunchStatus, ProjectMember, ReviewRequest,
        TestRun, TestSuiteOwner, UserRole,
    )
    from app.services.review_request_service import AI_DISCLAIMER_VERSION

    now = datetime.now(timezone.utc)
    w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Requester")
    w.lead2_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Approver")
    w.owner_id = await w.user(db, role=UserRole.QA_ENGINEER.value, full_name="Suite Owner")
    for uid, role in ((w.lead_id, UserRole.QA_LEAD), (w.lead2_id, UserRole.QA_LEAD),
                      (w.owner_id, UserRole.QA_ENGINEER)):
        db.add(ProjectMember(project_id=w.P, user_id=uid, role=role.value))
    db.add(TestSuiteOwner(project_id=w.P, suite_name=SUITE, owner_user_id=w.owner_id))
    w.run_q = uuid.uuid4()
    db.add(TestRun(id=w.run_q, project_id=w.Q, build_number=f"w15d-q-{w.tag}", jenkins_job="w15d",
                   status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1))
    await db.flush()
    w.fp = [f"fp{w.tag}{i}Ab" for i in range(4)]
    w.cases = []
    for i, fp in enumerate(w.fp):
        w.cases.append(await w.case(db, test_fingerprint=fp, test_name=f"checkout retry {i}", status="FAILED"))
    w.case_q = await w.case(db, run_id=w.run_q, test_fingerprint=f"fq{w.tag}Ab", status="FAILED")
    w.cluster_pk = uuid.uuid4()
    db.add(FailureCluster(id=w.cluster_pk, test_run_id=w.run_r, cluster_id="cl_001", label=f"timeouts {w.tag}",
                          member_test_ids=[str(c) for c in w.cases], size=len(w.cases)))
    w.pipeline = uuid.uuid4()
    db.add(AgentPipelineRun(id=w.pipeline, test_run_id=w.run_r, requested_by=w.lead_id,
                            workflow_type="investigation", status="completed"))
    await db.flush()
    db.add(ReviewRequest(
        project_id=w.P, kind="report", subject_type="pipeline_run", subject_id=str(w.pipeline),
        pipeline_run_id=w.pipeline, test_run_id=w.run_r, state="accepted",
        ai_disclaimer_version=AI_DISCLAIMER_VERSION, reviewed_at=now,
    ))
    await db.flush()


async def _act_on(w: SimpleNamespace, ticked: tuple[str, ...]) -> None:
    """The investigator in Act with ``ticked`` allowed, through the real PUT (QA_LEAD, If-Match)."""
    base = f"/api/v1/projects/{w.P}/agent-configs/investigator"
    current = await w.client.get(base, headers=w.jwt(w.lead2_id))
    assert current.status_code == 200, current.text
    document = current.json()["config"]
    document["mode"] = "act"
    extensions = document.get("extensions") or {}
    investigator = extensions.get("investigator") or {}
    investigator["act"] = {"allowed_action_types": list(ticked)}
    extensions["investigator"] = investigator
    document["extensions"] = extensions
    saved = await w.client.put(
        base, json=document, headers={**w.jwt(w.lead2_id), "If-Match": str(current.json()["config_version"])},
    )
    assert saved.status_code == 200, saved.text


@pytest.fixture
async def world(monkeypatch):
    env("REDIS_URL")
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis
    from app.services import agent_action_ledger_service as service

    try:
        async with defect_world() as w:
            async with w.sessions.begin() as db:
                await _seed(w, db)
            await _act_on(w, ("quarantine.apply", "failure.reassign", "defect.promote"))
            # The executor's own sessions on the world's engine (the statement counter watches it).
            monkeypatch.setattr(service, "AsyncSessionLocal", w.sessions)
            # promote_cluster's semantic-memory lookups (Chroma) are not this module's subject.
            from app.services import defect_promotion_service as promotion

            async def _no_owner(*_a: Any, **_kw: Any) -> None:
                return None

            async def _no_duplicate(*_a: Any, **_kw: Any) -> tuple[None, bool]:
                return None, False

            monkeypatch.setattr(promotion, "_resolve_defect_owner_from_memory", _no_owner)
            monkeypatch.setattr(promotion, "_find_duplicate_semantic", _no_duplicate)
            yield w
    finally:
        await close_redis()
        await close_mongo()


def _effect(w: SimpleNamespace, **fields: Any) -> dict[str, Any]:
    payload = {
        "proposing_agent_id": "investigator", "proposing_agent_mode": "act",
        "investigation_id": str(uuid.uuid4()), "test_run_id": str(w.run_r),
        "title": "An agent proposal", "rationale": "display only", "risk": "low", "evidence": [],
    }
    payload.update(fields)
    return payload


def _quarantine(w: SimpleNamespace, i: int = 0, **overrides: Any) -> tuple[str, str, dict[str, Any]]:
    fields = {"test_fingerprint": w.fp[i], "test_name": f"checkout retry {i}", "suite_name": SUITE}
    fields.update(overrides)
    return "quarantine.apply", w.fp[i], _effect(w, **fields)


def _reassign(w: SimpleNamespace, i: int = 1) -> tuple[str, str, dict[str, Any]]:
    return "failure.reassign", str(w.cases[i]), _effect(
        w, test_case_id=str(w.cases[i]), assignee_user_id=str(w.owner_id), suite_name=SUITE,
        test_name=f"checkout retry {i}", risk="medium",
    )


def _defect(w: SimpleNamespace) -> tuple[str, str, dict[str, Any]]:
    return "defect.promote", "cl_001", _effect(
        w, failure_cluster_id=str(w.cluster_pk), cluster_id="cl_001", cluster_label="timeouts", risk="medium",
    )


_TARGET_TYPES = {"quarantine.apply": "test", "failure.reassign": "test_case", "defect.promote": "cluster"}


async def _approved(w: SimpleNamespace, proposal: tuple[str, str, dict[str, Any]]) -> uuid.UUID:
    """A proposal created and approved by the second lead through the REAL ledger service."""
    from app.services.agent_action_ledger_service import create_action_proposal, transition_action

    action_type, target_id, payload = proposal
    async with w.sessions.begin() as db:
        row, created = await create_action_proposal(
            db, project_id=w.P, action_type=action_type, target_type=_TARGET_TYPES[action_type],
            target_id=target_id, idempotency_key=f"w15d:{uuid.uuid4().hex}", request_payload=payload,
            test_run_id=w.run_r, pipeline_run_id=w.pipeline,
        )
        assert created and row.status == "pending_review"
        await transition_action(db, project_id=w.P, action_id=row.id, to_status="approved",
                                actor_user_id=w.lead2_id)
        return row.id


async def _execute(w: SimpleNamespace, action_id: uuid.UUID) -> dict[str, Any]:
    from app.services.agent_action_ledger_service import execute_agent_action

    return await execute_agent_action(project_id=w.P, action_id=action_id)


async def _row(w: SimpleNamespace, action_id: uuid.UUID) -> Any:
    from app.models.postgres import AgentActionLedger

    async with w.sessions() as db:
        return (await db.execute(select(AgentActionLedger).where(AgentActionLedger.id == action_id))).scalar_one()


async def _quarantines(w: SimpleNamespace, fingerprint: str) -> list[Any]:
    from app.models.postgres import FlakyQuarantineRequest

    async with w.sessions() as db:
        return list((await db.execute(select(FlakyQuarantineRequest).where(
            FlakyQuarantineRequest.project_id == w.P,
            FlakyQuarantineRequest.test_fingerprint == fingerprint,
        ))).scalars().all())


async def _user(w: SimpleNamespace, user_id: uuid.UUID) -> Any:
    from app.models.postgres import User

    async with w.sessions() as db:
        return (await db.execute(select(User).where(User.id == user_id))).scalar_one()


class _Wrapped:
    """The REAL adapter of a type, with a hook before its effect."""

    def __init__(self, action_type: str, before_effect: Any) -> None:
        from app.services.agent_action_adapters.adapter_base import get_adapter

        self.inner = get_adapter(action_type)
        self.action_type = action_type
        self.commits_effect = self.inner.commits_effect
        self.before_effect = before_effect

    async def preflight(self, db: Any, **kw: Any) -> None:
        await self.inner.preflight(db, **kw)

    async def execute(self, db: Any, **kw: Any) -> Any:
        await self.before_effect(kw["action"])
        return await self.inner.execute(db, **kw)

    async def rollback(self, db: Any, **kw: Any) -> Any:
        return await self.inner.rollback(db, **kw)


# ── The claim ────────────────────────────────────────────────────────────────
async def test_the_claim_is_visible_to_a_second_session_before_the_effect(world, monkeypatch):
    from app.models.postgres import AgentActionLedger
    from app.services import agent_action_ledger_service as service

    w = world
    seen: list[tuple[str, Any]] = []

    async def peek(action: Any) -> None:
        async with w.sessions() as other:            # NOT the executor's session
            seen.append((await other.execute(select(
                AgentActionLedger.status, AgentActionLedger.execution_started_at,
            ).where(AgentActionLedger.id == action.id))).one())
        assert await _quarantines(w, w.fp[0]) == [], "no effect yet"

    monkeypatch.setattr(service, "get_adapter", lambda _t: _Wrapped("quarantine.apply", peek))
    action_id = await _approved(w, _quarantine(w, 0))

    outcome = await _execute(w, action_id)

    assert outcome == {"status": "executed", "action_id": str(action_id)}
    (status, started_at), = seen
    assert status == "executing" and started_at is not None
    row = await _row(w, action_id)
    assert row.status == "executed" and row.execution_started_at == started_at
    (request,) = await _quarantines(w, w.fp[0])
    assert request.status == "QUARANTINED" and request.approved_by_user_id == w.lead2_id
    assert row.rollback_payload == {"quarantine_request_id": str(request.id)}
    assert row.result_payload["result_link"]["href"] == "/flaky?tab=quarantined"


async def test_two_concurrent_dispatches_of_one_id_make_exactly_one_quarantine(world):
    w = world
    action_id = await _approved(w, _quarantine(w, 1))

    outcomes = await asyncio.gather(_execute(w, action_id), _execute(w, action_id))

    assert sorted(o["status"] for o in outcomes) == ["already_claimed", "executed"]
    requests = await _quarantines(w, w.fp[1])
    assert len(requests) == 1 and requests[0].status == "QUARANTINED"
    assert (await _execute(w, action_id))["status"] == "already_executed"


# ── A worker killed after the claim, and the reaper ──────────────────────────
def _ledger_row(w: SimpleNamespace, *, action_type: str, started_at: datetime, approval_required: bool = True) -> Any:
    from app.models.postgres import AgentActionLedger
    from app.services.canonical_json import stable_json_sha256

    payload = {"tool": "jira.create"} if not approval_required else _quarantine(w, 3)[2]
    return AgentActionLedger(
        id=uuid.uuid4(), project_id=w.P, action_type=action_type, target_type="test", target_id=w.fp[3],
        status="executing", approval_required=approval_required, idempotency_key=f"w15d:{uuid.uuid4().hex}",
        request_sha256=stable_json_sha256(payload, max_bytes=128_000), request_payload=payload,
        pipeline_run_id=w.pipeline, execution_started_at=started_at,
    )


async def test_a_worker_killed_after_the_claim_is_reaped_as_outcome_unknown_never_retried(world, monkeypatch):
    from app.services import agent_action_ledger_service as service

    w = world

    async def killed(_action: Any) -> None:
        raise asyncio.CancelledError()               # a BaseException: what a SIGKILL'd task leaves

    monkeypatch.setattr(service, "get_adapter", lambda _t: _Wrapped("quarantine.apply", killed))
    action_id = await _approved(w, _quarantine(w, 2))
    with pytest.raises(asyncio.CancelledError):
        await _execute(w, action_id)
    claimed = await _row(w, action_id)
    assert claimed.status == "executing" and claimed.execution_completed_at is None
    claim_at = claimed.execution_started_at
    # Beside it: a claim still inside its lease, and a tool call's OWN executing row (never ours to reap).
    inside = _ledger_row(w, action_type="quarantine.apply", started_at=claim_at + timedelta(seconds=300))
    tool = _ledger_row(w, action_type="jira.create", started_at=claim_at - timedelta(hours=1), approval_required=False)
    async with w.sessions.begin() as db:
        db.add_all([inside, tool])

    async with w.sessions() as db:
        reaped = await service.reap_stuck_actions(db, now=claim_at + timedelta(seconds=601))
        await db.commit()

    assert reaped == 1
    row = await _row(w, action_id)
    assert (row.status, row.error_code) == ("failed", "outcome_unknown")
    assert (await _row(w, inside.id)).status == "executing"
    assert (await _row(w, tool.id)).status == "executing"
    # A re-dispatch of the same id changes nothing: never retried.
    assert (await _execute(w, action_id))["status"] == "not_executable"
    again = await _row(w, action_id)
    assert (again.status, again.error_code, again.updated_at) == ("failed", "outcome_unknown", row.updated_at)
    assert await _quarantines(w, w.fp[2]) == []
    async with w.sessions.begin() as db:      # the two seeded claims are not part of any other test
        await db.execute(text("DELETE FROM agent_action_ledger WHERE id = ANY(:ids)"), {"ids": [inside.id, tool.id]})


# ── The IDOR boundary, on real rows ──────────────────────────────────────────
@pytest.mark.parametrize("make", [_quarantine, _reassign, _defect])
async def test_a_target_in_another_project_is_refused_and_nothing_changes(world, make):
    from app.models.postgres import Defect, TestCase

    w = world
    action_type, target_id, payload = make(w)
    payload["test_run_id"] = str(w.run_q)           # a run of project Q, in a P action
    if action_type == "failure.reassign":
        payload["test_case_id"] = str(w.case_q)     # a failing case of Q's run
    action_id = await _approved(w, (action_type, target_id, payload))

    outcome = await _execute(w, action_id)

    assert outcome == {"status": "failed", "action_id": str(action_id), "reason": "target_not_in_project"}
    assert (await _row(w, action_id)).error_code == "target_not_in_project"
    assert await _quarantines(w, w.fp[0]) == []
    async with w.sessions() as db:
        assert (await db.execute(select(TestCase.assigned_to_user_id).where(TestCase.id == w.case_q))).scalar_one() is None
        assert (await db.execute(select(func.count()).select_from(Defect).where(
            Defect.project_id.in_([w.P, w.Q])))).scalar_one() == 0


# ── Each executed effect, and its undo, on the real rows ─────────────────────
async def _undo(w: SimpleNamespace, action_id: uuid.UUID) -> Any:
    """The adapter's rollback as the PATCH runs it (the request session, then its commit)."""
    from app.services.agent_action_adapters.adapter_base import get_adapter

    row = await _row(w, action_id)
    actor = await _user(w, w.lead2_id)
    async with w.sessions() as db:
        outcome = await get_adapter(row.action_type).rollback(
            db, action=row, rollback_payload=row.rollback_payload or {}, actor=actor,
        )
        await db.commit()
    return outcome


async def test_quarantine_executed_then_undone_releases_the_request(world):
    w = world
    action_id = await _approved(w, _quarantine(w, 0))
    assert (await _execute(w, action_id))["status"] == "executed"
    (request,) = await _quarantines(w, w.fp[0])
    assert request.status == "QUARANTINED"

    await _undo(w, action_id)
    (released,) = await _quarantines(w, w.fp[0])
    assert released.status == "RELEASED"
    await _undo(w, action_id)                        # idempotent: a second Undo changes nothing
    assert [r.status for r in await _quarantines(w, w.fp[0])] == ["RELEASED"]


async def test_reassign_executed_then_undone_restores_a_null_previous_assignee(world):
    from app.models.postgres import TestCase

    w = world
    action_id = await _approved(w, _reassign(w, 1))

    async def assignee() -> Any:
        async with w.sessions() as db:
            return (await db.execute(select(TestCase.assigned_to_user_id).where(
                TestCase.id == w.cases[1]))).scalar_one()

    assert await assignee() is None
    assert (await _execute(w, action_id))["status"] == "executed"
    assert await assignee() == w.owner_id
    row = await _row(w, action_id)
    assert row.rollback_payload["previous_assignee_user_id"] is None
    assert row.result_payload["result_link"]["label"] == "Suite Owner"

    await _undo(w, action_id)
    assert await assignee() is None


async def test_defect_executed_then_undone_is_closed_invalid_and_never_filed(world):
    from app.models.postgres import Defect

    w = world
    action_id = await _approved(w, _defect(w))
    assert (await _execute(w, action_id))["status"] == "executed"
    row = await _row(w, action_id)
    async with w.sessions() as db:
        (defect,) = (await db.execute(select(Defect).where(Defect.project_id == w.P))).scalars().all()
    assert str(defect.id) == row.rollback_payload["defect_id"]
    assert defect.cluster_id == "cl_001" and defect.resolution_status == "OPEN"
    assert defect.jira_ticket_id is None and (defect.policy_evaluation or {}).get("jira_project_key") is None
    assert "agent-proposed" in (defect.labels or [])

    await _undo(w, action_id)
    async with w.sessions() as db:
        closed = (await db.execute(select(Defect).where(Defect.id == defect.id))).scalar_one()
    assert closed.resolution_status == "INVALID"
    # A second promotion of the same cluster is no longer refused as a duplicate of an open defect.
    assert (await _execute(w, await _approved(w, _defect(w))))["status"] == "executed"


# ── Budgets: the executor's statements per path, the reaper on 1,000 rows ───
def _acknowledge(w: SimpleNamespace) -> tuple[str, str, dict[str, Any]]:
    return "decision_report_action", "release.blocker.1", {
        "proposing_agent_id": "agent.decision_report.v1", "action_id": "release.blocker.1",
        "title": "Investigate", "owner": "release_owner", "rationale": "failure", "evidence": [],
        "risk": "high", "required_permission": "release_review",
    }


_TARGET_TYPES["decision_report_action"] = "release_review"


async def test_the_executor_statement_count_per_path(world):
    """Statements on the EXECUTOR's sessions (the target services' own sessions excluded)."""
    w = world
    counts: dict[str, int] = {}
    paths = {
        "executed quarantine.apply": _quarantine(w, 0),
        "executed failure.reassign": _reassign(w, 1),
        "executed defect.promote": _defect(w),
        "acknowledged (alias)": _acknowledge(w),
        "refused target_not_in_project": _quarantine(w, 2, test_run_id=str(w.run_q)),
    }
    for name, proposal in paths.items():
        action_id = await _approved(w, proposal)
        with StatementCounter(w.engine) as counter:
            outcome = await _execute(w, action_id)
        assert outcome["status"] in ("executed", "failed"), (name, outcome)
        counts[name] = len(counter.statements)
        with StatementCounter(w.engine) as again:
            assert (await _execute(w, action_id))["status"] in ("already_executed", "not_executable")
        counts[f"re-dispatch after {name}"] = len(again.statements)
    print("W15-GD executor statements per path:", counts)
    assert counts["refused target_not_in_project"] < counts["executed quarantine.apply"]
    assert max(counts.values()) <= 60, counts


async def test_the_reaper_on_a_thousand_stuck_claims(world):
    from app.services import agent_action_ledger_service as service

    w = world
    now = datetime.now(timezone.utc)
    rows = [_ledger_row(w, action_type="quarantine.apply", started_at=now - timedelta(hours=2, seconds=i))
            for i in range(1000)]
    async with w.sessions.begin() as db:
        db.add_all(rows)
    timings: list[float] = []
    reaped = 0
    for _ in range(12):
        started = time.perf_counter()
        async with w.sessions() as db:
            n = await service.reap_stuck_actions(db, now=now)
            await db.commit()
        timings.append(time.perf_counter() - started)
        reaped += n
        if n == 0:
            break
    print(f"W15-GD reaper on 1,000 stuck rows: {len(timings)} passes, "
          f"per pass max {max(timings):.3f}s, total {sum(timings):.3f}s")
    assert reaped == 1000 and len(timings) == 11       # 10 bounded passes of 100, then a no-op
    assert max(timings) < 10.0


