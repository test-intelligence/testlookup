"""W11 REL-05 / REL-07 against a real PostgreSQL: copy and bulk-create a first schedule.

What only a real database proves:

1. a clone into a planning release persists the shifted, Pending phases and
   its ``release.phases_cloned`` row; the copied phase's gate
   (``GET /releases/{id}/phases/gate``) evaluates WITH the copied
   ``exit_criteria`` as its narrowest layer (``policy.layers`` ends with
   ``"phase"``);
2. the bulk create persists its rows in order with its
   ``release.phases_generated`` row, and a second bulk is a 409;
3. two concurrent clones into the same target: exactly one 201 and one 409.
   The race is made deterministic: each request, once its phases are staged,
   waits (at most 1.5 s) for the other to get that far. With the target row
   locked ``FOR UPDATE`` the second request cannot get that far until the
   first commits, then reads its phases and is refused; without the lock both
   would stage a schedule and both commit.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
Rows are committed for real (two connections race), so the project and the
user are deleted at teardown (releases, phases and ledger rows cascade).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
async def world():
    import app.db.postgres as app_postgres
    from app.models.postgres import Project, Release, ReleasePhase, User, UserRole

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    tag = uuid.uuid4().hex[:10].translate(str.maketrans("0123456789", "ghijklmnop"))
    project = Project(id=uuid.uuid4(), name=f"Plan {tag}", slug=f"plan-{tag}", is_active=True)
    lead = User(id=uuid.uuid4(), email=f"lead-{tag}@example.test", username=f"lead-{tag}",
                full_name=f"Lead {tag}", hashed_password="x", role=UserRole.QA_LEAD, is_active=True)
    source = Release(id=uuid.uuid4(), project_id=project.id, name=f"2026.10-{tag}", status="released")
    target = Release(id=uuid.uuid4(), project_id=project.id, name=f"2026.12-{tag}", status="planning",
                     cutoff_start_at=_utc(2026, 11, 3))
    bulk_target = Release(id=uuid.uuid4(), project_id=project.id, name=f"2026.13-{tag}", status="planning")
    phases = [
        ReleasePhase(id=uuid.uuid4(), release_id=source.id, name="Smoke", phase_type="qa_testing",
                     status="completed", order_index=0, planned_start=_utc(2026, 9, 1),
                     planned_end=_utc(2026, 9, 5), actual_start=_utc(2026, 9, 1),
                     exit_criteria={"thresholds": {"go_threshold": 92}}),
        ReleasePhase(id=uuid.uuid4(), release_id=source.id, name="UAT", phase_type="uat",
                     status="completed", order_index=1, planned_start=_utc(2026, 9, 8),
                     planned_end=_utc(2026, 9, 20)),
    ]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([project, lead])
        await db.flush()
        db.add_all([source, target, bulk_target])
        await db.flush()
        db.add_all(phases)
        await db.commit()
    actor = SimpleNamespace(id=lead.id, username=lead.username, full_name=lead.full_name,
                            role=UserRole.QA_LEAD, is_service_account=False)
    try:
        yield SimpleNamespace(engine=engine, project=project, source=source, target=target,
                              bulk_target=bulk_target, actor=actor)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project.id})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": lead.id})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _session(world) -> AsyncSession:
    return AsyncSession(world.engine, expire_on_commit=False)


async def _clone(world, *, shift_to=None):
    from app.models.schemas import ReleasePhasesCloneRequest
    from app.routers.releases import clone_release_phases

    async with _session(world) as db:
        return await clone_release_phases(
            release_id=str(world.target.id),
            body=ReleasePhasesCloneRequest(from_release_id=world.source.id, shift_to=shift_to),
            db=db, current_user=world.actor, __=world.actor,
        )


async def _phases(world, release_id):
    from app.models.postgres import ReleasePhase

    async with _session(world) as db:
        return list((await db.execute(
            select(ReleasePhase).where(ReleasePhase.release_id == release_id).order_by(ReleasePhase.order_index)
        )).scalars().all())


async def _events(world, event_type):
    async with world.engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT summary, context FROM project_activity_events WHERE project_id = :p AND event_type = :e"
        ), {"p": world.project.id, "e": event_type})).all()


async def test_a_clone_persists_and_the_copied_gate_layer_is_evaluated(world):
    out = await _clone(world)   # anchored on the target's cutoff start: 2026-09-01 -> 2026-11-03

    assert out["created"] == 2 and out["shifted_by_days"] == 63
    rows = await _phases(world, world.target.id)
    assert [(r.name, r.status, r.order_index) for r in rows] == [("Smoke", "pending", 0), ("UAT", "pending", 1)]
    assert rows[0].planned_start == _utc(2026, 11, 3)
    assert rows[1].planned_end == _utc(2026, 9, 20) + timedelta(days=63)
    assert rows[0].actual_start is None
    assert rows[0].exit_criteria == {"thresholds": {"go_threshold": 92}}
    [(summary, context)] = await _events(world, "release.phases_cloned")
    assert summary == f"Release {world.target.name}: 2 phases copied from {world.source.name}"
    assert context["shifted_by_days"] == 63

    from app.routers.releases import get_release_phase_gate

    async with _session(world) as db:
        gate = await get_release_phase_gate(release_id=str(world.target.id), db=db, _=world.actor)
    smoke = next(p for p in gate["phases"] if p["phase_name"] == "Smoke")
    assert smoke["policy"]["layers"][-1] == "phase"
    assert smoke["policy"]["document"]["thresholds"]["go_threshold"] == 92
    uat = next(p for p in gate["phases"] if p["phase_name"] == "UAT")
    assert "phase" not in uat["policy"]["layers"]      # no exit criteria copied: no phase layer


async def test_a_bulk_create_persists_in_order_and_a_second_is_refused(world):
    from app.core.release_planning import TARGET_HAS_PHASES
    from app.models.schemas import ReleasePhasesBulkRequest
    from app.routers.releases import create_release_phases_bulk

    body = ReleasePhasesBulkRequest(source="rules", phases=[
        {"name": "Smoke testing", "phase_type": "qa_testing",
         "planned_start": "2026-11-03T00:00:00Z", "planned_end": "2026-11-07T00:00:00Z"},
        {"name": "Regression", "phase_type": "qa_testing"},
        {"name": "UAT", "phase_type": "uat", "description": "Exit criteria: finance signs off"},
    ])
    async with _session(world) as db:
        out = await create_release_phases_bulk(
            release_id=str(world.bulk_target.id), body=body, db=db, current_user=world.actor, __=world.actor,
        )
    assert out["created"] == 3
    rows = await _phases(world, world.bulk_target.id)
    assert [(r.name, r.phase_type, r.order_index, r.status) for r in rows] == [
        ("Smoke testing", "qa_testing", 0, "pending"),
        ("Regression", "qa_testing", 1, "pending"),
        ("UAT", "uat", 2, "pending"),
    ]
    [(summary, _context)] = await _events(world, "release.phases_generated")
    assert summary == f"Release {world.bulk_target.name}: 3 phases created (rules)"

    async with _session(world) as db:
        with pytest.raises(HTTPException) as exc:
            await create_release_phases_bulk(
                release_id=str(world.bulk_target.id), body=body, db=db, current_user=world.actor, __=world.actor,
            )
    assert (exc.value.status_code, exc.value.detail) == (409, TARGET_HAS_PHASES)
    assert len(await _phases(world, world.bulk_target.id)) == 3


async def test_two_concurrent_clones_one_wins_one_is_refused(world, monkeypatch):
    from app.core.release_planning import TARGET_HAS_PHASES
    from app.routers import releases as rel_router

    arrived = 0
    both_staged = asyncio.Event()
    real_record = rel_router.record_activity

    async def rendezvous(*args, **kwargs):
        # Staged, not committed: wait for the other request to get this far.
        nonlocal arrived
        arrived += 1
        if arrived >= 2:
            both_staged.set()
        try:
            await asyncio.wait_for(both_staged.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            pass
        return await real_record(*args, **kwargs)

    monkeypatch.setattr(rel_router, "record_activity", rendezvous)

    async def attempt():
        try:
            return 201, await _clone(world)
        except HTTPException as exc:
            return exc.status_code, exc.detail

    results = await asyncio.gather(attempt(), attempt())

    statuses = sorted(status for status, _ in results)
    assert statuses == [201, 409], results
    assert next(detail for status, detail in results if status == 409) == TARGET_HAS_PHASES
    assert len(await _phases(world, world.target.id)) == 2
    assert len(await _events(world, "release.phases_cloned")) == 1
