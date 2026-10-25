"""Migration 0231 (W16 AGT-09) on real PostgreSQL.

* A legacy run and baseline written at 0230 read ``label_agreement`` /
  ``completed`` (counters 0, ``items_capped`` false, no candidate) after the
  upgrade — never recomputed.
* ``uq_aeio_run_item`` refuses a second row for one ``(eval_run_id,
  item_index)`` and makes ``ON CONFLICT DO NOTHING`` a no-op; the checks refuse
  a bad item status, a negative index, a bad run status and a bad metric kind
  (run and baseline); ``score`` defaults to ``{}``.
* Deleting a run (or its dataset) deletes its items (CASCADE); deleting the
  project or the requester keeps the run (SET NULL).
* upgrade -> downgrade 0230 -> upgrade again works; the downgrade leaves the
  0230 shape and the legacy rows intact.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema holding the 0230 shape of the tables it touches (``users``,
``projects``, ``ai_eval_datasets``, ``ai_eval_runs``, ``ai_eval_baselines`` —
the columns it needs), dropped afterwards; the shared tables are never
written, and the last test checks they carry head's shape.

The fixture awaits ``app.db.postgres.dispose_engine_for_loop()`` before
building its engine and after disposing it, and closes the app's Redis / Mongo
clients, so the next module never meets "Event loop is closed". Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0231 = "0231_ai_eval_candidate_runs.py"
RUNS, BASELINES, ITEMS = "ai_eval_runs", "ai_eval_baselines", "ai_eval_item_outputs"
RUN_0230_COLUMNS = {"id", "dataset_id", "model_name", "task_type", "accuracy", "evaluated_at"}
RUN_W16_COLUMNS = {
    "metric_kind", "status", "candidate", "project_id", "requested_by", "started_at", "completed_at",
    "heartbeat_at", "scored_items", "unscored_items", "error_items", "items_capped", "error_code", "metrics",
}
ITEM_COLUMNS = {
    "id", "eval_run_id", "item_index", "item_key", "status", "error_code", "expected", "output", "score",
    "latency_ms", "tokens", "mode_used", "created_at",
}
RUN_W16_CONSTRAINTS = {
    ("ck_aer_metric_kind", "c"), ("ck_aer_status", "c"), ("fk_aer_project_id", "f"), ("fk_aer_requested_by", "f"),
}
ITEM_CONSTRAINTS = {
    ("ai_eval_item_outputs_pkey", "p"), ("ai_eval_item_outputs_eval_run_id_fkey", "f"),
    ("uq_aeio_run_item", "u"), ("ck_aeio_status", "c"), ("ck_aeio_item_index_nonnegative", "c"),
}
ITEM_INDEXES = {"ai_eval_item_outputs_pkey", "uq_aeio_run_item", "ix_aeio_run_status"}


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0231_pg", VERSIONS / M0231)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    from app.db.mongo import close_mongo
    from app.db.postgres import dispose_engine_for_loop
    from app.db.redis_client import close_redis

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
        await close_redis()
        await close_mongo()


#: The 0230 shape of what 0231 touches (only the columns it needs).
_SCRATCH_TABLES = (
    "CREATE TABLE {s}.users (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.projects (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.ai_eval_datasets (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.ai_eval_runs (id uuid PRIMARY KEY, "
    "dataset_id uuid NOT NULL REFERENCES {s}.ai_eval_datasets(id) ON DELETE CASCADE, "
    "model_name varchar(200) NOT NULL, task_type varchar(50) NOT NULL, accuracy double precision, "
    "evaluated_at timestamptz DEFAULT now())",
    "CREATE TABLE {s}.ai_eval_baselines (id uuid PRIMARY KEY, task_type varchar(50) NOT NULL, "
    "agent_name varchar(100) NOT NULL, prompt_version varchar(50) NOT NULL, "
    "eval_run_id uuid REFERENCES {s}.ai_eval_runs(id) ON DELETE SET NULL)",
)


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w16a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for statement in _SCRATCH_TABLES:
        await conn.execute(text(statement.format(s=f'"{scratch}"')))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _run(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _go(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)
    await conn.commit()


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _write(conn, sql: str, **params) -> int:
    result = await conn.execute(text(sql), params)
    await conn.commit()
    return result.rowcount


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
        s=schema, t=table,
    )
    return {r[0] for r in rows}


async def _constraints(conn, schema: str, table: str) -> set[tuple[str, str]]:
    rows = await _rows(
        conn,
        "SELECT c.conname, c.contype::text FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid = c.connamespace JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE n.nspname = :s AND t.relname = :t",
        s=schema, t=table,
    )
    return {(r[0], r[1]) for r in rows}


async def _indexes(conn, schema: str, table: str) -> set[str]:
    return {r[0] for r in await _rows(
        conn, "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = :t", s=schema, t=table,
    )}


async def _legacy_world(conn) -> dict[str, uuid.UUID]:
    """At 0230: a dataset, one legacy run and one legacy baseline pointing at it."""
    ids = {k: uuid.uuid4() for k in ("dataset", "run", "baseline")}
    await _write(conn, "INSERT INTO ai_eval_datasets (id) VALUES (:d)", d=ids["dataset"])
    await _write(
        conn,
        "INSERT INTO ai_eval_runs (id, dataset_id, model_name, task_type, accuracy) "
        "VALUES (:r, :d, 'AnalysisAgent', 'classification', 0.8)",
        r=ids["run"], d=ids["dataset"],
    )
    await _write(
        conn,
        "INSERT INTO ai_eval_baselines (id, task_type, agent_name, prompt_version, eval_run_id) "
        "VALUES (:b, 'classification', 'AnalysisAgent', 'v1', :r)",
        b=ids["baseline"], r=ids["run"],
    )
    return ids


async def _item(conn, run, index=0, status="correct", **extra) -> int:
    columns = {"id": uuid.uuid4(), "eval_run_id": run, "item_index": index, "status": status, **extra}
    names = ", ".join(columns)
    values = ", ".join(f":{n}" for n in columns)
    expected = "" if "expected" in columns else ", expected"
    expected_value = "" if "expected" in columns else ", '{}'::jsonb"
    return await _write(
        conn,
        f"INSERT INTO ai_eval_item_outputs ({names}{expected}) VALUES ({values}{expected_value}) "
        "ON CONFLICT (eval_run_id, item_index) DO NOTHING",
        **columns,
    )


async def test_legacy_rows_read_the_legacy_metric_after_the_upgrade(conn):
    await _scratch(conn)
    ids = await _legacy_world(conn)
    await _run(conn, "upgrade")
    assert await _rows(
        conn,
        "SELECT metric_kind, status, scored_items, unscored_items, error_items, items_capped, candidate, "
        "project_id, requested_by, metrics, error_code, accuracy FROM ai_eval_runs WHERE id = :r",
        r=ids["run"],
    ) == [("label_agreement", "completed", 0, 0, 0, False, None, None, None, None, None, 0.8)]
    assert await _rows(conn, "SELECT metric_kind FROM ai_eval_baselines WHERE id = :b", b=ids["baseline"]) == [
        ("label_agreement",),
    ]


async def test_the_item_key_the_checks_and_the_score_default(conn):
    await _scratch(conn)
    ids = await _legacy_world(conn)
    await _run(conn, "upgrade")
    run = ids["run"]
    assert await _item(conn, run, 0, "correct") == 1
    assert await _item(conn, run, 0, "incorrect") == 0, "a redelivered item is a no-op (ON CONFLICT DO NOTHING)"
    with pytest.raises(IntegrityError, match="uq_aeio_run_item"):
        await _write(
            conn,
            "INSERT INTO ai_eval_item_outputs (id, eval_run_id, item_index, status, expected) "
            "VALUES (:i, :r, 0, 'error', '{}'::jsonb)",
            i=uuid.uuid4(), r=run,
        )
    await conn.rollback()
    assert await _rows(conn, "SELECT status, score, created_at IS NOT NULL FROM ai_eval_item_outputs") == [
        ("correct", {}, True),
    ]
    for kwargs, check in (
        ({"index": 1, "status": "skipped"}, "ck_aeio_status"),
        ({"index": -1}, "ck_aeio_item_index_nonnegative"),
    ):
        with pytest.raises(IntegrityError, match=check):
            await _item(conn, run, **kwargs)
        await conn.rollback()
    for sql, check in (
        ("UPDATE ai_eval_runs SET status = 'done' WHERE id = :r", "ck_aer_status"),
        ("UPDATE ai_eval_runs SET metric_kind = 'heuristic' WHERE id = :r", "ck_aer_metric_kind"),
        ("UPDATE ai_eval_baselines SET metric_kind = 'heuristic' WHERE eval_run_id = :r", "ck_aeb_metric_kind"),
    ):
        with pytest.raises(IntegrityError, match=check):
            await _write(conn, sql, r=run)
        await conn.rollback()
    for status in ("queued", "running", "partial", "failed", "completed"):
        assert await _write(conn, "UPDATE ai_eval_runs SET status = :s, metric_kind = 'replay' WHERE id = :r",
                            s=status, r=run) == 1


async def test_the_cascades_and_the_set_nulls(conn):
    await _scratch(conn)
    ids = await _legacy_world(conn)
    await _run(conn, "upgrade")
    project, person, other_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _write(conn, "INSERT INTO projects (id) VALUES (:p)", p=project)
    await _write(conn, "INSERT INTO users (id) VALUES (:u)", u=person)
    await _write(
        conn,
        "INSERT INTO ai_eval_runs (id, dataset_id, model_name, task_type, metric_kind, status, project_id, "
        "requested_by) VALUES (:r, :d, 'rules', 'classification', 'replay', 'queued', :p, :u)",
        r=other_run, d=ids["dataset"], p=project, u=person,
    )
    await _item(conn, ids["run"], 0)
    await _item(conn, other_run, 0)
    await _item(conn, other_run, 1, "unscored")

    await _write(conn, "DELETE FROM users WHERE id = :u", u=person)
    await _write(conn, "DELETE FROM projects WHERE id = :p", p=project)
    assert await _rows(conn, "SELECT project_id, requested_by FROM ai_eval_runs WHERE id = :r", r=other_run) == [
        (None, None),
    ], "deleting the project or the requester keeps the run"
    await _write(conn, "DELETE FROM ai_eval_runs WHERE id = :r", r=ids["run"])
    assert await _rows(conn, "SELECT eval_run_id, item_index FROM ai_eval_item_outputs ORDER BY item_index") == [
        (other_run, 0), (other_run, 1),
    ], "deleting a run deletes its items, and only its items"
    await _write(conn, "DELETE FROM ai_eval_datasets WHERE id = :d", d=ids["dataset"])
    assert await _rows(conn, "SELECT count(*) FROM ai_eval_item_outputs") == [(0,)], (
        "deleting the dataset deletes its runs and their items"
    )


async def test_upgrade_downgrade_0230_upgrade_again(conn):
    scratch = await _scratch(conn)
    ids = await _legacy_world(conn)
    await _run(conn, "upgrade")
    assert await _columns(conn, scratch, RUNS) == RUN_0230_COLUMNS | RUN_W16_COLUMNS
    assert RUN_W16_CONSTRAINTS <= await _constraints(conn, scratch, RUNS)
    assert "metric_kind" in await _columns(conn, scratch, BASELINES)
    assert await _columns(conn, scratch, ITEMS) == ITEM_COLUMNS
    assert await _constraints(conn, scratch, ITEMS) == ITEM_CONSTRAINTS
    assert await _indexes(conn, scratch, ITEMS) == ITEM_INDEXES
    assert await _indexes(conn, scratch, RUNS) == {"ai_eval_runs_pkey"}, "no index on the existing table"
    await _item(conn, ids["run"], 0)

    await _run(conn, "downgrade")
    assert await _columns(conn, scratch, RUNS) == RUN_0230_COLUMNS
    assert not RUN_W16_CONSTRAINTS & await _constraints(conn, scratch, RUNS)
    assert "metric_kind" not in await _columns(conn, scratch, BASELINES)
    assert ("ck_aeb_metric_kind", "c") not in await _constraints(conn, scratch, BASELINES)
    assert await _columns(conn, scratch, ITEMS) == set()
    assert await _rows(conn, "SELECT id, model_name, accuracy FROM ai_eval_runs") == [
        (ids["run"], "AnalysisAgent", 0.8),
    ], "the legacy run survives the downgrade"
    assert await _rows(conn, "SELECT id, eval_run_id FROM ai_eval_baselines") == [(ids["baseline"], ids["run"])]

    await _run(conn, "upgrade")
    assert await _columns(conn, scratch, ITEMS) == ITEM_COLUMNS
    assert await _rows(conn, "SELECT metric_kind, status FROM ai_eval_runs") == [("label_agreement", "completed")]


async def test_the_shared_tables_carry_heads_shape(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert RUN_W16_COLUMNS <= await _columns(conn, base, RUNS)
    assert RUN_W16_CONSTRAINTS <= await _constraints(conn, base, RUNS)
    assert ("ck_aeb_metric_kind", "c") in await _constraints(conn, base, BASELINES)
    assert await _columns(conn, base, ITEMS) == ITEM_COLUMNS
    assert await _constraints(conn, base, ITEMS) == ITEM_CONSTRAINTS
    assert await _indexes(conn, base, ITEMS) == ITEM_INDEXES
