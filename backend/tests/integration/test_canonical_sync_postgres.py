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

    # W32 (deliberate): the count map always carries first_seen_backfilled.
    assert counts == {"added": 0, "updated": 1, "linked": 1, "skipped": 0, "first_seen_backfilled": 0}
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


# ── W24 F-6 (W32): first RETAINED sighting ───────────────────────────────────


async def _orphan_canonical(engine, project_id, suite_id, fp) -> uuid.UUID:
    """A catalog row with NULL run pointers (an imported catalog's shape)."""
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = CanonicalTestCase(project_id=project_id, test_suite_id=suite_id, test_fingerprint=fp,
                                test_name=f"t_{fp[:8]}", first_seen_run_id=None, last_seen_run_id=None)
        db.add(row)
        await db.commit()
        return row.id


async def _link(engine, run_id, fp, canonical_id) -> None:
    async with AsyncSession(engine) as db:
        await db.execute(
            text("UPDATE test_cases SET canonical_test_case_id = :c WHERE test_run_id = :r AND test_fingerprint = :f"),
            {"c": canonical_id, "r": run_id, "f": fp},
        )
        await db.commit()


async def _added_on(engine, project_id, run_id) -> set[str]:
    from app.services.suite_membership_cutover import canonical_changes

    async with AsyncSession(engine) as db:
        events = await canonical_changes(db, suite_name="Suite A", project_id=project_id, run_id=run_id, limit=50)
    return {e["test_fingerprint"] for e in events if e["event_type"] == "added"}


async def test_an_imported_catalog_row_takes_its_first_ingest_as_first_sighting(engine, world):
    fp = _fp()
    canonical_id = await _orphan_canonical(engine, world.project, world.suite_a, fp)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, [fp])
        await db.commit()
    # the ingest-time step anchor links the run's own case before the sync pass
    await _link(engine, world.run, fp, canonical_id)

    async with AsyncSession(engine, expire_on_commit=False) as db:
        counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        await db.commit()

    assert counts["first_seen_backfilled"] == 1
    row = await _canonical(engine, world.project, fp)
    assert (row.first_seen_run_id, row.last_seen_run_id) == (world.run, world.run)
    assert fp in await _added_on(engine, world.project, world.run)


async def test_a_purged_first_run_with_a_kept_sighting_stays_null_and_adds_nothing(engine, world):
    fp = _fp()
    canonical_id = await _orphan_canonical(engine, world.project, world.suite_a, fp)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.other_run, [fp])          # a KEPT run already saw it
        await _add_cases(db, world.run, [fp])
        await db.commit()
    await _link(engine, world.other_run, fp, canonical_id)

    async with AsyncSession(engine, expire_on_commit=False) as db:
        counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        await db.commit()

    assert counts["first_seen_backfilled"] == 0
    row = await _canonical(engine, world.project, fp)
    assert row.first_seen_run_id is None and row.last_seen_run_id == world.run
    assert fp not in await _added_on(engine, world.project, world.run), "no false 'added' after a purge"


async def test_a_runs_reset_survivor_is_backfilled(engine, world):
    fp = _fp()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(CanonicalTestCase(project_id=world.project, test_suite_id=world.suite_a, test_fingerprint=fp,
                                 test_name="t", first_seen_run_id=world.other_run, last_seen_run_id=world.other_run))
        await _add_cases(db, world.other_run, [fp])
        await db.commit()
    canonical_id = (await _canonical(engine, world.project, fp)).id
    await _link(engine, world.other_run, fp, canonical_id)
    async with AsyncSession(engine) as db:                   # the reset: the run goes, pointers SET NULL
        await db.execute(text("DELETE FROM test_runs WHERE id = :r"), {"r": world.other_run})
        await db.commit()
    assert (await _canonical(engine, world.project, fp)).first_seen_run_id is None

    async with AsyncSession(engine, expire_on_commit=False) as db:
        await _add_cases(db, world.run, [fp])
        counts = await svc.sync_canonical_test_cases(db, world.project, world.run)
        await db.commit()

    assert counts["first_seen_backfilled"] == 1
    assert (await _canonical(engine, world.project, fp)).first_seen_run_id == world.run
    assert fp in await _added_on(engine, world.project, world.run)
