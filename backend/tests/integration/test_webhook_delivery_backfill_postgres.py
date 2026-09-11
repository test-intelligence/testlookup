"""Re-audit N3: 0171 backfills webhook_deliveries.run_id in batches, as 0162 did at once.

0162 filled ``run_id`` with one cross-table UPDATE inside the migration
transaction, holding every matched row's lock until the whole upgrade
committed. That UPDATE moved to 0171, which commits batch by batch, keyset-paged
by id, and joins test_runs by primary key.

This runs 0171's real ``backfill`` on real PostgreSQL over planted rows --
matching, deleted-run, malformed, other events, already set -- and checks:

* the end state is exactly what 0162's UPDATE produced (computed on the same
  rows in a transaction that is rolled back);
* a tiny batch size reaches every row, and each candidate is looked at once;
* a second run fills nothing (idempotent, and a no-op after 0162's backfill);
* each batch commits (a concurrent reader sees the first batch mid-run).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration():
    path = VERSIONS / "0171_webhook_delivery_run_backfill.py"
    spec = importlib.util.spec_from_file_location("m0171", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M0171 = _migration()

# 0162's statement, verbatim, for the reference end state.
OLD_0162 = (
    "UPDATE webhook_deliveries AS delivery SET run_id = run.id FROM test_runs AS run "
    "WHERE delivery.event_type = 'run.completed' AND delivery.event_payload ? 'run_id' "
    "AND delivery.event_payload ->> 'run_id' = run.id::text"
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def planted():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    project, subscription = uuid.uuid4(), uuid.uuid4()
    runs = [uuid.uuid4() for _ in range(4)]
    gone = uuid.uuid4()  # a run that no longer exists
    rows: dict[str, uuid.UUID] = {}

    def payload(value):
        return json.dumps({"run_id": value} if value is not None else {"other": 1})

    plan = [
        # name, event, payload run_id, run_id already set
        *[(f"match{i}", "run.completed", str(runs[i % 4]), None) for i in range(9)],
        ("gone", "run.completed", str(gone), None),
        ("malformed", "run.completed", "not-a-uuid", None),
        ("number", "run.completed", 12345, None),
        ("no_key", "run.completed", None, None),
        ("other_event", "defect.promoted", str(runs[0]), None),
        ("already", "run.completed", str(runs[1]), runs[2]),
        # 0162 compared text, so an upper-case uuid never matched there.
        ("upper", "run.completed", str(runs[3]).upper(), None),
    ]
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO projects (id, name, slug, is_active) VALUES (:id, :n, :n, true)"
        ), {"id": project, "n": f"n3-{project.hex[:10]}"})
        for run in runs:
            await conn.execute(text(
                "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, "
                "ingestion_source, failed_tests, total_tests) "
                "VALUES (:id, :p, :b, 'n3', 'PASSED', 'unknown', 0, 1)"
            ), {"id": run, "p": project, "b": f"n3-{run.hex[:8]}"})
        await conn.execute(text(
            "INSERT INTO webhook_subscriptions (id, project_id, name, target_url, events, "
            "enabled, has_secret, max_retries) VALUES (:id, :p, 'n3', 'https://example.invalid', "
            "CAST('[\"run.completed\"]' AS jsonb), true, false, 5)"
        ), {"id": subscription, "p": project})
        for name, event, value, preset in plan:
            row_id = uuid.uuid4()
            rows[name] = row_id
            await conn.execute(text(
                "INSERT INTO webhook_deliveries (id, subscription_id, run_id, event_type, "
                "event_payload, status, attempt_count) VALUES (:id, :s, :r, :e, "
                "CAST(:payload AS jsonb), 'SUCCESS', 1)"
            ), {"id": row_id, "s": subscription, "r": preset, "e": event, "payload": payload(value)})
    try:
        yield engine, rows, runs
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM webhook_deliveries WHERE subscription_id = :s"),
                               {"s": subscription})
            await conn.execute(text("DELETE FROM webhook_subscriptions WHERE id = :s"), {"s": subscription})
            await conn.execute(text("DELETE FROM test_runs WHERE project_id = :p"), {"p": project})
            await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})
        await engine.dispose()


async def _state(conn, rows) -> dict[str, str | None]:
    result = await conn.execute(
        text("SELECT id, run_id FROM webhook_deliveries WHERE id = ANY(:ids)"),
        {"ids": list(rows.values())},
    )
    by_id = {row_id: name for name, row_id in rows.items()}
    return {by_id[r.id]: (str(r.run_id) if r.run_id else None) for r in result}


async def _backfill(engine, batch: int) -> tuple[int, int]:
    """0171's real loop, on an autocommit connection, scoped to these rows."""
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        return await conn.run_sync(lambda sync: M0171.backfill(sync, batch=batch))


async def test_the_batched_backfill_ends_where_0162_did(planted):
    engine, rows, _runs = planted
    async with engine.connect() as conn:
        transaction = await conn.begin()
        await conn.execute(text(OLD_0162 + " AND delivery.id = ANY(:ids)"), {"ids": list(rows.values())})
        reference = await _state(conn, rows)
        await transaction.rollback()

    await _backfill(engine, batch=2)

    async with engine.connect() as conn:
        after = await _state(conn, rows)
    # 0162 also overwrote an already-set row; the backfill leaves it alone.
    # Both leave it pointing at a real run, which is all the FK needs.
    reference.pop("already")
    assert after.pop("already") is not None
    # The one documented difference, in the safe direction: an upper-case
    # uuid names a real run, and 0171 matches it by value, not by text.
    assert reference.pop("upper") is None
    assert after.pop("upper") == str(_runs[3])
    assert after == reference
    assert all(after[f"match{i}"] is not None for i in range(9))
    assert {name: after[name] for name in ("gone", "malformed", "number", "no_key", "other_event")} == {
        "gone": None, "malformed": None, "number": None, "no_key": None, "other_event": None,
    }


async def test_each_candidate_is_seen_once_and_a_rerun_fills_nothing(planted):
    engine, rows, _runs = planted
    ours = set(rows.values())
    async with engine.connect() as conn:
        others = (await conn.execute(text(
            "SELECT count(*) FROM webhook_deliveries WHERE run_id IS NULL "
            "AND event_type = 'run.completed' AND event_payload ? 'run_id' AND NOT (id = ANY(:ids))"
        ), {"ids": list(ours)})).scalar_one()
    filled, scanned = await _backfill(engine, batch=2)
    # Candidates: the 9 matches, upper, gone, malformed and number (no_key has
    # no key, other_event another type, already is set) -- plus whatever else
    # the shared database holds, which the keyset walk also passes once.
    assert scanned == 13 + others
    assert filled >= 10
    # Idempotent: what is left is only what can never match, seen once more.
    assert await _backfill(engine, batch=2) == (0, scanned - filled)


async def test_a_failure_mid_run_keeps_the_batches_already_done(planted):
    """The point of batching: each batch commits and releases its row locks.

    The third fill fails. Inside one transaction -- 0162's shape -- that would
    roll back every row; here another connection still sees the first two
    batches.
    """
    engine, rows, _runs = planted
    fills = []

    def backfill_failing_at_the_third_fill(sync):
        original_execute = sync.execute

        def execute(statement, *args, **kwargs):
            if statement is M0171._FILL:
                fills.append(1)
                if len(fills) == 3:
                    raise RuntimeError("the third batch failed")
            return original_execute(statement, *args, **kwargs)

        sync.execute = execute
        return M0171.backfill(sync, batch=2)

    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(RuntimeError, match="third batch"):
            await conn.run_sync(backfill_failing_at_the_third_fill)

    async with engine.connect() as other:
        state = await _state(other, rows)
    done = [name for name, run_id in state.items() if name != "already" and run_id is not None]
    assert 1 <= len(done) <= 4, f"expected the first two batches committed, got {done}"
