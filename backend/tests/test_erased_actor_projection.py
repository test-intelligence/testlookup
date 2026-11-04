"""W31 STRAT-12 (E-9): "Erased user" is a READ-side projection; the audit rows keep the stored name.

The five gate-protected audit tables are never updated, so the ledger still holds
an erased person's ``actor_name``. The PRODUCT surfaces — the project feed, one
event, the entity timeline, the activity export (``services/activity/query.py``)
and the test-case timeline (``services/test_case_timeline_service.py``) — render
"Erased user" for an erased actor and the stored name for everyone else. The
COMPLIANCE surfaces — the audit dashboard, ``GET /users/{id}/access-audit`` and
the identity events view — keep the stored name (the legal purpose they are kept
for). SQLite world of ``test_subject_erasure_service.py``; times built in the tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.postgres import Base, ManagedTestCase, User
from app.services.activity import query as feed
from app.services.subject_rights_vocab import ERASED_DISPLAY
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

STORED = "Zqxw Subjperson"       # the name the ledger kept for the (now erased) subject
OTHER = "Zqxwadmin Stored"       # a live actor's stored name


@pytest.fixture
async def ledger(maker: Any, world: SimpleNamespace) -> SimpleNamespace:
    """A project, a test case, two ledger rows per kind (one by the erased subject, one by a live admin),
    two comments, and one row in each compliance table naming the subject."""
    now = datetime.now(timezone.utc)
    project_id, case_id = uuid.uuid4(), uuid.uuid4()
    events = Base.metadata.tables["project_activity_events"]
    rows = []
    for offset, (actor, name) in enumerate(((world.subject.id, STORED), (world.admin.id, OTHER))):
        for event_type, category in (("test_case.requirement_linked", "test_management"), ("run.created", "runs")):
            rows.append({
                "id": uuid.uuid4(), "project_id": project_id, "occurred_at": now - timedelta(minutes=offset * 10 + 1),
                "category": category, "event_type": event_type, "actor_type": "user", "actor_id": actor,
                "actor_name": name, "entity_type": "test_case", "entity_id": str(case_id), "summary": "changed",
                "context": {"requirement_key": "REQ-1", "link_type": "covers"},
            })
    async with maker.session() as db:
        await db.execute(Base.metadata.tables["projects"].insert().values(
            id=project_id, name="zqxw project", slug=f"zqxw-{project_id.hex[:8]}"))
        await db.execute(Base.metadata.tables["managed_test_cases"].insert().values(
            id=case_id, project_id=project_id, title="Login works"))
        await db.execute(events.insert(), rows)
        for author in (world.subject.id, world.admin.id):
            await db.execute(Base.metadata.tables["test_case_comments"].insert().values(
                id=uuid.uuid4(), test_case_id=case_id, author_id=author, content="a comment",
                created_at=now - timedelta(minutes=30)))
        await db.execute(Base.metadata.tables["access_audit_logs"].insert().values(
            id=uuid.uuid4(), action="role_changed", actor_user_id=world.subject.id, actor_name=STORED,
            target_user_id=world.subject.id, created_at=now))
        await db.execute(Base.metadata.tables["identity_events"].insert().values(
            id=uuid.uuid4(), event_type="SSO_LOGIN", user_id=world.subject.id, actor_id=world.subject.id,
            actor_name=STORED, success=True, created_at=now))
        await db.execute(Base.metadata.tables["settings_audit_log"].insert().values(
            id=uuid.uuid4(), setting_key="smtp", action="updated", actor_id=world.subject.id, actor_name=STORED,
            created_at=now))
        # the Postgres phase's B1 on the subject row (the ledger rows above are NOT touched)
        row = await db.get(User, world.subject.id)
        row.username, row.full_name, row.is_active = "erased-0a1b2c3d", ERASED_DISPLAY, False
        row.erased_at = now
        await db.commit()
    return SimpleNamespace(project_id=project_id, case_id=case_id, subject=world.subject.id, admin=world.admin.id)


def _names(items: list[dict]) -> dict[str, set[str]]:
    by_actor: dict[str, set[str]] = {}
    for item in items:
        actor = item["actor"] if "actor" in item else {"id": item.get("actor_id"), "name": item.get("actor_name")}
        by_actor.setdefault(str(actor["id"]), set()).add(actor["name"])
    return by_actor


async def test_the_feed_event_and_entity_timeline_mask_an_erased_actor(maker: Any, ledger: SimpleNamespace) -> None:
    async with maker.session() as db:
        page = await feed.list_events(db, ledger.project_id, feed.ActivityFilters())
        timeline = await feed.entity_timeline(db, ledger.project_id, "test_case", str(ledger.case_id))
        ids = {item["actor"]["id"]: uuid.UUID(item["id"]) for item in page["items"]}
        erased_one = await feed.get_event(db, ledger.project_id, ids[str(ledger.subject)])
        live_one = await feed.get_event(db, ledger.project_id, ids[str(ledger.admin)])
        paged = await feed.list_events(db, ledger.project_id, feed.ActivityFilters(limit=1))
    expected = {str(ledger.subject): {ERASED_DISPLAY}, str(ledger.admin): {OTHER}}
    assert _names(page["items"]) == expected
    assert _names(timeline) == expected
    assert erased_one is not None and erased_one["actor"]["name"] == ERASED_DISPLAY and "diff" in erased_one
    assert live_one is not None and live_one["actor"]["name"] == OTHER
    assert len(page["items"]) == 4 and page["next_cursor"] is None
    assert len(paged["items"]) == 1 and paged["next_cursor"] is not None   # the cursor reads the event, not the row


async def test_the_activity_export_masks_an_erased_actor(maker: Any, ledger: SimpleNamespace) -> None:
    async with maker.session() as db:
        rows, truncated = await feed.iter_export_rows(db, ledger.project_id, feed.ActivityFilters())
    assert not truncated and len(rows) == 4
    assert sorted(row["actor_name"] for row in rows) == sorted([ERASED_DISPLAY, ERASED_DISPLAY, OTHER, OTHER])
    assert STORED not in feed.rows_to_csv(rows) and STORED not in feed.rows_to_ndjson(rows)


async def test_the_test_case_timeline_masks_an_erased_actor(maker: Any, ledger: SimpleNamespace) -> None:
    from app.services.test_case_timeline_service import case_timeline

    async with maker.session() as db:
        case = await db.get(ManagedTestCase, ledger.case_id)
        answer = await case_timeline(db, case, kinds=("comment", "requirement"), cursor=None, limit=20)
    by_kind: dict[str, dict[str, set[str]]] = {}
    for entry in answer["items"]:
        by_kind.setdefault(entry["kind"], {}).setdefault(str(entry["actor_id"]), set()).add(entry["actor_name"])
    # requirement rows come from the ledger's STORED name; comments resolve the user row
    assert by_kind["requirement"] == {str(ledger.subject): {ERASED_DISPLAY}, str(ledger.admin): {OTHER}}
    assert by_kind["comment"][str(ledger.subject)] == {ERASED_DISPLAY}
    assert by_kind["comment"][str(ledger.admin)] == {"Zqxwadmin"}


async def test_my_activity_masks_an_erased_actor(maker: Any, world: SimpleNamespace, ledger: SimpleNamespace) -> None:
    from app.services.activity.mine import MyActivityFilters, list_my_activity

    async with maker.session() as db:
        answer = await list_my_activity(
            db, SimpleNamespace(id=ledger.subject), MyActivityFilters(view="mine"),
            scope={ledger.project_id}, member_projects=set(),
        )
    assert answer["items"], "the subject's own rows are part A of /activity/mine"
    assert {item["actor"]["name"] for item in answer["items"]} == {ERASED_DISPLAY}


# ── compliance surfaces keep the STORED name (Art. 17(3)) ─────────────────────

async def test_the_audit_dashboard_keeps_the_stored_name(maker: Any, ledger: SimpleNamespace) -> None:
    from app.services.audit_dashboard_service import query_unified_audit

    async with maker.session() as db:
        answer = await query_unified_audit(db, actor_id=ledger.subject, days=1, redact=False)
    names = {item["actor_name"] for item in answer["items"]}
    assert STORED in names and ERASED_DISPLAY not in names


async def test_access_audit_and_identity_events_keep_the_stored_name(maker: Any, ledger: SimpleNamespace) -> None:
    from app.routers.identity_events import list_identity_events
    from app.services.access_audit_service import get_access_audit_log

    async with maker.session() as db:
        access = await get_access_audit_log(db, target_user_id=ledger.subject, limit=10)
        identity = await list_identity_events(
            event_type=None, user_id=ledger.subject, sso_config_id=None, success=None, days=1, page=1,
            page_size=50, current_user=SimpleNamespace(), db=db,
        )
    assert [row["actor_name"] for row in access] == [STORED]
    assert [item.actor_name for item in identity.items] == [STORED]


async def test_the_ledger_rows_themselves_are_untouched(maker: Any, ledger: SimpleNamespace) -> None:
    """The projection reads; it never writes the audit table (the gate's rule, asserted on the data)."""
    from sqlalchemy import select

    events = Base.metadata.tables["project_activity_events"]
    async with maker.session() as db:
        await feed.list_events(db, ledger.project_id, feed.ActivityFilters())
        stored = (await db.execute(select(events.c.actor_name).where(events.c.actor_id == ledger.subject))).scalars()
        assert sorted(stored.all()) == [STORED, STORED]
