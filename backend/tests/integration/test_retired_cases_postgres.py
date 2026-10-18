"""W28 TCM-02 against real PostgreSQL: a retired case leaves the counts.

A deprecated case leaves the ``never_executed`` evidence gap, the Suites tab's
manual count and the duplicate review queue (its page, total and open count);
reinstated, it is back in all three. The suite's own case listing keeps it
throughout (with ``lifecycle_status``). The statements are the real ones:
``list_test_case_evidence_gaps``, ``list_test_suites``' raw SQL, and the
duplicates router's correlated EXISTS.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    DuplicateTestCaseCandidate,
    ManagedTestCase,
    Project,
    ProjectMember,
    TestCaseAuditLog as AuditRow,
    User,
    UserRole,
)
from app.routers.duplicates import list_duplicate_candidates
from app.routers.test_management_exports import get_suite_test_cases, list_test_suites
from app.services.test_case_lifecycle_service import transition
from app.services.test_suite_service import list_test_case_evidence_gaps

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SUITE = "W28 Retired Suite"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:12]
    lead = User(
        id=uuid.uuid4(), email=f"w28-retired-{token}@example.invalid", username=f"w28-retired-{token}",
        full_name="W28 Retired Lead", hashed_password="not-a-real-password", role=UserRole.QA_LEAD,
        is_active=True,
    )
    project = Project(id=uuid.uuid4(), name=f"W28 retired {token}", slug=f"w28-retired-{token}")
    ids = sorted(uuid.uuid4() for _ in range(3))
    keep_id, retire_id, partner_id = ids[0], ids[1], ids[2]
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([lead, project])
        await session.flush()
        session.add(ProjectMember(user_id=lead.id, project_id=project.id, role=UserRole.QA_LEAD.value))
        for case_id, title in ((keep_id, "Pay by card"), (retire_id, "Pay by cheque"), (partner_id, "Pay by check")):
            session.add(ManagedTestCase(
                id=case_id, project_id=project.id, title=title, status="active", version=1,
                suite_name=SUITE, priority="medium", severity="major", test_type="functional",
                is_automated=False, automation_status="not_automated", ai_generated=False,
            ))
        await session.flush()
        pair = DuplicateTestCaseCandidate(
            project_id=project.id, case_a_id=retire_id, case_b_id=partner_id, band="strong", score=0.93,
            reason="title 0.95", method="structural", status="open",
        )
        session.add(pair)
        await session.commit()
    try:
        yield SimpleNamespace(
            factory=factory, lead=lead, project=project,
            keep_id=keep_id, retire_id=retire_id, partner_id=partner_id, pair_id=pair.id,
        )
    finally:
        async with factory() as session:
            await session.execute(delete(AuditRow).where(AuditRow.project_id == project.id))
            await session.execute(delete(Project).where(Project.id == project.id))
            await session.execute(delete(User).where(User.id == lead.id))
            await session.commit()


async def _what_counts(world) -> dict:
    async with world.factory() as session:
        gaps, gap_total = await list_test_case_evidence_gaps(
            session, [world.project.id], kind="never_executed", page=1, size=50
        )
        suites = await list_test_suites(project_id=world.project.id, db=session, current_user=world.lead)
        queue = await list_duplicate_candidates(
            project_id=world.project.id, band=None, status_filter="open", page=1, size=50,
            db=session, current_user=world.lead, _=None,
        )
        listing = await get_suite_test_cases(
            suite_name=SUITE, project_id=world.project.id, page=1, size=50, limit=None,
            db=session, current_user=world.lead,
        )
    suite_count = next((s["test_count"] for s in suites if s["suite_name"] == SUITE), 0)
    return {
        "never_executed": sorted(str(g["id"]) for g in gaps),
        "never_executed_total": gap_total,
        "suite_count": suite_count,
        "queue": [str(item.id) for item in queue.items],
        "queue_total": queue.total,
        "open_count": queue.open_count,
        "listed": {item["id"]: item.get("lifecycle_status") for item in listing["items"]},
    }


async def _move(world, action: str, reason: str):
    async with world.factory() as session:
        lead = await session.get(User, world.lead.id)
        await transition(session, world.retire_id, action, lead, reason=reason)
        await session.commit()


async def test_a_deprecated_case_leaves_the_gaps_the_suite_count_and_the_queue_and_comes_back(world):
    every = sorted(str(i) for i in (world.keep_id, world.retire_id, world.partner_id))
    before = await _what_counts(world)
    assert before["never_executed"] == every and before["never_executed_total"] == 3
    assert before["suite_count"] == 3
    assert (before["queue"], before["queue_total"], before["open_count"]) == ([str(world.pair_id)], 1, 1)

    await _move(world, "deprecate", "Cheques are no longer accepted")
    retired = await _what_counts(world)
    assert str(world.retire_id) not in retired["never_executed"]
    assert retired["never_executed_total"] == 2
    assert retired["suite_count"] == 2
    assert (retired["queue"], retired["queue_total"], retired["open_count"]) == ([], 0, 0)
    # Listed, not counted: the suite's own listing keeps the retired case.
    assert retired["listed"][str(world.retire_id)] == "deprecated"
    assert retired["listed"][str(world.keep_id)] == "active"
    async with world.factory() as session:
        stored = await session.get(DuplicateTestCaseCandidate, world.pair_id)
        assert stored.status == "open"  # read-time: the pair itself is untouched

    await _move(world, "archive", "Archived after the release")
    archived = await _what_counts(world)
    assert (archived["queue_total"], archived["open_count"], archived["suite_count"]) == (0, 0, 2)

    await _move(world, "reinstate", "Cheques are back")
    back = await _what_counts(world)
    assert back["never_executed"] == every
    assert back["suite_count"] == 3
    assert (back["queue"], back["queue_total"], back["open_count"]) == ([str(world.pair_id)], 1, 1)
    assert back["listed"][str(world.retire_id)] == "draft"
