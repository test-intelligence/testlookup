"""Canonical sync inserts new fingerprints in batches, and still survives a race.

Re-audit M8. ``sync_canonical_test_cases`` inserted each new fingerprint in its
own SAVEPOINT + INSERT + RELEASE, so a run's first ingest paid three round trips
per test. It now collects them, inserts them in chunks with
``INSERT ... ON CONFLICT DO NOTHING``, and re-selects to link every case.

What the savepoint protected must still hold, and only real Postgres can show
it. A concurrent run's sync committing the same fingerprint between this
call's prefetch and its insert must cost nothing: no exception, nothing else in
the batch lost, every case linked. And the winner's suite -- possibly a
person's manual move -- must never be overwritten.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.postgres import CanonicalTestCase, Project, TestCase, TestRun
from app.services import test_suite_service as svc

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _fp() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64 chars, the column width


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def world(engine):
    """A committed project with two runs and two suites, removed afterwards."""
    project_id = uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(Project(id=project_id, name="m8", slug=f"m8-{project_id.hex}"))
        await db.flush()
        run = TestRun(project_id=project_id, build_number=f"m8-{uuid.uuid4().hex[:12]}")
        other = TestRun(project_id=project_id, build_number=f"m8-{uuid.uuid4().hex[:12]}")
        db.add_all([run, other])
        await db.flush()
        suite_a = await svc.get_or_create_suite_by_name(db, project_id, "Suite A")
        suite_b = await svc.get_or_create_suite_by_name(db, project_id, "Suite B")
        await db.commit()
        ids = SimpleNamespace(
            project=project_id, run=run.id, other_run=other.id,
            suite_a=suite_a.id, suite_b=suite_b.id,
        )
    yield ids
    async with engine.begin() as conn:
        # Canonicals first: their suite foreign key is RESTRICT.
        await conn.execute(
            text("DELETE FROM canonical_test_cases WHERE project_id = :id"), {"id": project_id}
        )
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


async def _add_cases(db, run_id, fingerprints, suite_name="Suite A") -> None:
    db.add_all([
        TestCase(
            test_run_id=run_id, test_name=f"t_{fp[:8]}", test_fingerprint=fp,
            suite_name=suite_name, class_name="C", status="PASSED",
        )
        for fp in fingerprints
    ])
    await db.flush()


def _record_statements(engine):
    seen: list[str] = []

    def _before(_conn, _cursor, statement, _params, _context, _executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    return seen, lambda: event.remove(engine.sync_engine, "before_cursor_execute", _before)


async def _unlinked(engine, run_id) -> int:
    async with AsyncSession(engine) as db:
        return (await db.execute(
            select(func.count()).select_from(TestCase).where(
                TestCase.test_run_id == run_id, TestCase.canonical_test_case_id.is_(None)
            )
        )).scalar_one()


async def _canonical(engine, project_id, fp) -> CanonicalTestCase:
    async with AsyncSession(engine) as db:
        return (await db.execute(
            select(CanonicalTestCase).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint == fp,
            )
        )).scalar_one()


async def test_a_first_ingest_is_batched_and_links_every_case(engine, world):
    fps = [_fp() for _ in range(500)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, fps)
        seen, stop = _record_statements(engine)
        try:
            counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        finally:
            stop()
        await db.commit()

    assert counts["added"] == 500
    assert counts["linked"] == 500
    savepoints = [s for s in seen if s.lstrip().upper().startswith("SAVEPOINT")]
    assert savepoints == [], (
        f"canonical sync still opens a SAVEPOINT per new fingerprint ({len(savepoints)})"
    )
    assert len(seen) < 25, f"{len(seen)} statements for 500 new tests: not batched"
    assert await _unlinked(engine, world.run) == 0


async def test_a_concurrent_winner_is_linked_counted_and_never_re_suited(
    engine, world, monkeypatch
):
    fps = [_fp() for _ in range(4)]
    contested = fps[1]
    real_insert = svc._insert_new_canonicals

    async def _race_then_insert(db, rows):
        # Another run's sync commits the contested fingerprint first -- after
        # this call's prefetch, before its insert: the window the savepoint
        # covered. It lands in Suite B, as a person's manual move would.
        async with AsyncSession(engine) as other:
            other.add(CanonicalTestCase(
                project_id=world.project, test_suite_id=world.suite_b,
                test_fingerprint=contested, test_name="winner",
                first_seen_run_id=world.other_run, last_seen_run_id=world.other_run,
            ))
            await other.commit()
        return await real_insert(db, rows)

    monkeypatch.setattr(svc, "_insert_new_canonicals", _race_then_insert)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, fps)
        counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        await db.commit()

    assert counts["added"] == 3, "the row a concurrent run inserted was counted as ours"
    assert counts["linked"] == 4
    assert await _unlinked(engine, world.run) == 0, (
        "a case whose insert lost the race was left with no canonical"
    )
    winner = await _canonical(engine, world.project, contested)
    assert winner.test_suite_id == world.suite_b, (
        "the winner was re-suited: a manual suite move would be undone by the next run"
    )
    assert winner.first_seen_run_id == world.other_run
    assert winner.last_seen_run_id == world.run, "this run saw the test but did not record it"


async def test_a_manually_moved_case_stays_moved(engine, world):
    fp = _fp()
    async with AsyncSession(engine) as db:
        db.add(CanonicalTestCase(
            project_id=world.project, test_suite_id=world.suite_b, test_fingerprint=fp,
            test_name="t", first_seen_run_id=world.other_run, last_seen_run_id=world.other_run,
        ))
        await db.commit()

    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, [fp], suite_name="Suite A")
        counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        await db.commit()

    assert counts == {"added": 0, "updated": 1, "linked": 1, "skipped": 0}
    assert (await _canonical(engine, world.project, fp)).test_suite_id == world.suite_b


async def test_every_case_is_linked_across_chunk_boundaries(engine, world, monkeypatch):
    monkeypatch.setattr(svc, "_CANONICAL_INSERT_CHUNK", 7)
    monkeypatch.setattr(svc, "_FINGERPRINT_CHUNK", 5)
    fps = [_fp() for _ in range(23)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, fps)
        seen, stop = _record_statements(engine)
        try:
            counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        finally:
            stop()
        await db.commit()

    assert counts["added"] == 23 and counts["linked"] == 23
    inserts = [s for s in seen if s.lstrip().upper().startswith("INSERT INTO CANONICAL_TEST_CASES")]
    assert len(inserts) == 4, f"{len(inserts)} INSERTs for 23 rows in chunks of 7"
    # The prefetch and the re-select each read the 23 fingerprints in chunks of 5.
    selects = [
        s for s in seen
        if s.lstrip().upper().startswith("SELECT") and "FROM canonical_test_cases" in s
    ]
    assert len(selects) == 10, (
        f"{len(selects)} canonical SELECTs for 23 fingerprints in chunks of 5"
    )
    assert await _unlinked(engine, world.run) == 0
