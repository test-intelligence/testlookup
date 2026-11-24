"""D-78: the Audit dashboard names one person the same way on every row.

The access writer snapshots ``username``, the test-management writer
``full_name or username``, so one person read "zqxwadmin" on one row and
"Zqxwadmin" on the next. ``query_unified_audit`` now shows a live account's
current ``full_name or username`` and keeps the stored snapshot as
``actor_name_recorded``; an erased account keeps the stored name (the
compliance projection, ``tests/test_erased_actor_projection.py``).

Real ``query_unified_audit`` over the SQLite world of
``test_subject_erasure_service.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.postgres import Base, User
from app.services.audit_dashboard_service import query_unified_audit
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

pytestmark = pytest.mark.regression

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]


async def _insert(maker: Any, table: str, **values: Any) -> None:
    async with maker.session() as db:
        await db.execute(Base.metadata.tables[table].insert().values(id=uuid.uuid4(), **values))
        await db.commit()


async def test_one_person_reads_the_same_on_access_and_test_management_rows(maker, world):
    if "test_case_audit_logs" not in maker.created:  # pragma: no cover - SQLite could not build it
        pytest.skip("test_case_audit_logs is not available in the SQLite world")
    admin = world.admin  # username "zqxwadmin", full_name "Zqxwadmin"
    now = datetime.now(timezone.utc)
    project = uuid.uuid4()
    await _insert(maker, "access_audit_logs", action="membership_added", project_id=project,
                  actor_user_id=admin.id, actor_name=admin.username, created_at=now - timedelta(minutes=2))
    await _insert(maker, "test_case_audit_logs", action="updated", project_id=project, entity_type="test_case",
                  entity_id=uuid.uuid4(), actor_id=admin.id, actor_name=admin.full_name,
                  created_at=now - timedelta(minutes=1))

    async with maker.session() as db:
        feed = await query_unified_audit(db, allowed_project_ids=None, days=14, page_size=50)

    mine = [item for item in feed["items"] if item.get("actor_id") == str(admin.id)]
    assert len(mine) == 2
    assert {item["actor_name"] for item in mine} == {"Zqxwadmin"}
    # The stored snapshot is still there for whoever needs the record as written.
    assert {item["actor_name_recorded"] for item in mine} == {"zqxwadmin", "Zqxwadmin"}


async def test_an_erased_account_keeps_the_stored_name(maker, world):
    subject = world.subject
    async with maker.session() as db:
        row = await db.get(User, subject.id)
        row.erased_at = datetime.now(timezone.utc)
        row.full_name = None
        row.username = "erased-0001"
        await db.commit()
    await _insert(maker, "access_audit_logs", action="membership_added", project_id=uuid.uuid4(),
                  actor_user_id=subject.id, actor_name="zqxwsubj", created_at=datetime.now(timezone.utc))

    async with maker.session() as db:
        feed = await query_unified_audit(db, allowed_project_ids=None, days=14, page_size=50)

    (item,) = [i for i in feed["items"] if i.get("actor_id") == str(subject.id)]
    assert item["actor_name"] == "zqxwsubj"
