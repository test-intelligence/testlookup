"""W11 REL-03 against a real PostgreSQL: the portfolio trend's four reads.

Seeded over three weeks: project A (a release in progress with a GO then a
NO_GO whose ``blocking_reasons`` is the JSON scalar ``null``, a released one
with a live pack, a planning one), project B (in progress, NOT_EVALUATED with a
blocking reason, a failed phase), a DELETED project C and a foreign tenant D,
plus rows the statements must NOT read (a phase-level GO, a retired pack, a
decision after ``now``).

1. Project A's series equal the pure bucketing of exactly the rows the reads
   should return, and the NOW point is the cards' count;
2. All Projects as a member of A, B and the deleted C: A and B counted, C and
   the foreign D not; a non-member of A asking for A -> 403;
3. the JSON ``null`` reasons do not raise (the ``json_typeof`` guard), and the
   trend is FOUR statements; a scope with no release is one.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
everything runs in one outer transaction that is rolled back.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DAY = timedelta(days=1)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine):
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            factory = async_sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
            async with factory() as session:
                yield session
        finally:
            await outer.rollback()


@pytest.fixture
async def world(db):
    from app.models.postgres import (
        CompliancePack, Project, ProjectMember, Release, ReleaseGateDecision, ReleasePhase, User, UserRole,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    tag = uuid.uuid4().hex[:10].translate(str.maketrans("0123456789", "ghijklmnop"))

    def project(name, active=True):
        return Project(id=uuid.uuid4(), name=f"{name} {tag}", slug=f"{name.lower()}-{tag}", is_active=active)

    a, b, c, d = project("A"), project("B"), project("C", active=False), project("D")
    member = User(id=uuid.uuid4(), email=f"m-{tag}@example.test", username=f"m-{tag}", full_name="M",
                  hashed_password="x", role=UserRole.QA_ENGINEER, is_active=True)
    outsider = User(id=uuid.uuid4(), email=f"o-{tag}@example.test", username=f"o-{tag}", full_name="O",
                    hashed_password="x", role=UserRole.QA_ENGINEER, is_active=True)
    db.add_all([a, b, c, d, member, outsider])
    await db.flush()
    for p in (a, b, c):
        db.add(ProjectMember(id=uuid.uuid4(), project_id=p.id, user_id=member.id, role=UserRole.QA_ENGINEER.value))
    db.add(ProjectMember(id=uuid.uuid4(), project_id=d.id, user_id=outsider.id, role=UserRole.QA_ENGINEER.value))

    def release(p, name, status, created, released=None):
        return Release(id=uuid.uuid4(), project_id=p.id, name=f"{name}-{tag}", status=status,
                       created_at=created, released_at=released)

    a1 = release(a, "a1", "in_progress", now - 20 * DAY)
    a2 = release(a, "a2", "released", now - 25 * DAY, now - 9 * DAY)
    a3 = release(a, "a3", "planning", now - 5 * DAY)
    a4 = release(a, "a4", "released", now - 24 * DAY, now - 8 * DAY)    # its only pack is retired
    b1 = release(b, "b1", "in_progress", now - 18 * DAY)
    c1 = release(c, "c1", "in_progress", now - 30 * DAY)
    d1 = release(d, "d1", "in_progress", now - 30 * DAY)
    db.add_all([a1, a2, a3, a4, b1, c1, d1])
    await db.flush()
    b1_failed = ReleasePhase(id=uuid.uuid4(), release_id=b1.id, name="Regression", status="FAILED", order_index=0)
    a2_phase = ReleasePhase(id=uuid.uuid4(), release_id=a2.id, name="Smoke", status="completed", order_index=0)
    db.add_all([b1_failed, a2_phase])
    await db.flush()

    def decision(rel, verdict, at, *, reasons=None, phase=None, current=False):
        return ReleaseGateDecision(id=uuid.uuid4(), release_id=rel.id, phase_id=getattr(phase, "id", None),
                                   is_current=current, verdict=verdict, denominator=10, evidence_count=10,
                                   blocking_reasons=reasons, created_at=at)

    db.add_all([
        decision(a1, "GO", now - 16 * DAY),
        decision(a1, "NO_GO", now - 2 * DAY, reasons=None, current=True),    # JSON null reasons
        decision(a2, "GO", now - 12 * DAY, current=True),
        decision(a2, "NO_GO", now - 11 * DAY, phase=a2_phase, current=True),  # a PHASE row: never read
        decision(b1, "NOT_EVALUATED", now - 10 * DAY, reasons=["checkout::test_pay is FAILED"], current=True),
        decision(b1, "GO", now + 2 * DAY),                                     # after now: never read
        decision(c1, "GO", now - 3 * DAY, current=True),
        decision(d1, "GO", now - 3 * DAY, current=True),
    ])

    def pack(rel, expires):
        return CompliancePack(id=uuid.uuid4(), release_id=rel.id, project_id=rel.project_id,
                              minio_key=f"packs/{uuid.uuid4()}.zip", manifest_sha256="0" * 64,
                              retention_expires_at=expires)

    db.add_all([pack(a2, now + 400 * DAY), pack(a4, now - DAY)])
    await db.flush()
    return SimpleNamespace(now=now, a=a, b=b, c=c, d=d, member=member, outsider=outsider,
                           a1=a1, a2=a2, a3=a3, a4=a4, b1=b1, c1=c1, d1=d1)


def _row(rel):
    return SimpleNamespace(id=rel.id, status=rel.status, created_at=rel.created_at, released_at=rel.released_at)


def _dec(rel, verdict, at, reasons=False):
    return SimpleNamespace(release_id=rel.id, verdict=verdict, created_at=at, has_reasons=reasons)


async def test_project_a_reads_exactly_its_rows(db, world):
    from app.services.release_portfolio_service import bucket_portfolio, portfolio_trend

    now = world.now
    out = await portfolio_trend(db, project_ids={world.a.id}, weeks=12, now=now)

    expected = bucket_portfolio(
        [_row(r) for r in (world.a1, world.a2, world.a3, world.a4)],
        [_dec(world.a1, "GO", now - 16 * DAY), _dec(world.a1, "NO_GO", now - 2 * DAY),
         _dec(world.a2, "GO", now - 12 * DAY)],
        set(), {world.a2.id}, now=now, weeks=12,
    )
    for key in ("in_progress", "ready_to_ship", "blocked", "released_30d", "evaluated_in_week", "go_in_week",
                "totals", "points_at"):
        assert out[key] == expected[key], key
    # The cards at NOW: a1 in progress and blocked (NO_GO), a2 + a4 released in 30 days.
    assert [out[k][-1] for k in ("in_progress", "ready_to_ship", "blocked", "released_30d")] == [1, 0, 1, 2]
    assert out["totals"] == {"evaluated": 2, "go": 1, "conditional_go": 0, "no_go": 1, "not_evaluated": 0,
                             "released": 2, "released_with_pack": 1}   # a4's pack is retired
    assert out["in_progress"][0] is None          # twelve weeks ago no release of A existed


async def test_all_projects_counts_the_members_active_projects_only(db, world):
    from app.routers.releases import get_release_portfolio_trend

    out = await get_release_portfolio_trend(project_id=None, weeks=12, db=db, current_user=world.member)

    assert out["project_id"] is None
    # a1 + b1 in progress; both blocked (a1 NO_GO; b1 a blocking reason and a failed phase).
    # C is deleted and D is another tenant's: neither counted (with them, 4 and 4).
    assert out["in_progress"][-1] == 2
    assert out["blocked"][-1] == 2
    assert out["totals"]["evaluated"] == 3        # a1, a2, b1
    assert out["totals"]["not_evaluated"] == 1 and out["totals"]["go"] == 1


async def test_a_non_member_is_refused(db, world):
    from app.routers.releases import get_release_portfolio_trend

    with pytest.raises(HTTPException) as exc:
        await get_release_portfolio_trend(project_id=str(world.a.id), weeks=12, db=db, current_user=world.outsider)
    assert exc.value.status_code == 403


async def test_four_statements_and_none_beyond_the_first_for_an_empty_scope(engine, db, world):
    from app.services.release_portfolio_service import portfolio_trend

    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        if "SAVEPOINT" not in statement.upper():
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        await portfolio_trend(db, project_ids={world.a.id, world.b.id}, weeks=12, now=world.now)
        assert len(statements) == 4, statements
        assert "json_typeof" in statements[1]
        statements.clear()
        out = await portfolio_trend(db, project_ids={uuid.uuid4()}, weeks=12, now=world.now)
        assert len(statements) == 1
        assert out["in_progress"] == [None] * 13
        statements.clear()
        await portfolio_trend(db, project_ids=set(), weeks=12, now=world.now)
        assert statements == []
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
