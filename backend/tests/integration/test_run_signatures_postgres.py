"""``GET /runs/failure-signatures`` on real PostgreSQL (W07 FAIL-03).

Builds are grouped by the error their failing tests share, not by their
``(passed, failed, total)`` counts. Fixtures in one project:

* A -- 12 rows ``AssertionError: expected N but was M`` (the numbers vary) and
  3 rows ``Timeout after 30s``;
* B -- the SAME counts as A (15 failing) but every row ``NullPointerException at Foo``;
* C -- 5 failing rows with a NULL or blank message;
* D -- a FAILED build with no FAILED/BROKEN row;
* E -- 600 failing rows of one message (above the 500-row read cap).

Also: authorization (a foreign and an unknown id answer alike), and the read
is two statements on the request session.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.run_signature_service import batch_run_signatures

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
        text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, 'w07a-sigs', :slug, :a)"),
        {"id": pid, "slug": f"w07a-s-{pid.hex}", "a": active},
    )
    return pid


async def _run(conn, project_id, build: str, failed: int) -> uuid.UUID:
    rid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, ingestion_source, "
        "passed_tests, failed_tests, total_tests) VALUES (:id, :p, :b, 'w07a', 'FAILED', 'unknown', 5, :f, :t)"
    ), {"id": rid, "p": project_id, "b": build, "f": failed, "t": failed + 5})
    return rid


async def _cases(conn, run_id, rows: list[tuple[str, str | None]], status: str = "FAILED") -> None:
    for name, message in rows:
        await conn.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_fingerprint, test_name, status, error_message) "
            "VALUES (gen_random_uuid(), :r, md5(:n), :n, :s, :m)"
        ), {"r": run_id, "n": name, "s": status, "m": message})


@pytest.fixture
async def world(engine):
    created: list[uuid.UUID] = []
    async with engine.begin() as conn:
        p = await _project(conn)
        q = await _project(conn)
        created += [p, q]
        a = await _run(conn, p, "A", 15)
        await _cases(conn, a, [
            (f"test_count_{i:02d}", f"AssertionError: expected {i} but was {i + 1}\n  at line {i}")
            for i in range(12)
        ] + [(f"test_slow_{i}", "Timeout after 30s") for i in range(3)])
        await _cases(conn, a, [("test_passes", None)], status="PASSED")
        b = await _run(conn, p, "B", 15)
        await _cases(conn, b, [(f"test_npe_{i:02d}", "NullPointerException at Foo") for i in range(15)])
        c = await _run(conn, p, "C", 5)
        await _cases(conn, c, [("test_c_0", None), ("test_c_1", ""), ("test_c_2", "   "),
                               ("test_c_3", None), ("test_c_4", "\n")])
        d = await _run(conn, p, "D", 0)
        await _cases(conn, d, [("test_d_ok", None)], status="PASSED")
        e = await _run(conn, p, "E", 600)
        await conn.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_fingerprint, test_name, status, error_message) "
            "SELECT gen_random_uuid(), :r, md5('e' || g), 'test_e_' || lpad(g::text, 4, '0'), "
            "CASE WHEN g % 2 = 0 THEN 'FAILED' ELSE 'BROKEN' END, 'ConnectionError: refused' "
            "FROM generate_series(1, 600) AS g"
        ), {"r": e})
        tie = await _run(conn, p, "TIE", 4)
        await _cases(conn, tie, [("test_t_0", "zeta failure"), ("test_t_1", "zeta failure"),
                                 ("test_t_2", "alpha failure"), ("test_t_3", "alpha failure")])
        q_run = await _run(conn, q, "Q", 1)
        await _cases(conn, q_run, [("test_q", "Foreign error")])
    yield {"p": p, "a": a, "b": b, "c": c, "d": d, "e": e, "tie": tie, "q_run": q_run}
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
                           {"ids": [str(i) for i in created]})


async def _batch(engine, ids, accessible):
    async with AsyncSession(engine) as db:
        return await batch_run_signatures(db, ids, accessible=accessible)


async def test_builds_are_grouped_by_their_real_error(engine, world):
    out = await _batch(engine, [world[k] for k in ("a", "b", "c", "d", "e")], {world["p"]})
    assert out["unavailable"] == [] and out["max_ids"] == 50 and out["rows_per_run_cap"] == 500
    a, b, c, d, e = out["items"]

    assert a["failing_tests"] == 15 and a["read"] == 15 and a["capped"] is False
    assert a["dominant"] == "assertionerror: expected # but was #"
    first, second = a["signatures"]
    assert (first["count"], first["test_count"]) == (12, 12)
    assert first["label"].startswith("AssertionError: expected ") and "\n" not in first["label"]
    assert first["tests"] == ["test_count_00", "test_count_01", "test_count_02"]
    assert (second["signature"], second["count"], second["label"]) == ("timeout after #s", 3, "Timeout after 30s")

    # Same pass / fail / total counts as A, a different error: a different group.
    assert b["failing_tests"] == a["failing_tests"]
    assert b["dominant"] == "nullpointerexception at foo" != a["dominant"]

    assert c["dominant"] == "__NO_MESSAGE__"
    assert c["signatures"] == [{"signature": "__NO_MESSAGE__", "label": "", "count": 5, "test_count": 5,
                                "tests": ["test_c_0", "test_c_1", "test_c_2"]}]

    assert d == {"run_id": str(world["d"]), "failing_tests": 0, "read": 0, "capped": False,
                 "dominant": None, "signatures": []}

    assert (e["failing_tests"], e["read"], e["capped"]) == (600, 500, True)
    assert [s["count"] for s in e["signatures"]] == [500]


async def test_equal_counts_order_by_signature(engine, world):
    (tie,) = (await _batch(engine, [world["tie"]], None))["items"]
    assert [(s["signature"], s["count"]) for s in tie["signatures"]] == [("alpha failure", 2), ("zeta failure", 2)]
    assert tie["dominant"] == "alpha failure"


async def test_foreign_and_unknown_ids_answer_alike(engine, world):
    stranger = uuid.uuid4()
    member = await _batch(engine, [world["a"], world["q_run"], stranger], {world["p"]})
    assert [item["run_id"] for item in member["items"]] == [str(world["a"])]
    assert member["unavailable"] == [str(world["q_run"]), str(stranger)]
    admin = await _batch(engine, [world["q_run"], stranger], None)
    assert [item["run_id"] for item in admin["items"]] == [str(world["q_run"])]
    assert admin["unavailable"] == [str(stranger)]


async def test_two_statements_on_the_request_session(engine, world):
    statements: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async with AsyncSession(engine) as db:
        await db.execute(text("SELECT 1"))
        event.listen(engine.sync_engine, "before_cursor_execute", _before)
        try:
            await batch_run_signatures(db, [world[k] for k in ("a", "b", "c", "d", "e")], accessible={world["p"]})
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _before)
    assert len(statements) == 2
