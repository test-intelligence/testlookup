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
