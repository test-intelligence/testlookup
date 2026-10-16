"""``GET /runs/ai-verdicts`` on real PostgreSQL (W07 FAIL-04).

The batch read answers each run's verdict inputs in SQL (jsonb paths, a
JavaScript-truthiness CASE, leaves returned as JSON text) and assembles the
slim payload in Python. These tests bind that SQL to the Python reference
``run_verdict.verdict_inputs_of``: every ``CASES`` payload of
``tests/test_run_verdict.py`` is stored as a snapshot and must come back as
exactly ``verdict_inputs_of(payload)``. Also: a snapshot at a superseded
``schema_version`` is not servable; a stale one is; a finished pipeline without
a snapshot is ``analysed``; the review state equals ``review_envelope_for_run``'s
run by run; unknown, foreign and soft-deleted runs are ``unavailable`` alike;
and the read is exactly two statements (one when no run has a snapshot).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION
from app.services.review_envelope import review_envelope_for_run
from app.services.run_verdict import verdict_inputs_of
from app.services.run_verdict_service import batch_verdict_inputs
from tests.test_run_verdict import CASES

pytest.importorskip("asyncpg")

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield eng
    await eng.dispose()


async def _project(conn, *, active: bool = True) -> uuid.UUID:
    pid = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, 'w07a-verdicts', :slug, :a)"),
        {"id": pid, "slug": f"w07a-v-{pid.hex}", "a": active},
    )
    return pid


async def _run(conn, project_id: uuid.UUID, build: str) -> uuid.UUID:
    rid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, ingestion_source, "
        "failed_tests, total_tests) VALUES (:id, :p, :b, 'w07a', 'FAILED', 'unknown', 1, 2)"
    ), {"id": rid, "p": project_id, "b": build})
    return rid


async def _snapshot(conn, run_id, payload, *, version=CURRENT_SCHEMA_VERSION, stale=False) -> None:
    await conn.execute(text(
        "INSERT INTO run_intelligence_snapshots (id, run_id, schema_version, payload, fallback_used, stale) "
        "VALUES (gen_random_uuid(), :r, :v, CAST(:p AS json), false, :s)"
    ), {"r": run_id, "v": version, "p": json.dumps(payload), "s": stale})


async def _pipeline(conn, run_id, status: str) -> None:
    await conn.execute(text(
        "INSERT INTO agent_pipeline_runs (id, test_run_id, workflow_type, status) "
        "VALUES (gen_random_uuid(), :r, 'offline', :s)"
    ), {"r": run_id, "s": status})


async def _review(conn, project_id, run_id, state, *, workflow=None, minutes_ago=0) -> None:
    settled = "now()" if state in ("accepted", "rejected") else "NULL"
    reason = "'other'" if state == "rejected" else "NULL"
    await conn.execute(text(
        "INSERT INTO review_requests (id, project_id, kind, subject_type, subject_id, test_run_id, "
        "workflow_type, state, created_by, reviewed_at, ai_disclaimer_version, reason_code, created_at) "
        f"VALUES (gen_random_uuid(), :p, 'report', 'summary', :subj, :r, :w, :s, 'system', {settled}, 'v1', "
        f"{reason}, now() - make_interval(mins => :m))"
    ), {"p": project_id, "subj": f"summary-{uuid.uuid4().hex}", "r": run_id, "w": workflow, "s": state,
        "m": minutes_ago})


@pytest.fixture
async def world(engine):
    created: list[uuid.UUID] = []
    async with engine.begin() as conn:
        p = await _project(conn)
        q = await _project(conn)
        dead = await _project(conn, active=False)
        created += [p, q, dead]
        cases = []
        for index, case in enumerate(CASES):
            rid = await _run(conn, p, f"case-{index}")
            await _snapshot(conn, rid, case["full"])
            cases.append((rid, case))
        old = await _run(conn, p, "old-version")
        await _snapshot(conn, old, CASES[0]["full"], version=CURRENT_SCHEMA_VERSION - 1)
        stale = await _run(conn, p, "stale")
        await _snapshot(conn, stale, CASES[0]["full"], stale=True)
        analysed = await _run(conn, p, "analysed-no-snapshot")
        await _pipeline(conn, analysed, "completed")
        running = await _run(conn, p, "pipeline-running")
        await _pipeline(conn, running, "running")
        bare = await _run(conn, p, "never-analysed")
        q_run = await _run(conn, q, "foreign")
        await _snapshot(conn, q_run, CASES[0]["full"])
        dead_run = await _run(conn, dead, "soft-deleted")
        await _snapshot(conn, dead_run, CASES[0]["full"])
    yield {
        "p": p, "q": q, "cases": cases, "old": old, "stale": stale, "analysed": analysed,
        "running": running, "bare": bare, "q_run": q_run, "dead_run": dead_run,
    }
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
                           {"ids": [str(i) for i in created]})


async def _batch(engine, ids, accessible):
    async with AsyncSession(engine) as db:
        return await batch_verdict_inputs(db, ids, accessible=accessible)


async def test_every_case_payload_comes_back_as_its_python_slim(engine, world):
    ids = [rid for rid, _ in world["cases"]]
    out = await _batch(engine, ids, {world["p"]})
    assert out["unavailable"] == [] and out["max_ids"] == 25
    assert [item["run_id"] for item in out["items"]] == [str(i) for i in ids]
    for item, (_, case) in zip(out["items"], world["cases"]):
        assert item["snapshot"] is True and item["stale"] is False, case["name"]
        assert item["verdict_inputs"] == verdict_inputs_of(case["full"]) == case["slim"], case["name"]
        assert item["review"] == {"state": "pending_review"}, case["name"]
        assert item["generated_at"] and item["generated_at"].endswith("+00:00")


async def test_object_leaves_and_array_payloads_that_cases_do_not_cover(engine, world):
    # A dict top_analyses with a numeric length, an array summary, a leaf that is an object.
    payloads = [
        {"top_analyses": {"length": 2}, "structured_summary": [1]},
        {"top_analyses": {"length": "2"}, "release_decision": [], "intelligence_available": "x"},
        {"structured_summary": {"decision_intelligence": {"verification": ["passed"],
                                                           "release_decision": {"recommendation": {"v": 1}}},
                                "latest_decision_attempt": False,
                                "decision_report_verification": 0.0}},
        {"top_analyses": "", "intelligence_available": 0.5, "release_decision": "GO"},
        # json holds a \u0000 escape that jsonb refuses: the batch must not fail on it.
        {"top_analyses": [{"error": "boom\u0000tail \\u0000 literal"}],
         "release_decision": {"recommendation": "GO"}},
    ]
    async with engine.begin() as conn:
        ids = []
        for index, payload in enumerate(payloads):
            rid = await _run(conn, world["p"], f"extra-{index}")
            await _snapshot(conn, rid, payload)
            ids.append(rid)
    out = await _batch(engine, ids, None)
    assert [item["verdict_inputs"] for item in out["items"]] == [verdict_inputs_of(p) for p in payloads]


async def test_schema_version_staleness_and_pipelines(engine, world):
    ids = [world["old"], world["stale"], world["analysed"], world["running"], world["bare"]]
    out = await _batch(engine, ids, {world["p"]})
    old, stale, analysed, running, bare = out["items"]
    assert old == {"run_id": str(world["old"]), "snapshot": False, "stale": None, "generated_at": None,
                   "analysed": False, "verdict_inputs": None, "review": None}
    assert stale["snapshot"] is True and stale["stale"] is True
    assert stale["verdict_inputs"] == CASES[0]["slim"]
    assert analysed["snapshot"] is False and analysed["analysed"] is True
    assert running["analysed"] is False, "a running pipeline is not a finished analysis"
    assert bare["snapshot"] is False and bare["analysed"] is False


async def test_review_states_equal_the_per_run_envelope(engine, world):
    accepted, superseded_pair, investigation, nothing = [rid for rid, _ in world["cases"][:4]]
    async with engine.begin() as conn:
        await _review(conn, world["p"], accepted, "accepted")
        await _review(conn, world["p"], superseded_pair, "accepted", minutes_ago=10)
        await conn.execute(text(
            "UPDATE review_requests SET state = 'superseded' WHERE test_run_id = :r"
        ), {"r": superseded_pair})
        await _review(conn, world["p"], superseded_pair, "pending_review")
        await _review(conn, world["p"], investigation, "accepted", workflow="investigation")
    ids = [accepted, superseded_pair, investigation, nothing]
    out = await _batch(engine, ids, {world["p"]})
    states = [item["review"]["state"] for item in out["items"]]
    assert states == ["accepted", "pending_review", "pending_review", "pending_review"]
    async with AsyncSession(engine) as db:
        per_run = [(await review_envelope_for_run(db, rid)).state for rid in ids]
    assert states == per_run


async def test_unknown_foreign_and_soft_deleted_runs_are_unavailable_alike(engine, world):
    mine = world["cases"][0][0]
    stranger = uuid.uuid4()
    member = await _batch(engine, [mine, world["q_run"], stranger, world["dead_run"]], {world["p"]})
    assert [item["run_id"] for item in member["items"]] == [str(mine)]
    assert member["unavailable"] == [str(world["q_run"]), str(stranger), str(world["dead_run"])]

    admin = await _batch(engine, [mine, world["q_run"], stranger, world["dead_run"]], None)
    assert [item["run_id"] for item in admin["items"]] == [str(mine), str(world["q_run"])]
    assert admin["unavailable"] == [str(stranger), str(world["dead_run"])]

    nobody = await _batch(engine, [mine], set())
    assert nobody == {"items": [], "unavailable": [str(mine)], "max_ids": 25}


async def _count_statements(engine, ids, accessible) -> int:
    statements: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async with AsyncSession(engine) as db:
        await db.execute(text("SELECT 1"))  # connect first: dialect bootstrap is not the read's
        event.listen(engine.sync_engine, "before_cursor_execute", _before)
        try:
            await batch_verdict_inputs(db, ids, accessible=accessible)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _before)
    return len(statements)


async def test_twenty_five_runs_with_snapshots_take_two_statements(engine, world):
    async with engine.begin() as conn:
        extra = []
        for index in range(25 - len(world["cases"])):
            rid = await _run(conn, world["p"], f"count-{index}")
            await _snapshot(conn, rid, CASES[index]["full"])
            extra.append(rid)
    ids = [rid for rid, _ in world["cases"]] + extra
    assert len(ids) == 25
    assert await _count_statements(engine, ids, {world["p"]}) == 2


async def test_runs_without_snapshots_take_one_statement(engine, world):
    ids = [world["analysed"], world["bare"], world["old"]]
    assert await _count_statements(engine, ids, None) == 1
