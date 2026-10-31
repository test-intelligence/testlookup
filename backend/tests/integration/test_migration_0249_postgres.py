"""Migration 0249 (W25b HYG-05) on real PostgreSQL.

* Before the upgrade, the statement ``ON DELETE SET NULL`` runs for each run
  pointer -- ``UPDATE ONLY canonical_test_cases SET <col> = NULL WHERE <col> =
  $1`` -- reads the whole catalog (``Seq Scan``) on a 20,000-row table.
* After it, the three indexes are VALID (``pg_index.indisvalid``),
  ``pg_get_indexdef`` renders ``USING btree (<col>) WHERE (<col> IS NOT
  NULL)``, and each statement is served by its own index with seq scans NOT
  disabled -- for a literal id, and for the GENERIC plan of the prepared
  statement (the shape the referential trigger caches), which is why the
  indexes are partial on ``IS NOT NULL``.
* The migration changes NO row (the D26 parity clock): ``count(*)`` and an
  ordered ``md5`` of every row's id, run pointers and status are equal before
  the upgrade, after it, after the downgrade and after the upgrade again.
* An INVALID leftover holding ``ix_ctc_last_seen_run`` (a failed CONCURRENT
  build, planted here by a unique build over duplicate keys -- no superuser
  needed, the 0245 trick) is dropped and rebuilt as the real, VALID index.
* Downgrade removes all three; upgrading again restores them (the round trip).
* The shared database (migrated to head) carries the three indexes, VALID.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema that is the ONLY schema on the search path while it runs
(``canonical_test_cases`` as ``LIKE … INCLUDING DEFAULTS``: no index, no
foreign key copied), so neither the upgrade nor the downgrade can resolve a
name in the shared schema. A CONCURRENT build cannot run inside a
transaction, so this work commits; the scratch schema is dropped afterwards
and the shared tables are only read. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it; it binds no Redis / Mongo client and closes none.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0249 = "0249_ctc_run_fk_indexes.py"
#: index name -> its column.
INDEXES = {
    "ix_ctc_first_seen_run": "first_seen_run_id",
    "ix_ctc_last_seen_run": "last_seen_run_id",
    "ix_ctc_deleted_at_run": "deleted_at_run_id",
}
ROWS = 20_000
RUNS = 5_000
#: Run number 0 is a first-seen, last-seen AND deleted-at pointer of rows 0, 5000, 10000, 15000.
PROBE_RUN = "run-0"

_CHECKSUM_SQL = (
    "SELECT count(*), md5(coalesce(string_agg("
    "id::text || coalesce(first_seen_run_id::text, '-') || coalesce(last_seen_run_id::text, '-') "
    "|| coalesce(deleted_at_run_id::text, '-') || status, ',' ORDER BY id), '')) "
    "FROM canonical_test_cases"
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0249_pg", VERSIONS / M0249)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tail(column: str) -> str:
    return f"USING btree ({column}) WHERE ({column} IS NOT NULL)"


@pytest.fixture
async def conn():
    """A connection whose work COMMITS (the indexes are built CONCURRENTLY); the scratch schema is dropped afterwards."""
    # Redis and Mongo are never bound here, so they are not closed here either.
    from app.db.postgres import dispose_engine_for_loop

    await dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            connection.info["scratch_schemas"] = []
            try:
                yield connection
            finally:
                await connection.rollback()
                await connection.execute(text("RESET search_path"))
                for schema in connection.info["scratch_schemas"]:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()
        await dispose_engine_for_loop()


async def _scratch(conn) -> str:
    """A scratch schema holding an index-less, FK-less copy of canonical_test_cases, ALONE on the search path."""
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w25bb_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".canonical_test_cases '
        f'(LIKE "{base}".canonical_test_cases INCLUDING DEFAULTS)'
    ))
    await conn.execute(text(f'SET search_path TO "{scratch}"'))
    await conn.commit()
    return scratch


async def _fill(conn, rows: int) -> None:
    """``rows`` catalog rows pointing at ``RUNS`` distinct run ids (deterministic md5 uuids): every row has a
    first / last seen run, every tenth row a deleted-at run (and status ``deleted``); 17 projects, 200 suites."""
    await conn.execute(text(
        "INSERT INTO canonical_test_cases (id, project_id, test_suite_id, test_fingerprint, test_name, "
        "status, first_seen_run_id, last_seen_run_id, deleted_at_run_id) "
        "SELECT md5('ctc-' || g)::uuid, md5('project-' || (g % 17))::uuid, md5('suite-' || (g % 200))::uuid, "
        "md5('fp-' || g), 'test_w25bb_' || g, "
        "CASE WHEN g % 10 = 0 THEN 'deleted' ELSE 'active' END, "
        "md5('run-' || ((g * 7) % :runs))::uuid, md5('run-' || ((g * 13) % :runs))::uuid, "
        "CASE WHEN g % 10 = 0 THEN md5('run-' || ((g * 17) % :runs))::uuid END "
        "FROM generate_series(0, :last) AS g"
    ), {"runs": RUNS, "last": rows - 1})
    await conn.commit()
    await conn.execute(text("ANALYZE canonical_test_cases"))
    await conn.commit()


async def _migrate(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        # As a real run: alembic owns the transaction, so autocommit_block() can commit it.
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_run)
    await conn.commit()


async def _checksum(conn) -> tuple[int, str]:
    count, digest = (await conn.execute(text(_CHECKSUM_SQL))).one()
    await conn.commit()
    return int(count), str(digest)


async def _index_state(conn, schema: str) -> dict[str, tuple[bool, str]]:
    rows = (await conn.execute(text(
        "SELECT c.relname, i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = ANY(:names) AND n.nspname = :s"
    ), {"names": list(INDEXES), "s": schema})).all()
    await conn.commit()
    return {name: (bool(valid), definition) for name, valid, definition in rows}


async def _index_count(conn, schema: str) -> int:
    count = (await conn.execute(
        text("SELECT count(*) FROM pg_indexes WHERE schemaname = :s"), {"s": schema}
    )).scalar_one()
    await conn.commit()
    return int(count)


def _nodes(plan: dict) -> list[dict]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


async def _plan(conn, sql: str) -> list[dict]:
    """Every node of ``EXPLAIN (FORMAT JSON) sql`` -- planned, never executed; seq scans NOT disabled."""
    raw = (await conn.execute(text("EXPLAIN (FORMAT JSON) " + sql))).scalar_one()
    await conn.rollback()
    document = json.loads(raw) if isinstance(raw, str) else raw
    return _nodes(document[0]["Plan"])


def _ri_sql(column: str, run: str) -> str:
    """What ON DELETE SET NULL runs for one deleted run (a literal id: EXPLAIN takes no binds)."""
    return f"UPDATE ONLY canonical_test_cases SET {column} = NULL WHERE {column} = md5('{run}')::uuid"


async def _generic_plan(conn, column: str) -> list[dict]:
    """The GENERIC plan of the prepared RI statement (the referential trigger caches a prepared plan)."""
    name = f"w25bb_ri_{column}"
    await conn.execute(text(
        f"PREPARE {name}(uuid) AS UPDATE ONLY canonical_test_cases SET {column} = NULL WHERE {column} = $1"
    ))
    try:
        await conn.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        raw = (await conn.execute(
            text(f"EXPLAIN (FORMAT JSON) EXECUTE {name}(md5('{PROBE_RUN}')::uuid)")
        )).scalar_one()
    finally:
        await conn.execute(text(f"DEALLOCATE {name}"))
        await conn.rollback()  # EXPLAIN never executed the UPDATE; SET LOCAL ends here
    document = json.loads(raw) if isinstance(raw, str) else raw
    return _nodes(document[0]["Plan"])


def _scans(nodes: list[dict]) -> list[tuple[str, str | None]]:
    return [(n["Node Type"], n.get("Index Name")) for n in nodes if "Scan" in n["Node Type"]]


def _uses(nodes: list[dict], index: str) -> bool:
    scans = _scans(nodes)
    return (
        any(kind in ("Index Scan", "Index Only Scan", "Bitmap Index Scan") and name == index for kind, name in scans)
        and not any(kind == "Seq Scan" for kind, _ in scans)
    )


# ── the plans, the checksum, the round trip ──────────────────────────────────


async def test_0249_builds_three_valid_indexes_the_set_null_statements_use_and_changes_no_row(conn):
    scratch = await _scratch(conn)
    await _fill(conn, ROWS)
    assert await _index_count(conn, scratch) == 0, "the copy carries no index"

    before = await _checksum(conn)
    assert before[0] == ROWS
    for column in INDEXES.values():
        scans = _scans(await _plan(conn, _ri_sql(column, PROBE_RUN)))
        assert scans == [("Seq Scan", None)], (column, scans)

    await _migrate(conn, "upgrade")

    state = await _index_state(conn, scratch)
    assert set(state) == set(INDEXES), state
    for name, column in INDEXES.items():
        valid, definition = state[name]
        assert valid, f"the CONCURRENT build of {name} left a VALID index"
        assert definition.endswith(_tail(column)), definition
        assert "UNIQUE" not in definition
        literal = await _plan(conn, _ri_sql(column, PROBE_RUN))
        assert _uses(literal, name), (name, _scans(literal))
        generic = await _generic_plan(conn, column)
        assert _uses(generic, name), ("generic plan", name, _scans(generic))
    assert await _checksum(conn) == before, "the upgrade changed a row"

    await _migrate(conn, "downgrade")
    assert await _index_state(conn, scratch) == {}
    assert await _index_count(conn, scratch) == 0
    assert await _checksum(conn) == before, "the downgrade changed a row"
    for column in INDEXES.values():
        assert _scans(await _plan(conn, _ri_sql(column, PROBE_RUN))) == [("Seq Scan", None)], column

    # The round trip: the upgrade runs again on the downgraded table.
    await _migrate(conn, "upgrade")
    state = await _index_state(conn, scratch)
    assert {name: valid for name, (valid, _) in state.items()} == {name: True for name in INDEXES}
    assert all(state[name][1].endswith(_tail(column)) for name, column in INDEXES.items())
    assert await _checksum(conn) == before, "the second upgrade changed a row"

    # The data really is what the plans claim: run 0 is each pointer of rows 0, 5000, 10000, 15000.
    for column in INDEXES.values():
        hits = (await conn.execute(text(
            f"SELECT count(*) FROM canonical_test_cases WHERE {column} = md5(:run)::uuid"
        ), {"run": PROBE_RUN})).scalar_one()
        await conn.commit()
        assert hits == 4, (column, hits)


async def test_an_invalid_leftover_holding_the_name_is_rebuilt_as_the_real_index(conn):
    scratch = await _scratch(conn)
    await _fill(conn, 40)
    await _migrate(conn, "upgrade")
    before = await _checksum(conn)

    # A failed CONCURRENT build leaves an INVALID index behind: a UNIQUE build over duplicate keys
    # (status repeats), under the real index's name once the real one is gone.
    await conn.execute(text(f'DROP INDEX "{scratch}".ix_ctc_last_seen_run'))
    await conn.commit()
    autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
    with pytest.raises(DBAPIError):
        await autocommit.execute(text(
            "CREATE UNIQUE INDEX CONCURRENTLY ix_ctc_last_seen_run ON canonical_test_cases (status)"
        ))
    await conn.rollback()
    await conn.execution_options(isolation_level="READ COMMITTED")
    valid, definition = (await _index_state(conn, scratch))["ix_ctc_last_seen_run"]
    assert not valid and "UNIQUE" in definition

    await _migrate(conn, "upgrade")

    state = await _index_state(conn, scratch)
    valid, definition = state["ix_ctc_last_seen_run"]
    assert valid, "the leftover was dropped and the real index built"
    assert "UNIQUE" not in definition and definition.endswith(_tail("last_seen_run_id")), definition
    # The two VALID siblings were left alone (IF NOT EXISTS), and no row moved.
    assert {name: ok for name, (ok, _) in state.items()} == {name: True for name in INDEXES}
    assert await _checksum(conn) == before


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_the_three_indexes(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    await conn.commit()
    assert head >= "0249"
    state = await _index_state(conn, base)
    assert set(state) == set(INDEXES), state
    for name, column in INDEXES.items():
        valid, definition = state[name]
        assert valid and definition.endswith(_tail(column)), (name, definition)
