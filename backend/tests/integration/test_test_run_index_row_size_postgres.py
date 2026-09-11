"""Review R-B45-D-1: no legal test_runs row can overflow a test_runs index.

0169's first index keyed the normalised suite NAME and INCLUDEd the column, so
a String(500) name was stored twice per entry: a 500-character CJK name made a
3,088-byte index row, a 500-emoji name 4,096 -- past btree's 2,704-byte limit.
The run's INSERT failed (an ingest that 500s), and on a database already
holding such a run the index build failed and left an INVALID index.

The key is now the md5 of the normalised name. These use RANDOM multibyte
text (repeated characters compress, and would hide the overflow):

* a run with the widest legal suite name and build number INSERTs at head,
  through every index test_runs has;
* on a copy of the table already holding such rows, every index definition
  the migrations build succeeds -- and the first 0169 definition does not,
  which is what keeps this check honest.

Everything runs in a transaction that is rolled back.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import random
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3], VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M0167 = _migration("0167_test_runs_natural_build_index.py")
M0169 = _migration("0169_test_runs_suite_seq_index.py")
M0170 = _migration("0170_test_runs_global_natural_index.py")

# The first 0169 definition, verbatim: the one that overflowed.
FIRST_0169 = (
    "(project_id, (lower(trim(coalesce(primary_suite_name, '')))), "
    f"({M0169.NATURAL_KEY}) ASC NULLS LAST, build_number, created_at, id) "
    "INCLUDE (primary_suite_name)"
)

_rng = random.Random(20260911)
SUITE_MAX = 500   # TestRun.primary_suite_name: String(500)
BUILD_MAX = 100   # TestRun.build_number: String(100)


def _cjk(n: int) -> str:
    return "".join(chr(_rng.randint(0x4E00, 0x9FFF)) for _ in range(n))


def _emoji(n: int) -> str:
    return "".join(chr(_rng.randint(0x1F300, 0x1FAFF)) for _ in range(n))


def _widest_build() -> str:
    """A digit between every four-byte character: 50 numeric chunks, 250 bytes."""
    return "".join(str(_rng.randint(1, 9)) + chr(_rng.randint(0x1F300, 0x1FAFF)) for _ in range(BUILD_MAX // 2))


WIDE_ROWS = [
    ("cjk", _cjk(SUITE_MAX), _widest_build()),
    ("emoji", _emoji(SUITE_MAX), _widest_build()),
    ("digits", _emoji(SUITE_MAX), "9" * BUILD_MAX),
]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def conn():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


def _check_lengths():
    for _name, suite, build in WIDE_ROWS:
        assert len(suite) == SUITE_MAX and len(build) <= BUILD_MAX


_INSERT = (
    "INSERT INTO {table} (id, project_id, build_number, jenkins_job, status, "
    "ingestion_source, failed_tests, total_tests, primary_suite_name) "
    "VALUES (:id, :p, :b, :job, 'PASSED', 'unknown', 0, 1, :s)"
)


async def test_the_widest_legal_run_inserts_at_head(conn):
    _check_lengths()
    project = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO projects (id, name, slug, is_active) VALUES (:id, :n, :n, true)"
    ), {"id": project, "n": f"d1-{project.hex[:10]}"})
    for name, suite, build in WIDE_ROWS:
        await conn.execute(text(_INSERT.format(table="test_runs")), {
            "id": uuid.uuid4(), "p": project, "b": build, "job": f"d1-{name}", "s": suite,
        })
    count = (await conn.execute(
        text("SELECT count(*) FROM test_runs WHERE project_id = :p"), {"p": project}
    )).scalar_one()
    assert count == len(WIDE_ROWS)


async def _probe_with_wide_rows(conn) -> str:
    probe = f"d1_probe_{uuid.uuid4().hex[:8]}"
    await conn.execute(text(f"CREATE TABLE {probe} (LIKE test_runs INCLUDING DEFAULTS)"))
    for name, suite, build in WIDE_ROWS:
        await conn.execute(text(_INSERT.format(table=probe)), {
            "id": uuid.uuid4(), "p": uuid.uuid4(), "b": build, "job": f"d1-{name}", "s": suite,
        })
    return probe


@pytest.mark.parametrize("migration", [M0167, M0169, M0170], ids=["0167", "0169", "0170"])
async def test_each_index_builds_over_a_table_already_holding_them(conn, migration):
    """What an upgrade of a database that already holds such runs does."""
    probe = await _probe_with_wide_rows(conn)
    await conn.execute(text(f"CREATE INDEX {probe}_ix ON {probe} {migration.DEFINITION}"))


async def test_the_first_0169_definition_overflows_these_rows(conn):
    """Keeps the check above honest: the rows really are too wide for it."""
    probe = await _probe_with_wide_rows(conn)
    savepoint = await conn.begin_nested()
    with pytest.raises(DBAPIError, match="index row size"):
        await conn.execute(text(f"CREATE INDEX {probe}_old ON {probe} {FIRST_0169}"))
    await savepoint.rollback()
