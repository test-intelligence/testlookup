"""D-0018 on real PostgreSQL: each stored audit row is in exactly one category of the unified feed.

The audit dashboard's All feed listed every ``report_*`` AccessAuditLog row twice (once as access, once as
report) and counted it twice. The SQLite regression (``tests/regression/test_audit_unified_feed_counts_once.py``)
drives the same function; this pins the shipped LIKE semantics (case-sensitive, ``_`` escaped by ``autoescape``).

Same fixture pattern as ``test_defects_list_fields_postgres.py``: requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a
database migrated to head, skips otherwise. Nothing is committed: the session is rolled back. Rows are scoped to
a fresh project, so other rows in a shared CI database do not count.

Runs in CI as part of the ``postgres-integration`` job's file list in ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

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


async def _world(db):
    from app.models.postgres import AccessAuditLog, Project

    token = uuid.uuid4().hex[:10]
    project = Project(id=uuid.uuid4(), name=f"audit {token}", slug=f"audit-{token}")
    db.add(project)
    await db.flush()
    now = datetime.now(timezone.utc)
    for minutes, action, actor_name in (
        (5, "report_share_accessed", "anonymous_share"),
        (4, "membership_added", "Ann Example"),
        (3, "reportx_probe", "Ann Example"),
        (2, "Report_Mixed_Case", "Ann Example"),
    ):
        db.add(AccessAuditLog(
            id=uuid.uuid4(), action=action, actor_name=actor_name, project_id=project.id,
            created_at=now - timedelta(minutes=minutes),
        ))
    await db.flush()
    return project.id


async def test_all_is_the_disjoint_union_of_access_and_report(session) -> None:
    from app.services.audit_dashboard_service import query_unified_audit

    project_id = await _world(session)

    async def feed(category=None):
        answer = await query_unified_audit(
            session, category=category, days=14, page_size=50, allowed_project_ids={project_id},
        )
        return answer, sorted((item["action"], item["source"]) for item in answer["items"])

    everything, all_rows = await feed()
    report, report_rows = await feed("report")
    access, access_rows = await feed("access")

    assert report_rows == [("report_share_accessed", "report")]
    # ``reportx_probe`` is not ``report_`` (the ``_`` is literal), and LIKE is case-sensitive on Postgres.
    assert access_rows == [
        ("Report_Mixed_Case", "access"), ("membership_added", "access"), ("reportx_probe", "access"),
    ]
    assert all_rows == sorted(report_rows + access_rows)
    assert (everything["total"], report["total"], access["total"]) == (4, 1, 3)
