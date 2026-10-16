"""DEF-06: ``GET /api/v1/deep-investigate/defects/pending-review`` is ``{items, total, page, page_size}``.

``total`` is a COUNT over the same scope, not the page length ("a top-N length
is not a count"): the Inbox badge must read 120, not 100. ``project_id`` pins
one project through ``resolve_project_scope`` (400 malformed, 403 foreign); a
soft-deleted project's queue is nobody's work. Driven through the real route
and guards over an in-memory session that evaluates the real query (scope,
pending filter, live-project sub-select, ordering, offset and limit). The
source-scan guards over this handler (``test_pending_defects_are_project_scoped.py``,
``test_remaining_idors_closed.py``, ``test_phase3_performance.py``) run unedited.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.postgres import Project, UserRole
from app.routers import deep_investigation
from tests.fakes.fake_defect_world import MemDB, defect, make_app, membership, user

pytestmark = pytest.mark.asyncio

PID = uuid.uuid4()
QID = uuid.uuid4()
GONE = uuid.uuid4()
RUN = uuid.uuid4()


@pytest.fixture
def world(monkeypatch, fake_redis):
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    lead = user(UserRole.QA_LEAD)
    admin = user(UserRole.ADMIN)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [
        lead, admin, membership(lead, PID, role="QA_LEAD"), membership(lead, GONE, role="QA_LEAD"),
        Project(id=PID, name="p", slug="p", is_active=True),
        Project(id=QID, name="q", slug="q", is_active=True),
        Project(id=GONE, name="gone", slug="gone", is_active=False),
    ]
    for i in range(120):
        rows.append(defect(PID, approval_status="pending_review", created_at=start + timedelta(minutes=i),
                           title=f"pending {i:03d}"))
    rows += [
        defect(PID, approval_status="approved", created_at=start, title="approved"),
        defect(QID, approval_status="pending_review", created_at=start, title="other project"),
        defect(GONE, approval_status="pending_review", created_at=start, title="deleted project"),
    ]
    db = MemDB(rows)

    async def get(query: str, principal=None):
        app = make_app(deep_investigation.router, lambda: db, principal or lead)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await asyncio.wait_for(client.get(f"/api/v1/deep-investigate/defects/pending-review{query}"),
                                          timeout=5)

    return db, lead, admin, get


async def test_total_counts_the_scope_not_the_page(world):
    _db, _lead, _admin, get = world

    first = await get(f"?project_id={PID}&page=1&page_size=100")
    second = await get(f"?project_id={PID}&page=2&page_size=100")

    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert (body["total"], body["page"], body["page_size"], len(body["items"])) == (120, 1, 100, 100)
    assert second.json()["total"] == 120 and len(second.json()["items"]) == 20
    # Newest first, across both pages, with no repeats.
    titles = [i["title"] for i in body["items"]] + [i["title"] for i in second.json()["items"]]
    assert titles[0] == "pending 119" and titles[-1] == "pending 000" and len(set(titles)) == 120


async def test_without_a_project_the_caller_sees_only_live_projects_they_belong_to(world):
    _db, _lead, _admin, get = world

    body = (await get("?page_size=100")).json()

    assert body["total"] == 120
    assert {i["project_id"] for i in body["items"]} == {str(PID)}


async def test_an_admin_sees_every_live_project_but_no_deleted_one(world):
    _db, _lead, admin, get = world

    body = (await get("?page_size=100", principal=admin)).json()

    assert body["total"] == 121
    assert "deleted project" not in {i["title"] for i in body["items"]}


async def test_a_foreign_project_is_403_and_a_malformed_one_400(world):
    _db, _lead, _admin, get = world

    assert (await get(f"?project_id={QID}")).status_code == 403
    assert (await get("?project_id=not-a-uuid")).status_code == 400


async def test_a_deleted_project_pinned_by_a_member_lists_nothing(world):
    _db, _lead, _admin, get = world

    body = (await get(f"?project_id={GONE}")).json()

    assert body["total"] == 0 and body["items"] == []


async def test_items_carry_the_key_and_run_under_either_name(world):
    db, _lead, _admin, get = world
    newest = datetime(2026, 11, 1, tzinfo=timezone.utc)
    db.rows += [
        defect(PID, approval_status="pending_review", created_at=newest, title="from promote",
               cluster_id="cl_9", ai_confidence_score=71, component="payments", owner_team="Payments",
               policy_evaluation={"policy_reasons": ["Jira ticket creation requires human approval"],
                                  "jira_project_key": "PLAT", "source_run_id": str(RUN)}),
        defect(PID, approval_status="pending_review", created_at=newest - timedelta(seconds=1),
               title="from integrations",
               policy_evaluation={"requested_project_key": "OPS", "requested_run_id": str(RUN)}),
    ]

    items = (await get(f"?project_id={PID}&page_size=2")).json()["items"]

    promoted, integration = items
    assert promoted == {
        "defect_id": promoted["defect_id"], "project_id": str(PID), "title": "from promote",
        "severity": "HIGH", "component": "payments", "owner_team": "Payments",
        "created_at": newest.isoformat(), "policy_reasons": ["Jira ticket creation requires human approval"],
        "jira_project_key": "PLAT", "run_id": str(RUN), "cluster_id": "cl_9", "ai_confidence_score": 71,
    }
    assert (integration["jira_project_key"], integration["run_id"]) == ("OPS", str(RUN))
    assert integration["policy_reasons"] == []


async def test_a_qa_engineer_cannot_read_the_queue(world):
    _db, _lead, _admin, get = world
    engineer = user(UserRole.QA_ENGINEER)

    assert (await get("", principal=engineer)).status_code == 403
