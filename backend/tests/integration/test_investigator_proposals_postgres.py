"""The REAL ``_finalize`` files the Investigator's typed actions on real PostgreSQL (W15 AGT-02, §2.2 test 3).

``_finalize`` opens its own session (``AsyncSessionLocal``), stages the proposals
through ``services/investigation_action_proposals`` inside its terminal
transaction and commits once. Pinned here with real SQL (the unique key, the
``FOR UPDATE`` supersede, ``IS DISTINCT FROM``, the tuple ``IN``):

* suggest → N ``pending_review`` rows, ``approval_required``, the mode in the payload;
  finalize again → still N (idempotent);
* shadow → no row and no supersede;
* a newer investigation on the same run and target → the older PENDING rows are
  ``rejected`` / ``superseded``; an approved one and another project's pending row
  on the same fingerprint are untouched;
* act → rows pending (never auto-approved, no dispatch outbox row);
* an already-quarantined fingerprint / an already-assigned test case / a cluster
  with an OPEN defect → no proposal; a fingerprint the sanitizer would rewrite
  (an all-digit 16-character id) → skipped and logged; a reused key with another
  payload → skipped, the run still completes;
* ``_gather_bundle``'s ONE suite-owner statement (the project's owners of the
  failing suites only).

The fixture awaits ``app.db.postgres.dispose_engine_for_loop()`` before building
its engine and after disposing it, and closes the Redis / Mongo clients the
finalize path bound (``record_agent_run`` mirrors to the pipeline event log).
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SUITE = "checkout"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.agents.investigator import workflow as workflow_mod
    from app.db import postgres as app_postgres
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis
    from app.models.postgres import LaunchStatus, Project, TestRun, TestSuiteOwner, User, UserRole

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow_mod, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(workflow_mod, "_prompt_versions", lambda: {})

    # Tags start with a letter: an all-digit hex run reads as a phone number to the redactor.
    tag = "g" + uuid.uuid4().hex[:9]
    P, Q = uuid.uuid4(), uuid.uuid4()
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    run_p, run_q = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions.begin() as db:
            for pid, label in ((P, "p"), (Q, "q")):
                db.add(Project(id=pid, name=f"w15c-{label}-{tag}", slug=f"w15c-{label}-{tag}", is_active=True))
            for uid, label in ((owner, "owner"), (other_owner, "other")):
                db.add(User(
                    id=uid, email=f"w15c-{label}-{tag}@example.com", username=f"w15c_{label}_{tag}",
                    full_name=f"W15C {label}", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
                ))
            await db.flush()
            for rid, pid in ((run_p, P), (run_q, Q)):
                db.add(TestRun(
                    id=rid, project_id=pid, build_number=f"w15c-{tag}", jenkins_job="w15c",
                    status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=8, failed_tests=8,
                ))
            db.add(TestSuiteOwner(project_id=P, suite_name=SUITE, owner_user_id=owner))
            db.add(TestSuiteOwner(project_id=P, suite_name="payments", owner_user_id=None))
            db.add(TestSuiteOwner(project_id=Q, suite_name="billing", owner_user_id=other_owner))
        yield SimpleNamespace(
            sessions=sessions, engine=engine, tag=tag, P=P, Q=Q, run_p=run_p, run_q=run_q,
            owner=owner, other_owner=other_owner, workflow=workflow_mod,
        )
    finally:
        for statement, params in (
            (text("DELETE FROM projects WHERE id = ANY(:ids)"), {"ids": [P, Q]}),
            (text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [owner, other_owner]}),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, params)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"w15c teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await close_redis()
        await close_mongo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _fp(tag: str, i: int) -> str:
    # Mixed-case hex led by letters (never a phone / card number to the sanitizer).
    return f"fpA{i:02d}{tag[:5]}C3d4"


async def _cases(w, n: int, *, suite: str = SUITE, assigned=None, start: int = 0) -> list[dict]:
    from app.models.postgres import TestCase

    rows = []
    async with w.sessions.begin() as db:
        for i in range(start, start + n):
            cid = uuid.uuid4()
            fingerprint = _fp(w.tag, i)
            db.add(TestCase(
                id=cid, test_run_id=w.run_p, test_name=f"test_checkout_{i:02d}", suite_name=suite,
                status="FAILED", test_fingerprint=fingerprint, assigned_to_user_id=assigned,
            ))
            rows.append({
                "test_case_id": str(cid), "fingerprint": fingerprint,
                "test_name": f"test_checkout_{i:02d}", "suite_name": suite, "status": "FAILED", "category": None,
            })
    return rows


def _bundle(w, failures: list[dict], *, flaky=(), clusters=()) -> dict:
    newly = [{k: f[k] for k in ("fingerprint", "test_name", "suite_name", "status")} for f in failures]
    return {
        "run": {"id": str(w.run_p)},
        "compare": {"new_failures": len(newly), "newly_failed_tests": newly},
        "failures": failures,
        "flaky_fingerprints": sorted(flaky),
        "clusters": list(clusters),
        "suite_owners": {SUITE: str(w.owner)},
    }


async def _investigation(w, mode: str) -> tuple[uuid.UUID, uuid.UUID]:
    from app.models.postgres import AgentInvestigation, AgentPipelineRun

    inv_id = uuid.uuid4()
    pipeline_id = uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:investigation:{inv_id}")
    async with w.sessions.begin() as db:
        db.add(AgentPipelineRun(
            id=pipeline_id, test_run_id=w.run_p, workflow_type="investigation", status="running",
        ))
        await db.flush()
        db.add(AgentInvestigation(
            id=inv_id, project_id=w.P, run_id=w.run_p, scope_type="run", status="running", mode=mode,
            triggered_by="manual", budget={}, spend={"ledger_version": 2}, hypotheses=[],
        ))
    return inv_id, pipeline_id


def _verdict(cause: str, bundle: dict, inv_id: uuid.UUID, mode: str) -> dict:
    from app.agents.investigator.action_plan import plan_structured_actions
    from app.agents.investigator.synthesis import RECOMMENDED_ACTIONS

    return {
        "primary_cause": cause,
        "narrative": "Deterministic narrative.",
        "confidence": 80,
        "recommended_actions": list(RECOMMENDED_ACTIONS[cause]),
        "structured_actions": plan_structured_actions(
            cause, bundle, suite_owners=bundle["suite_owners"],
            investigation_id=str(inv_id), proposing_agent_mode=mode,
        ),
    }


async def _finalize(w, inv_id: uuid.UUID, pipeline_id: uuid.UUID, verdict: dict) -> None:
    await w.workflow._finalize(
        str(inv_id), pipeline_run_id=str(pipeline_id),
        final_state={"hypotheses": [], "verdict": verdict}, error=None, wall_seconds=1.0,
    )


async def _run(w, mode: str, cause: str, bundle: dict) -> tuple[uuid.UUID, uuid.UUID, dict]:
    inv_id, pipeline_id = await _investigation(w, mode)
    verdict = _verdict(cause, bundle, inv_id, mode)
    await _finalize(w, inv_id, pipeline_id, verdict)
    return inv_id, pipeline_id, verdict


async def _ledger(w, *, project=None, pipeline_id=None) -> list:
    from app.models.postgres import AgentActionLedger

    async with w.sessions() as db:
        query = select(AgentActionLedger).where(AgentActionLedger.project_id == (project or w.P))
        if pipeline_id is not None:
            query = query.where(AgentActionLedger.pipeline_run_id == pipeline_id)
        return list((await db.execute(query.order_by(AgentActionLedger.created_at))).scalars().all())


async def _investigation_status(w, inv_id: uuid.UUID) -> str:
    from app.models.postgres import AgentInvestigation

    async with w.sessions() as db:
        return (await db.execute(
            select(AgentInvestigation.status).where(AgentInvestigation.id == inv_id)
        )).scalar_one()


async def _quarantine(w, fingerprint: str, *, project=None, status: str = "QUARANTINED") -> None:
    from app.models.postgres import FlakyQuarantineRequest

    async with w.sessions.begin() as db:
        db.add(FlakyQuarantineRequest(
            project_id=project or w.P, test_fingerprint=fingerprint, test_name="x", suite_name=SUITE,
            status=status, detection_method="manual",
        ))


# ─────────────────────────── suggest / shadow / act ───────────────────────────


async def test_suggest_files_pending_proposals_and_a_second_finalize_doubles_nothing(world):
    from app.services.investigation_action_proposals import (
        investigation_action_key,
        persist_investigation_proposals,
    )

    w = world
    cases = await _cases(w, 3)
    fps = [c["fingerprint"] for c in cases]
    await _quarantine(w, fps[2])  # already quarantined in P → not proposed (D-8)
    bundle = _bundle(w, cases, flaky=fps)
    inv_id, pipeline_id, verdict = await _run(w, "suggest", "known_flaky", bundle)

    assert await _investigation_status(w, inv_id) == "completed"
    rows = await _ledger(w, pipeline_id=pipeline_id)
    assert sorted(r.target_id for r in rows) == fps[:2]
    for row in rows:
        assert row.status == "pending_review"
        assert row.approval_required is True
        assert row.action_type == "quarantine.apply" and row.target_type == "test"
        assert row.test_run_id == w.run_p
        assert row.idempotency_key == investigation_action_key(inv_id, "quarantine.apply", row.target_id)
        assert row.request_payload["proposing_agent_id"] == "investigator"
        assert row.request_payload["proposing_agent_mode"] == "suggest"
        assert row.request_payload["investigation_id"] == str(inv_id)
        assert row.request_payload["test_run_id"] == str(w.run_p)
        assert row.request_payload["test_fingerprint"] == row.target_id
    ids = sorted(r.id for r in rows)

    # A replayed finalize returns before the block (the row is terminal) …
    await _finalize(w, inv_id, pipeline_id, verdict)
    # … and a resumed run reaching the block with the same keys gets the same rows back.
    from app.models.postgres import AgentInvestigation

    async with w.sessions.begin() as db:
        inv = (await db.execute(select(AgentInvestigation).where(AgentInvestigation.id == inv_id))).scalar_one()
        again = await persist_investigation_proposals(
            db, investigation=inv, pipeline_run_id=pipeline_id, actions=verdict["structured_actions"],
        )
    assert sorted(again) == ids
    assert sorted(r.id for r in await _ledger(w, pipeline_id=pipeline_id)) == ids


async def test_shadow_files_nothing_and_supersedes_nothing(world):
    w = world
    cases = await _cases(w, 2)
    bundle = _bundle(w, cases, flaky=[c["fingerprint"] for c in cases])
    _inv, first_pipeline, _v = await _run(w, "suggest", "known_flaky", bundle)
    shadow_inv, shadow_pipeline, verdict = await _run(w, "shadow", "known_flaky", bundle)

    assert verdict["structured_actions"]  # the plan still ran: the cockpit shows it
    assert await _investigation_status(w, shadow_inv) == "completed"
    assert await _ledger(w, pipeline_id=shadow_pipeline) == []
    assert {r.status for r in await _ledger(w, pipeline_id=first_pipeline)} == {"pending_review"}


async def test_act_files_pending_rows_and_never_approves_one(world):
    from app.models.postgres import AgentActionDispatchOutbox

    w = world
    cases = await _cases(w, 2)
    bundle = _bundle(w, cases, flaky=[c["fingerprint"] for c in cases])
    _inv, pipeline_id, _v = await _run(w, "act", "known_flaky", bundle)

    rows = await _ledger(w, pipeline_id=pipeline_id)
    assert len(rows) == 2
    assert {(r.status, r.approval_required) for r in rows} == {("pending_review", True)}
    assert {r.request_payload["proposing_agent_mode"] for r in rows} == {"act"}
    async with w.sessions() as db:
        outbox = (await db.execute(
            select(AgentActionDispatchOutbox).where(AgentActionDispatchOutbox.action_id.in_([r.id for r in rows]))
        )).scalars().all()
    assert outbox == []


# ─────────────────────────── superseding (D-10) ───────────────────────────


async def test_a_newer_investigation_supersedes_older_pending_rows_of_its_project_only(world):
    from app.services.agent_action_ledger_service import create_action_proposal

    w = world
    cases = await _cases(w, 2)
    fps = [c["fingerprint"] for c in cases]
    bundle = _bundle(w, cases, flaky=fps)
    # Another project's pending proposal on the SAME fingerprint.
    async with w.sessions.begin() as db:
        foreign, _ = await create_action_proposal(
            db, project_id=w.Q, test_run_id=w.run_q, action_type="quarantine.apply", target_type="test",
            target_id=fps[0], idempotency_key=f"w15c-foreign-{w.tag}", request_payload={"title": "foreign"},
        )
        foreign_id = foreign.id

    _first, first_pipeline, _v = await _run(w, "suggest", "known_flaky", bundle)
    first_rows = await _ledger(w, pipeline_id=first_pipeline)
    approved = next(r for r in first_rows if r.target_id == fps[1])
    async with w.sessions.begin() as db:
        await db.execute(
            text("UPDATE agent_action_ledger SET status = 'approved' WHERE id = :id"), {"id": approved.id},
        )

    _second, second_pipeline, _v = await _run(w, "suggest", "known_flaky", bundle)

    by_id = {r.id: r for r in await _ledger(w, pipeline_id=first_pipeline)}
    superseded = next(r for r in by_id.values() if r.target_id == fps[0])
    assert (superseded.status, superseded.error_code) == ("rejected", "superseded")
    assert superseded.execution_completed_at is not None
    assert by_id[approved.id].status == "approved"          # history is never rewritten
    assert by_id[approved.id].error_code is None
    second = await _ledger(w, pipeline_id=second_pipeline)
    assert sorted((r.target_id, r.status) for r in second) == [(fps[0], "pending_review"), (fps[1], "pending_review")]
    (foreign_row,) = [r for r in await _ledger(w, project=w.Q) if r.id == foreign_id]
    assert (foreign_row.status, foreign_row.error_code) == ("pending_review", None)


# ─────────────────────────── already done (D-8), the sanitizer (R-9), conflicts ───────────────────────────


async def test_already_done_targets_are_not_proposed(world):
    from app.models.postgres import Defect

    w = world
    free = await _cases(w, 1)
    taken = await _cases(w, 1, assigned=w.owner, start=1)
    cluster = {"failure_cluster_id": str(uuid.uuid4()), "cluster_id": f"cl_{w.tag}", "label": "NPE", "size": 2}
    async with w.sessions.begin() as db:
        db.add(Defect(project_id=w.P, cluster_id=cluster["cluster_id"], resolution_status="OPEN", title="open"))
    bundle = _bundle(w, free + taken, clusters=[cluster])
    _inv, pipeline_id, verdict = await _run(w, "suggest", "regression", bundle)

    planned = sorted(a["action_type"] for a in verdict["structured_actions"])
    assert planned == ["defect.promote", "failure.reassign", "failure.reassign"]
    rows = await _ledger(w, pipeline_id=pipeline_id)
    assert [(r.action_type, r.target_id) for r in rows] == [("failure.reassign", free[0]["test_case_id"])]
    assert rows[0].request_payload["assignee_user_id"] == str(w.owner)


async def test_an_open_defect_in_another_project_does_not_block_this_one(world):
    from app.models.postgres import Defect

    w = world
    cases = await _cases(w, 2)
    cluster = {"failure_cluster_id": str(uuid.uuid4()), "cluster_id": f"cl_{w.tag}", "label": "NPE", "size": 2}
    async with w.sessions.begin() as db:
        db.add(Defect(project_id=w.Q, cluster_id=cluster["cluster_id"], resolution_status="OPEN", title="q"))
    _inv, pipeline_id, _v = await _run(w, "suggest", "regression", _bundle(w, cases, clusters=[cluster]))
    rows = await _ledger(w, pipeline_id=pipeline_id)
    assert ("defect.promote", cluster["cluster_id"]) in {(r.action_type, r.target_id) for r in rows}


async def test_a_fingerprint_the_sanitizer_would_rewrite_is_skipped_and_logged(world, monkeypatch):
    from app.services import investigation_action_proposals as proposals

    logged: list[dict] = []
    monkeypatch.setattr(proposals, "logger", SimpleNamespace(
        warning=lambda event, **kw: logged.append({"event": event, **kw}),
        info=lambda event, **kw: logged.append({"event": event, **kw}),
    ))
    w = world
    cases = await _cases(w, 1)
    digits = "4111111111111111"  # 16 digits: the redactor masks it as a card number
    cases.append({**cases[0], "fingerprint": digits, "test_name": "test_digits"})
    bundle = _bundle(w, cases, flaky=[cases[0]["fingerprint"], digits])
    _inv, pipeline_id, verdict = await _run(w, "suggest", "known_flaky", bundle)

    assert digits in {a["target_id"] for a in verdict["structured_actions"]}
    assert [r.target_id for r in await _ledger(w, pipeline_id=pipeline_id)] == [cases[0]["fingerprint"]]
    (skip,) = [entry for entry in logged if entry["event"] == "investigator_action_payload_redacted"]
    assert skip["action_type"] == "quarantine.apply"
    assert "test_fingerprint" in skip["changed_fields"]
    assert digits not in repr(logged)  # ids of a mangled payload are never logged


async def test_a_reused_key_with_another_payload_is_skipped_and_the_run_completes(world):
    from app.services.agent_action_ledger_service import create_action_proposal
    from app.services.investigation_action_proposals import investigation_action_key

    w = world
    cases = await _cases(w, 2)
    fps = [c["fingerprint"] for c in cases]
    inv_id, pipeline_id = await _investigation(w, "suggest")
    async with w.sessions.begin() as db:
        await create_action_proposal(
            db, project_id=w.P, test_run_id=w.run_p, action_type="quarantine.apply", target_type="test",
            target_id=fps[0], idempotency_key=investigation_action_key(inv_id, "quarantine.apply", fps[0]),
            request_payload={"title": "something else entirely"},
        )
    verdict = _verdict("known_flaky", _bundle(w, cases, flaky=fps), inv_id, "suggest")
    await _finalize(w, inv_id, pipeline_id, verdict)

    assert await _investigation_status(w, inv_id) == "completed"
    assert [r.target_id for r in await _ledger(w, pipeline_id=pipeline_id)] == [fps[1]]


# ─────────────────────────── the bundle's suite owners ───────────────────────────


async def test_gather_bundle_reads_the_projects_owners_of_the_failing_suites(world):
    from app.models.postgres import AgentInvestigation

    w = world
    await _cases(w, 2)
    await _cases(w, 1, suite="payments", start=2)     # an owner row with no owner
    await _cases(w, 1, suite="billing", start=3)      # owned only in ANOTHER project
    inv_id, _pipeline = await _investigation(w, "suggest")
    async with w.sessions() as db:
        inv = (await db.execute(select(AgentInvestigation).where(AgentInvestigation.id == inv_id))).scalar_one()
    bundle = await w.workflow._gather_bundle(inv)

    assert {f["suite_name"] for f in bundle["failures"]} == {SUITE, "payments", "billing"}
    assert bundle["suite_owners"] == {SUITE: str(w.owner)}
