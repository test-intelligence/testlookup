"""The launch QA fixes that were tested only with scripted sessions, on a real database.

* A manual quarantine proposal on a test that already has a live request keeps the detector's
  measured flip rate and rationale (O-01).
* ``count_requests`` returns the number of rows ``list_requests`` returns without its limit.
* A test case holding an automation status from before the write rules ("manual", as the demo seed
  stored it) can be edited, sent back whole, and restored; a new out-of-vocabulary value is refused.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (skips otherwise), a unique marker,
cleanup by delete. Runs in CI as part of the ``postgres-integration`` job's file list.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    import app.db.postgres as pg
    from app.models.postgres import (
        FlakyQuarantineRequest, ManagedTestCase, Project, ProjectMember, User, UserRole,
    )
    from app.services import cache_service, flaky_quarantine_service

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)
    monkeypatch.setattr(flaky_quarantine_service, "AsyncSessionLocal", factory)
    monkeypatch.setattr(flaky_quarantine_service, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(cache_service, "bump_analytics_epoch", AsyncMock())

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    lead = User(id=uuid.uuid4(), email=f"lead-{marker}@example.test", username=f"lead-{marker}",
                full_name="Priya Lead", hashed_password="x", role=UserRole.QA_LEAD, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Live findings {marker}", slug=f"live-{marker}", is_active=True)
    gone = Project(id=uuid.uuid4(), name=f"Deleted {marker}", slug=f"gone-{marker}", is_active=False)
    quarantined = FlakyQuarantineRequest(
        id=uuid.uuid4(), project_id=project.id, test_fingerprint=f"fp-q-{marker}", test_name="test_pay",
        status="QUARANTINED", detection_method="flip_rate", flip_rate=0.4, flip_window_size=20,
        pass_count=12, fail_count=8, rationale={"source": "flip_detector", "flips": 8},
    )
    others = [
        # One live request per fingerprint (``ux_fqr_live_per_fingerprint``).
        FlakyQuarantineRequest(id=uuid.uuid4(), project_id=project.id, test_fingerprint=f"fp-{index}-{marker}",
                               status=status, detection_method="flip_rate")
        for index, status in enumerate(("PROPOSED", "PROPOSED", "RELEASED", "REJECTED"))
    ]
    hidden = FlakyQuarantineRequest(id=uuid.uuid4(), project_id=gone.id, test_fingerprint=f"fp-gone-{marker}",
                                    status="PROPOSED", detection_method="flip_rate")
    case = ManagedTestCase(
        id=uuid.uuid4(), project_id=project.id, title=f"Checkout total {marker}", status="draft", version=1,
        priority="high", severity="major", test_type="functional", automation_status="manual",
    )
    async with factory() as db:
        db.add_all([lead, project, gone])
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=lead.id, role="QA_LEAD"))
        db.add_all([quarantined, *others, hidden, case])
        await db.commit()
    try:
        yield SimpleNamespace(factory=factory, lead=lead, project=project, gone=gone, quarantined=quarantined,
                              case=case, marker=marker)
    finally:
        async with factory() as db:
            for pid in (project.id, gone.id):
                await db.execute(delete(FlakyQuarantineRequest).where(FlakyQuarantineRequest.project_id == pid))
            await db.execute(delete(ManagedTestCase).where(ManagedTestCase.project_id == project.id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
            await db.execute(delete(Project).where(Project.id.in_([project.id, gone.id])))
            await db.commit()
        async with factory() as db:
            try:
                await db.execute(delete(User).where(User.id == lead.id))
                await db.commit()
            except Exception:  # an audit or version row may still reference the user
                await db.rollback()
        await engine.dispose()


# ── O-01 on a real database ──────────────────────────────────────────────────


async def test_a_manual_proposal_keeps_a_live_requests_measured_evidence(world):
    from app.models.postgres import FlakyQuarantineRequest
    from app.services.flaky_quarantine_service import propose_quarantine

    w = world
    row = await propose_quarantine(
        project_id=w.project.id, test_fingerprint=w.quarantined.test_fingerprint, test_name=None, suite_name=None,
        detection_method="manual", flip_rate=None, flip_window_size=None,
        rationale={"reason": "flips in CI", "source": "mcp"}, actor=w.lead,
    )

    assert row is not None and row.id == w.quarantined.id
    async with w.factory() as db:
        stored = await db.get(FlakyQuarantineRequest, w.quarantined.id)
    assert (stored.status, stored.flip_rate, stored.flip_window_size) == ("QUARANTINED", 0.4, 20)
    assert stored.rationale == {
        "source": "flip_detector", "flips": 8, "manual_proposal": {"reason": "flips in CI", "source": "mcp"},
    }


# ── the quarantine count on a real database ──────────────────────────────────


@pytest.mark.parametrize(
    ("filters", "expected"),
    [({}, 5), ({"status_filter": "PROPOSED"}, 2), ({"live_only": True}, 3)],
    ids=["all", "proposed", "live"],
)
async def test_the_quarantine_count_matches_the_unlimited_list(world, filters, expected):
    from app.services.flaky_quarantine_service import count_requests, list_requests

    w = world
    async with w.factory() as db:
        listed = await list_requests(db, project_ids={w.project.id, w.gone.id}, limit=500, **filters)
        first = await list_requests(db, project_ids={w.project.id, w.gone.id}, limit=1, **filters)
        counted = await count_requests(db, project_ids={w.project.id, w.gone.id}, **filters)

    assert counted == len(listed) == expected  # the deleted project's request is in neither
    assert len(first) == 1


# ── a case holding a value from before the write rules ───────────────────────


async def _edit(w, change_type="updated", **fields):
    from app.models.postgres import ManagedTestCase, User
    from app.models.schemas import ManagedTestCaseUpdate
    from app.services.test_management_service import update_managed_test_case

    async with w.factory() as db:
        current = await db.get(ManagedTestCase, w.case.id)
        actor = await db.get(User, w.lead.id)
        payload = ManagedTestCaseUpdate(expected_version=current.version, **fields)
        edited = await update_managed_test_case(db, w.case.id, payload, actor, change_type=change_type)
        await db.commit()
        return edited.version, edited.automation_status, edited.title


async def test_a_case_with_a_legacy_automation_status_can_be_edited_and_sent_back_whole(world):
    w = world

    version, status, title = await _edit(w, title=f"Checkout total v2 {w.marker}", automation_status="manual")

    assert (version, status, title) == (2, "manual", f"Checkout total v2 {w.marker}")


async def test_a_legacy_value_cannot_be_chosen_again_once_changed_but_can_be_restored(world):
    from app.models.postgres import ManagedTestCase

    w = world
    assert (await _edit(w, automation_status="Automated"))[1] == "automated"

    with pytest.raises(HTTPException) as refused:
        await _edit(w, automation_status="manual")
    assert refused.value.status_code == 422

    assert (await _edit(w, change_type="restored", automation_status="manual"))[1] == "manual"
    async with w.factory() as db:
        stored = (await db.execute(select(ManagedTestCase.automation_status, ManagedTestCase.version)
                                   .where(ManagedTestCase.id == w.case.id))).one()
    assert tuple(stored) == ("manual", 3)
