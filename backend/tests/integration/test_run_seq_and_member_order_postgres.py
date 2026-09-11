"""Re-audit N17 + N18 on real PostgreSQL, under a forced generic plan.

N17. "Run #N" (``fetch_run_seq_map``) numbers runs within each (project,
normalised suite) partition over the partition's whole history. It windowed an
OR of the page's pairs, which reached the rows through a BitmapOr -- unordered
-- so every page sorted each suite's entire history. Now one UNION ALL branch
per pair reads migration 0169's index already in window order: no Sort. The
index INCLUDEs primary_suite_name so the read is index-only.

N18. The multi-project and admin run lists could not use 0167's index (it
leads with project_id): a Seq Scan and a Sort of every run. The admin list
walks migration 0170's global natural-order index to its LIMIT; a member of
several projects gets one 0167-ordered branch per project, each stopped at the
deepest row the page can need.

Every plan is taken from the statement the service actually executes,
PREPAREd with ``plan_cache_mode = force_generic_plan`` (what asyncpg's
prepared statements can settle on). The numbers and the page order are
checked against reference SQL written from the definitions.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import runs_service

pytestmark = pytest.mark.integration

SEQ_INDEX = "ix_test_runs_project_suite_hash_seq"
# 0169's first index, which overflowed btree for long multibyte suite names
# (review R-B45-D-1); 0172 drops it.
OLD_SEQ_INDEX = "ix_test_runs_project_suite_natural_seq"
GLOBAL_INDEX = "ix_test_runs_natural_build"
PROJECT_INDEX = "ix_test_runs_project_natural_build"

KEY = (
    "CASE WHEN build_number ~ '[0-9]' THEN CAST(string_to_array(trim(regexp_replace("
    "build_number, '[^0-9]+', ' ', 'g')), ' ') AS NUMERIC[]) END"
)
SUITE = "lower(trim(coalesce(primary_suite_name, '')))"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    import app.db.postgres as app_postgres

    await app_postgres.dispose_engine_for_loop()
    eng = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield eng
    await eng.dispose()
    await app_postgres.dispose_engine_for_loop()


_INSERT = (
    "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, "
    "ingestion_source, failed_tests, total_tests, primary_suite_name, created_at) "
)


@pytest.fixture
async def seeded(engine):
    """Three projects. The first has two big suites -- one written two ways that
    normalise alike -- a NULL-suite partition, and build numbers the natural key
    has to work for. VACUUM ANALYZE so the planner sees the real shape."""
    tag = uuid.uuid4().hex[:8]
    projects = [uuid.uuid4() for _ in range(3)]
    async with engine.begin() as conn:
        for i, pid in enumerate(projects):
            await conn.execute(
                text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, :n, :n, true)"),
                {"id": pid, "n": f"n1718-{tag}-{i}"},
            )
        big = projects[0]
        await conn.execute(text(
            _INSERT + "SELECT gen_random_uuid(), :p, 'api-' || g, 'n17', 'PASSED', 'unknown', 0, 1, "
            "CASE WHEN g % 2 = 0 THEN 'API' ELSE '  api ' END, now() - (g || ' minutes')::interval "
            "FROM generate_series(1, 2500) g"
        ), {"p": big})
        await conn.execute(text(
            _INSERT + "SELECT gen_random_uuid(), :p, 'ui-' || g, 'n17', 'FAILED', 'unknown', 1, 1, "
            "'UI', now() - (g || ' minutes')::interval FROM generate_series(1, 2500) g"
        ), {"p": big})
        for build in ("release", "9.9.9", "10.0.1", "b" + "9" * 25, "ui-2", "ui-10"):
            await conn.execute(text(
                # Its own job: (project, build_number, job) is unique, and 'ui-2'
                # is also a generated UI build above.
                _INSERT + "VALUES (gen_random_uuid(), :p, :b, 'n17-odd', 'PASSED', 'unknown', 0, 1, NULL, now())"
            ), {"p": big, "b": build})
        for pid in projects[1:]:
            await conn.execute(text(
                _INSERT + "SELECT gen_random_uuid(), :p, 'm-' || g, 'n18', 'PASSED', 'unknown', 0, 1, "
                "'Smoke', now() - (g || ' minutes')::interval FROM generate_series(1, 800) g"
            ), {"p": pid})
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM (ANALYZE) test_runs"))
    yield projects
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM test_runs WHERE project_id = ANY(:p)"), {"p": projects})
        await conn.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": projects})


async def _captured(engine, call) -> list:
    captured = []
    async with AsyncSession(engine) as db:
        real = db.execute

        async def spy(statement, *args, **kwargs):
            captured.append(statement)
            return await real(statement, *args, **kwargs)

        db.execute = spy
        result = await call(db)
    return captured, result


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


async def _generic_plan(engine, statement, settings_off: tuple[str, ...] = ()) -> dict:
    """The generic plan of ``statement``; ``settings_off`` planner knobs apply
    to this EXPLAIN only and are reset after."""
    compiled = statement.compile(
        dialect=pg_asyncpg.dialect(), compile_kwargs={"render_postcompile": True}
    )
    params = compiled.construct_params()
    args = ", ".join(_literal(params[name]) for name in compiled.positiontup)
    async with engine.connect() as conn:
        driver = (await conn.get_raw_connection()).driver_connection
        await driver.execute("SET plan_cache_mode = force_generic_plan")
        for setting in settings_off:
            await driver.execute(f"SET {setting} = off")
        await driver.execute(f"PREPARE n1718_q AS {compiled}")
        try:
            call = f"EXECUTE n1718_q({args})" if args else "EXECUTE n1718_q"
            raw = await driver.fetchval(f"EXPLAIN (FORMAT JSON) {call}")
        finally:
            await driver.execute("DEALLOCATE n1718_q")
            await driver.execute("RESET plan_cache_mode")
            for setting in settings_off:
                await driver.execute(f"RESET {setting}")
    return (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _run_scans(plan: dict) -> list[dict]:
    return [n for n in _nodes(plan) if n.get("Relation Name") == "test_runs"]


def _id(item) -> str:
    return str(item["id"] if isinstance(item, dict) else item.id)


def _items_statement(statements):
    for statement in statements:
        sql = str(statement.compile(dialect=pg_asyncpg.dialect()))
        if "ORDER BY" in sql and "LIMIT" in sql and "row_number" not in sql and "count(" not in sql:
            return statement
    raise AssertionError("list_project_runs issued no ordered, LIMITed items SELECT")


async def test_both_indexes_exist_and_are_valid(engine):
    async with engine.connect() as conn:
        rows = dict((await conn.execute(text(
            "SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname IN (:a, :b)"
        ), {"a": SEQ_INDEX, "b": GLOBAL_INDEX})).all())
        old = (await conn.execute(text(
            "SELECT count(*) FROM pg_class WHERE relname = :old AND relkind = 'i'"
        ), {"old": OLD_SEQ_INDEX})).scalar_one()
    assert rows == {SEQ_INDEX: True, GLOBAL_INDEX: True}, rows
    assert old == 0, f"{OLD_SEQ_INDEX} (the overflowing first 0169 index) is still there"


async def _page_ids(engine, project) -> list[uuid.UUID]:
    """A /runs page that spans three partitions: API, UI and the NULL suite.

    Each suite's oldest rows are its highest builds ('api-2500' is 2500
    minutes old), so the page holds each partition's top numbers."""
    async with engine.connect() as conn:
        return list((await conn.execute(text(
            "(SELECT id FROM test_runs WHERE project_id = :p AND primary_suite_name = 'API' "
            " ORDER BY created_at LIMIT 7) "
            "UNION ALL (SELECT id FROM test_runs WHERE project_id = :p AND primary_suite_name = '  api ' "
            " ORDER BY created_at LIMIT 3) "
            "UNION ALL (SELECT id FROM test_runs WHERE project_id = :p AND primary_suite_name = 'UI' "
            " ORDER BY created_at LIMIT 7) "
            "UNION ALL (SELECT id FROM test_runs WHERE project_id = :p AND primary_suite_name IS NULL)"
        ), {"p": project})).scalars())


async def test_run_numbers_read_the_index_in_order_under_a_generic_plan(engine, seeded):
    ids = await _page_ids(engine, seeded[0])
    statements, _ = await _captured(engine, lambda db: runs_service.fetch_run_seq_map(db, ids))
    statement = statements[-1]

    # 1. As planned, under default costs: every branch reads 0169's index for
    # its own (project, suite) pair -- no BitmapOr of an OR over the pairs, the
    # shape N17 removed. What it does NOT show: that no Sort runs. Since the
    # index stores the md5 of the suite (review R-B45-D-1), a branch cannot be
    # index-only, and reading a whole partition through a bitmap and sorting it
    # can be the cheaper plan -- measured 64.9 ms for 4 x 10,000 runs, against
    # 87.6 ms for the pre-N17 BitmapOr and Sort.
    plan = await _generic_plan(engine, statement)
    rendered = json.dumps(plan)[:4000]
    # WHICH index the default plan reads is a cost choice by table size, and
    # is deliberately not asserted: on CI's fresh, small table it reads each
    # partition through ix_test_runs_project_environment and filters on the
    # suite hash; on a large one it takes this index (lead review of the N17
    # test, the same trap as the N18 join-back). Only the shape is the code's.
    assert not any(n["Node Type"] == "BitmapOr" for n in _nodes(plan)), rendered
    appends = [n for n in _nodes(plan) if n["Node Type"] == "Append"]
    assert len(appends) == 1 and len(appends[0]["Plans"]) == 3, rendered  # API, UI, NULL suite

    # 2. The index CAN deliver the window's order: with sorting and bitmap scans
    # priced out for this EXPLAIN only, each branch is a plain Index Scan on it
    # with no Sort above. A key or direction that did not match the window
    # would leave a Sort here whatever the settings.
    ordered = await _generic_plan(
        engine, statement, settings_off=("enable_sort", "enable_bitmapscan", "enable_seqscan")
    )
    ordered_rendered = json.dumps(ordered)[:4000]
    assert not any(n["Node Type"] == "Sort" for n in _nodes(ordered)), ordered_rendered
    ordered_scans = _run_scans(ordered)
    assert ordered_scans and all(
        n["Node Type"] in ("Index Scan", "Index Only Scan") and n.get("Index Name") == SEQ_INDEX
        for n in ordered_scans
    ), ordered_rendered


async def test_run_numbers_match_the_partitioned_definition(engine, seeded):
    ids = await _page_ids(engine, seeded[0])
    async with AsyncSession(engine) as db:
        got = await runs_service.fetch_run_seq_map(db, ids)
    async with engine.connect() as conn:
        reference = dict((await conn.execute(text(
            f"SELECT CAST(id AS text), rn FROM (SELECT id, row_number() OVER ("
            f"PARTITION BY project_id, {SUITE} ORDER BY ({KEY}) ASC NULLS LAST, "
            f"build_number, created_at, id) AS rn FROM test_runs WHERE project_id = :p) r "
            f"WHERE id = ANY(:ids)"
        ), {"p": seeded[0], "ids": ids})).all())
    assert got == reference
    assert len(got) == len(ids) == 23
    # 'API' and '  api ' are one partition: its numbers run to 2500, not 1250.
    assert max(got.values()) == 2500


async def test_the_admin_list_walks_the_global_index_to_its_limit(engine, seeded):
    statements, _ = await _captured(engine, lambda db: runs_service.list_project_runs(
        db, project_id=None, page=1, size=20, days=0, accessible_project_ids=None,
    ))
    plan = await _generic_plan(engine, _items_statement(statements))
    rendered = json.dumps(plan)[:4000]
    assert not any(n["Node Type"] == "Sort" for n in _nodes(plan)), f"still sorts: {rendered}"
    assert [n.get("Index Name") for n in _run_scans(plan)] == [GLOBAL_INDEX], rendered


async def test_a_members_list_is_one_ordered_branch_per_project(engine, seeded):
    statements, _ = await _captured(engine, lambda db: runs_service.list_project_runs(
        db, project_id=None, page=2, size=20, days=0, accessible_project_ids=set(seeded),
    ))
    plan = await _generic_plan(engine, _items_statement(statements))
    rendered = json.dumps(plan)[:4000]
    # What this proves: the page's candidates come from one branch per
    # project, and each branch is 0167's index read in order and stopped at
    # a LIMIT -- so no branch sorts or scans its project's history.
    #
    # What it does not constrain: how the page joins those few candidate ids
    # back to test_runs. That join reads only the candidates' rows, and its
    # method is a cost choice by table size -- on CI's fresh, small database
    # a hash join over the whole (tiny) table is cheaper than primary-key
    # probes; with a real table it is probes. Asserting it would test the
    # database's contents, not this code (lead review of the N18 test).
    appends = [n for n in _nodes(plan) if n["Node Type"] == "Append"]
    assert len(appends) == 1, f"expected one Append of per-project branches: {rendered}"
    branches = appends[0].get("Plans", [])
    assert len(branches) == len(seeded), rendered
    for branch in branches:
        nodes = list(_nodes(branch))
        assert any(n["Node Type"] == "Limit" for n in nodes), f"a branch has no LIMIT: {rendered}"
        runs = [n for n in nodes if n.get("Relation Name") == "test_runs"]
        assert len(runs) == 1, rendered
        assert runs[0]["Node Type"] in ("Index Scan", "Index Only Scan"), rendered
        assert runs[0].get("Index Name") == PROJECT_INDEX, rendered
        assert not any(n["Node Type"] == "Sort" for n in nodes), f"a branch sorts: {rendered}"
        # The LIMIT sits above the index scan, so the read stops there.
        limit_depth = next(i for i, n in enumerate(nodes) if n["Node Type"] == "Limit")
        scan_depth = next(i for i, n in enumerate(nodes) if n is runs[0])
        assert limit_depth < scan_depth, rendered


async def test_a_member_of_one_project_takes_the_single_project_index(engine, seeded):
    """QA-B45-D-4. As a one-branch join under a generic plan, a member of one
    project hash-joined its candidates against a Seq Scan of every run (59 ms
    at 400k runs, QA). It takes the plain path: `project_id IN (x)` is an
    equality, and 0167's index serves it with the LIMIT."""
    statements, _ = await _captured(engine, lambda db: runs_service.list_project_runs(
        db, project_id=None, page=1, size=20, days=0, accessible_project_ids={seeded[0]},
    ))
    statement = _items_statement(statements)
    sql = str(statement.compile(dialect=pg_asyncpg.dialect()))
    assert "page_candidates" not in sql and "UNION" not in sql.upper(), sql
    plan = await _generic_plan(engine, statement)
    rendered = json.dumps(plan)[:4000]
    assert not any(n["Node Type"] == "Seq Scan" and n.get("Relation Name") == "test_runs"
                   for n in _nodes(plan)), rendered
    assert not any(n["Node Type"] == "Sort" for n in _nodes(plan)), rendered
    assert [n.get("Index Name") for n in _run_scans(plan)] == [PROJECT_INDEX], rendered


async def test_each_branch_limit_is_a_literal(engine, seeded):
    """QA-B45-D-4. A bound LIMIT is `$n` under a generic plan, and the planner
    cannot see how small it is. Each branch's LIMIT is page * size, rendered."""
    statements, _ = await _captured(engine, lambda db: runs_service.list_project_runs(
        db, project_id=None, page=2, size=20, days=0, accessible_project_ids=set(seeded),
    ))
    compiled = _items_statement(statements).compile(dialect=pg_asyncpg.dialect())
    sql = " ".join(str(compiled).split())
    assert sql.count("LIMIT 40") == len(seeded), sql


@pytest.mark.parametrize("page", [1, 2, 3])
async def test_a_members_pages_are_the_global_order(engine, seeded, page):
    async with AsyncSession(engine) as db:
        items, total, _pages = await runs_service.list_project_runs(
            db, project_id=None, page=page, size=20, days=0, accessible_project_ids=set(seeded),
        )
    async with engine.connect() as conn:
        expected = [str(r) for r in (await conn.execute(text(
            f"SELECT id FROM test_runs WHERE project_id = ANY(:p) "
            f"ORDER BY ({KEY}) DESC NULLS FIRST, build_number DESC, created_at DESC, id DESC "
            f"OFFSET :o LIMIT 20"
        ), {"p": seeded, "o": (page - 1) * 20})).scalars()]
    assert [_id(item) for item in items] == expected
    assert total == 2500 * 2 + 6 + 800 * 2
