"""``test_steps`` text columns must be trigram-indexed, and the planner must use them.

Keyword search ORs five predicates; one is a correlated EXISTS over
``test_steps`` so that a failing step name or assertion message makes its test
findable. Neither ``test_steps.name`` nor ``test_steps.assertion_message`` had
an index, so Postgres satisfied that operand by reading **every** step row —
a cost every search paid regardless of the term. Measured on a 15,780-case /
60,360-step corpus that subplan was 46.2 ms; with the indexes it is 0.8 ms, and
end-to-end search throughput roughly doubled.

Two assertions, because either alone can pass while the fix is dead:

* the indexes exist with the expected access method and operator class — which
  a dropped or malformed migration breaks;
* the planner actually **chooses** one for a selective ILIKE, which an index
  that exists but cannot serve the predicate (wrong opclass, wrong column)
  would fail.

The plan assertion runs with ``enable_seqscan = off``, and that is deliberate.

The first version asserted the planner picks the index *naturally*. It passed
locally against a table that already held 60,360 rows of unrelated data, and
failed in CI where ``test_steps`` was empty. That failure was correct: on a
small table a sequential scan really is cheaper, so the test was asserting a
falsehood. Measured on the real table with a 20,000-row fixture — seq scan
1157, forced bitmap 2659. The index only wins around the ~60k scale where the
46.2ms -> 0.8ms improvement was originally measured, and a 60k-row fixture is
too heavy to build on every CI run.

Whether the planner *chooses* the index is a cost decision about data volume.
What this test needs to pin is that the index **can serve the predicate** —
that is what breaks if someone changes the opclass, the indexed column, or the
predicate form. Disabling seqscan isolates exactly that, and it is not vacuous:
``enable_seqscan = off`` only adds a cost penalty, it does not forbid the scan.
Verified by dropping ``ix_test_steps_name_trgm`` with seqscan still off — the
plan falls back to ``Seq Scan`` at cost 10000001157 rather than using any
index.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a disposable database. All rows
are uniquely keyed and removed in a ``finally``.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

STEP_ROWS = 3000
# A token that appears in exactly one step, so the ILIKE is selective enough
# that an index scan is the cheaper plan.
NEEDLE = "zqxjneedle"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


EXPECTED_INDEXES = {
    "ix_test_steps_name_trgm": "name",
    "ix_test_steps_assertion_trgm": "assertion_message",
}


async def test_step_text_columns_are_trigram_indexed():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'test_steps'"
            ))).all()
        defs = {r.indexname: r.indexdef for r in rows}

        for name, column in EXPECTED_INDEXES.items():
            assert name in defs, (
                f"{name} is missing — migration 0138 did not run or was reverted. "
                f"Without it every keyword search sequentially scans test_steps."
            )
            definition = defs[name].lower()
            assert "using gin" in definition, f"{name} is not a GIN index: {defs[name]}"
            assert "gin_trgm_ops" in definition, (
                f"{name} lacks gin_trgm_ops, so it cannot serve an ILIKE: {defs[name]}"
            )
            assert column in definition, f"{name} does not cover {column}: {defs[name]}"
    finally:
        await engine.dispose()


async def test_planner_uses_the_step_trigram_index_for_an_ilike():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    suffix = uuid.uuid4().hex[:10]
    project_id = uuid.uuid4()
    suite_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO projects (id, name, slug, is_active) "
                "VALUES (:id, :name, :slug, true)"
            ), {"id": project_id, "name": f"step-idx {suffix}", "slug": f"step-idx-{suffix}"})
            await conn.execute(text(
                "INSERT INTO test_suites (id, project_id, name) VALUES (:id, :pid, :name)"
            ), {"id": suite_id, "pid": project_id, "name": f"suite {suffix}"})
            await conn.execute(text(
                "INSERT INTO canonical_test_cases "
                "(id, project_id, test_suite_id, test_fingerprint, test_name) "
                "VALUES (:id, :pid, :sid, :fp, :tn)"
            ), {"id": canonical_id, "pid": project_id, "sid": suite_id,
                "fp": f"fp-{suffix}", "tn": f"test {suffix}"})
            await conn.execute(text(
                "INSERT INTO test_steps "
                "(canonical_test_case_id, ordinal, depth, name, status) "
                "SELECT :cid, g.i, 0, "
                "  CASE WHEN g.i = 1 THEN :needle ELSE 'ordinary step ' || g.i END, "
                "  'PASSED' "
                "FROM generate_series(1, :n) AS g(i)"
            ), {"cid": canonical_id, "needle": f"step {NEEDLE} here", "n": STEP_ROWS})

        async with engine.connect() as conn:
            await conn.execute(text("ANALYZE test_steps"))
            live_rows = (await conn.execute(text(
                "SELECT count(*) FROM test_steps WHERE canonical_test_case_id = :cid"
            ), {"cid": canonical_id})).scalar()
            assert live_rows == STEP_ROWS, (
                f"fixture inserted {live_rows} steps, expected {STEP_ROWS}"
            )
            await conn.execute(text("SET LOCAL enable_seqscan = off"))
            plan_rows = (await conn.execute(text(
                "EXPLAIN SELECT id FROM test_steps "
                "WHERE name ILIKE :pattern OR assertion_message ILIKE :pattern"
            ), {"pattern": f"%{NEEDLE}%"})).all()
        plan = chr(10).join(str(r[0]) for r in plan_rows)

        assert "Bitmap Index Scan on ix_test_steps_name_trgm" in plan, (
            "migration 0138's index cannot serve an ILIKE on test_steps.name. "
            "With seqscan penalised Postgres still refused it, which means the "
            "opclass or the indexed column is wrong — not that the table is "
            f"small. Plan:{chr(10)}{plan}"
        )
        assert "Index Cond" in plan, (
            f"the ILIKE was not pushed down as an index condition:{chr(10)}{plan}"
        )
        assert "Seq Scan on test_steps" not in plan, (
            f"test_steps is still sequentially scanned even with seqscan "
            f"penalised, so no index applies. Plan:{chr(10)}{plan}"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(
                "DELETE FROM test_steps WHERE canonical_test_case_id = :cid"
            ), {"cid": canonical_id})
            await conn.execute(text(
                "DELETE FROM canonical_test_cases WHERE id = :id"), {"id": canonical_id})
            await conn.execute(text("DELETE FROM test_suites WHERE id = :id"), {"id": suite_id})
            await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        await engine.dispose()
