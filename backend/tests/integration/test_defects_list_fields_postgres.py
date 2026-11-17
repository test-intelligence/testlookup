"""The defects list carries each defect's own title, severity and component.

``list_defects`` selected none of them, so the Defects page titled a row with
its test's name, or with its category ("product bug") when it had no test, and
derived the severity from the category: the title and P0-P3 a person typed
into New defect never showed (the UX redesign's browser E2E pass, 2026-10-08).

Same fixture pattern as ``test_viz_seed_postgres.py``: requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head, skips
otherwise. Nothing is committed: the session is rolled back.

Runs in CI as part of the ``postgres-integration`` job's file list in
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def session():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        try:
            yield db
        finally:
            await db.rollback()
    await engine.dispose()


async def test_an_intake_defect_lists_with_its_title_severity_and_component(session) -> None:
    from app.models.postgres import Project
    from app.services import analytics_service

    token = uuid.uuid4().hex[:10]
    project = Project(id=uuid.uuid4(), name=f"defects {token}", slug=f"defects-{token}")
    session.add(project)
    await session.flush()

    created = await analytics_service.create_manual_defect(
        session,
        project.id,
        {
            "title": "Refund posts twice under retry",
            "severity": "P3",
            "failure_category": "PRODUCT_BUG",
            "component": "payments-service",
            "test_name": None,
            "suite_name": None,
            "jira_ticket_url": None,
            "description": None,
        },
    )

    listed = await analytics_service.list_defects(session, str(project.id), None, 1, 20)
    (row,) = [item for item in listed["items"] if item["id"] == created.id]
    assert row["title"] == "Refund posts twice under retry"
    # P3 is stored as LOW; a product bug's derived severity would have been P1.
    assert row["severity"] == "LOW"
    assert row["component"] == "payments-service"
    assert listed["total"] == 1
    # W06: the row also carries its project, the server-derived priority, and the
    # owner / assignee / approval / Jira-filing keys (null = not known, never "").
    assert row["project_id"] == project.id
    assert row["priority"] == "P3"
    assert (row["owner_team"], row["owner_source"]) == (None, None)
    assert (row["assignee_user_id"], row["assignee_name"]) == (None, None)
    assert row["approval_status"] == "approved"
    assert (row["jira_filing_status"], row["jira_filing_reason"]) == (None, None)


# ── D-0011: a run linked to two releases lists its defect once ───────────────
#
# GPT-6 (2026-11-15) saw one OPEN P0 twice on the Defects page while the open
# count said 1. ``release_test_run_links`` is many-to-many by design (one link
# per run is ``is_primary``), and ``list_defects`` LEFT JOINed it only to read a
# release name: a run linked twice listed each of its defects twice, while the
# COUNT (which skips that join) said 1.


async def _seed_defect_on_a_twice_linked_run(session, *, title: str = "Refund posts twice"):
    from app.models.postgres import (
        LaunchStatus,
        LinkSource,
        Project,
        Release,
        ReleaseTestRunLink,
        TestCase,
        TestRun,
    )
    from app.services import analytics_service

    token = uuid.uuid4().hex[:10]
    project = Project(id=uuid.uuid4(), name=f"d0011 {token}", slug=f"d0011-{token}")
    primary = Release(id=uuid.uuid4(), project_id=project.id, name=f"2026.11-{token}", status="in_progress")
    other = Release(id=uuid.uuid4(), project_id=project.id, name=f"2026.10-{token}", status="released")
    session.add_all([project, primary, other])
    await session.flush()
    run = TestRun(
        id=uuid.uuid4(), project_id=project.id, primary_release_id=primary.id,
        build_number=f"d0011-{token}", jenkins_job="d0011", branch="main", status=LaunchStatus.FAILED,
    )
    session.add(run)
    await session.flush()
    session.add_all([
        ReleaseTestRunLink(release_id=primary.id, test_run_id=run.id, project_id=project.id,
                           link_source=LinkSource.EXPLICIT_CLIENT.value, is_primary=True),
        # Auto-attributed first, relabelled later: the old link stays (runs_service.link_run_to_release).
        ReleaseTestRunLink(release_id=other.id, test_run_id=run.id, project_id=project.id,
                           link_source=LinkSource.EXPLICIT_CLIENT.value, is_primary=False),
    ])
    case = TestCase(id=uuid.uuid4(), test_run_id=run.id, test_fingerprint=f"fp-{token}",
                    test_name="test_refund_once", suite_name="payments", status="FAILED")
    session.add(case)
    await session.flush()
    defect = await analytics_service.create_manual_defect(
        session, project.id,
        {"title": title, "severity": "P0", "failure_category": "PRODUCT_BUG", "component": None,
         "test_name": "test_refund_once", "suite_name": "payments", "jira_ticket_url": None, "description": None},
    )
    assert defect.test_case_id == case.id, "the defect is on the run linked twice"
    return project, primary, other, defect


async def test_a_defect_whose_run_has_two_release_links_is_listed_once(session) -> None:
    from app.services import analytics_service

    project, primary, _other, defect = await _seed_defect_on_a_twice_linked_run(session)
    listed = await analytics_service.list_defects(session, str(project.id), None, 1, 20)
    assert [item["id"] for item in listed["items"]] == [defect.id]
    assert listed["total"] == 1
    # The run's PRIMARY release names the row, deterministically.
    assert listed["items"][0]["release_name"] == primary.name


async def test_paging_by_one_shows_the_defect_once(session) -> None:
    from app.services import analytics_service

    project, _primary, _other, defect = await _seed_defect_on_a_twice_linked_run(session)
    first = await analytics_service.list_defects(session, str(project.id), None, 1, 1)
    second = await analytics_service.list_defects(session, str(project.id), None, 2, 1)
    assert [item["id"] for item in first["items"]] == [defect.id]
    assert second["items"] == []
    assert first["pages"] == 1


async def test_a_second_distinct_defect_still_lists_twice(session) -> None:
    """Control: the fix removes a fan-out, it does not dedupe by title or priority."""
    from app.services import analytics_service

    project, _primary, _other, first = await _seed_defect_on_a_twice_linked_run(session)
    second = await analytics_service.create_manual_defect(
        session, project.id,
        {"title": "Refund posts twice", "severity": "P0", "failure_category": "PRODUCT_BUG", "component": None,
         "test_name": None, "suite_name": None, "jira_ticket_url": None, "description": None},
    )
    listed = await analytics_service.list_defects(session, str(project.id), None, 1, 20)
    assert sorted(str(item["id"]) for item in listed["items"]) == sorted([str(first.id), str(second.id)])
    assert listed["total"] == 2
