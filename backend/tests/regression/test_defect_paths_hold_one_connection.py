"""The W06 defect paths never hold the request's connection while they need another.

Same rule and same harness as
``test_no_second_session_while_the_request_holds_one.py`` (#258): a worker's
pool is three connections (2 + 1), so a request that opens a second session,
or makes an external call, while its own still holds a connection stalls the
worker on the homelab. Every test here runs on a pool of ONE connection with
a 1 s checkout, with sessions that take the connection on their first query
and give it back on ``commit`` / ``rollback`` / ``close`` (what an
``AsyncSession`` does):

* the triage PATCH online: the Jira lookup runs with NO connection checked
  out (``release_read_connection`` before the call), the write-back after the
  commit;
* the promote POST: the ``defect.promoted`` emitter opens its own session
  only after the router's commit returned the request's connection, and
  ``emit_event`` opens the next one only after the emitter left its own;
* the review approve (offline with a key, and with no key): the same, after
  the LAST commit.

A path that opened a session too early waits out the 1 s checkout, and the
emitter (which never raises) would silently send nothing: each test asserts
the emission was REACHED, so that cannot pass quietly.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.models.postgres import TestRun, UserRole, WebhookSubscription
from app.routers import defect_jira as defect_jira_router
from app.services import defect_promoted_webhook as hook
from app.services import webhook_service
from tests.fakes.fake_defect_world import (
    HttpRecorder,
    MemDB,
    OneConnectionPool,
    defect,
    jira_issue_payload,
    make_app,
    membership,
    patch_jira_config,
    user,
)

pytestmark = [pytest.mark.regression, pytest.mark.asyncio]

PID = uuid.uuid4()
RUN = uuid.uuid4()


@pytest.fixture
def pool():
    return OneConnectionPool(timeout=1.0)


@pytest.fixture
def webhooks(monkeypatch, pool):
    """Webhooks on; the emitter's and ``emit_event``'s sessions come from the
    same one-connection pool; ``emit_event`` is the REAL one, spied."""
    reached: list = []
    rows: list = [WebhookSubscription(id=uuid.uuid4(), project_id=PID, name="hook",
                                      target_url="https://hooks.example.test/x", events=["defect.promoted"],
                                      enabled=True)]
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_service, "AsyncSessionLocal", lambda: MemDB(rows, pool=pool))
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: MemDB(rows, pool=pool))
    real_emit = webhook_service.emit_event

    async def _spy(event_type, **kwargs):
        reached.append((event_type, kwargs["delivery_scope"], pool.held))
        return await real_emit(event_type, **kwargs)

    monkeypatch.setattr(webhook_service, "emit_event", _spy)
    return SimpleNamespace(reached=reached, rows=rows)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_the_triage_patch_calls_jira_with_no_connection_held(monkeypatch, pool):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    patch_jira_config(monkeypatch)
    http = HttpRecorder().install(monkeypatch)
    held_during_http: list[int] = []
    original = http.send

    async def _send(request):
        held_during_http.append(pool.held)
        return await original(request)

    http.send = _send
    http.route("GET", "/rest/api/3/issue/ABC-123", (200, jira_issue_payload("ABC-123")))
    http.route("GET", "/rest/api/3/issue/ABC-123/transitions",
               (200, {"transitions": [{"id": "21", "to": {"statusCategory": {"key": "indeterminate"}}}]}))
    http.route("POST", "/rest/api/3/issue/ABC-123/transitions", (204, None))
    engineer = user(UserRole.QA_ENGINEER)
    row = defect(PID)
    rows = [engineer, row, membership(engineer, PID)]
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: MemDB(rows, pool=pool))
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())

    async def _row(session, project_id, defect_id):
        await session.execute(select(TestRun.id))
        return {"id": str(defect_id)}

    monkeypatch.setattr("app.services.analytics_service.defect_row", _row, raising=False)
    app = make_app(defect_jira_router.router, lambda: MemDB(rows, pool=pool), engineer)

    async with _client(app) as client:
        response = await asyncio.wait_for(client.patch(
            f"/api/v1/projects/{PID}/defects/{row.id}",
            json={"jira_key": "ABC-123", "resolution_status": "IN_PROGRESS"},
        ), timeout=10)

    assert response.status_code == 200, response.text
    assert response.json()["write_back"] == {"status": "sent", "reason": None}
    assert held_during_http and set(held_during_http) == {0}, held_during_http
    assert pool.max_held == 1
    assert pool.timeouts == 0, "a session was opened while another held the connection"


async def test_the_promote_route_emits_on_its_own_connection_after_the_commit(monkeypatch, pool, webhooks,
                                                                              fake_redis):
    from app.routers import deep_investigation

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    engineer = user(UserRole.QA_ENGINEER)
    promoted = defect(PID, cluster_id="cl_1", approval_status="approved",
                      policy_evaluation={"source_run_id": str(RUN)})
    request_rows = [engineer, membership(engineer, PID),
                    TestRun(id=RUN, project_id=PID, build_number="b", status="FAILED")]
    webhooks.rows.append(promoted)

    async def _promote_cluster(**kwargs):
        await kwargs["db"].execute(select(TestRun.id))  # it reads and stages
        return {"defect_id": str(promoted.id), "cluster_id": "cl_1", "severity": "HIGH", "title": "t"}

    monkeypatch.setattr(deep_investigation, "promote_cluster", _promote_cluster)
    monkeypatch.setattr("app.services.intelligence_snapshot_service.mark_stale", AsyncMock())
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr(hook, "AsyncSessionLocal", lambda: MemDB(webhooks.rows, pool=pool))
    app = make_app(deep_investigation.router, lambda: MemDB(request_rows, pool=pool), engineer)

    async with _client(app) as client:
        response = await asyncio.wait_for(
            client.post(f"/api/v1/deep-investigate/{RUN}/clusters/cl_1/promote", json={"title": "t"}), timeout=10)

    assert response.status_code == 200, response.text
    assert webhooks.reached == [("defect.promoted", f"defect-promoted:{promoted.id}:v1", 0)], (
        "the emitter never got a connection (the request still held it), or held its own into emit_event"
    )
    assert pool.max_held == 1
    assert pool.timeouts == 0, "a session was opened while another held the connection"


@pytest.mark.parametrize("policy", [{"jira_project_key": "PLAT"}, {}], ids=["offline-with-key", "no-key"])
async def test_review_approve_emits_after_its_last_commit(monkeypatch, pool, webhooks, fake_redis, policy):
    from app.routers import deep_investigation

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    patch_jira_config(monkeypatch)
    lead = user(UserRole.QA_LEAD)
    pending = defect(PID, cluster_id="cl_1", approval_status="pending_review",
                     policy_evaluation={"source_run_id": str(RUN), **policy})
    request_rows = [lead, membership(lead, PID, role="QA_LEAD"), pending]
    # The emitter reads the row as committed: the same object, seen from its own session.
    webhooks.rows.append(pending)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr(hook, "AsyncSessionLocal", lambda: MemDB(webhooks.rows, pool=pool))
    app = make_app(deep_investigation.router, lambda: MemDB(request_rows, pool=pool), lead)

    async with _client(app) as client:
        response = await asyncio.wait_for(
            client.post(f"/api/v1/deep-investigate/defects/{pending.id}/review", json={"action": "approve"}),
            timeout=10)

    assert response.status_code == 200, response.text
    assert response.json()["jira_status"] == ("not_filed" if policy else "not_requested")
    assert webhooks.reached == [("defect.promoted", f"defect-promoted:{pending.id}:v1", 0)]
    assert pool.max_held == 1
    assert pool.timeouts == 0, "a session was opened while another held the connection"


async def test_the_harness_refuses_a_second_session(pool):
    """The harness itself: a second session while the first holds the
    connection times out, so the tests above are not vacuously green."""
    first = MemDB([], pool=pool)
    await first.execute(select(TestRun.id))
    with pytest.raises(asyncio.TimeoutError):
        await MemDB([], pool=pool).execute(select(TestRun.id))
    await first.close()
