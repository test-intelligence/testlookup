"""DEF-01: linking a Jira key (offline / online) and the status write-back.

Jira is a recorder over EVERY ``httpx.AsyncClient`` in the process
(``tests/fakes/fake_defect_world.HttpRecorder``): an offline path asserts zero
requests to anyone, not just to one client. The PATCH runs through the real
route on an in-memory session; the online Jira lookup is checked to run with
NO pooled connection checked out (pattern of
``tests/regression/test_defect_draft_releases_its_connection.py``).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.models.postgres import Defect, ProjectActivityEvent, UserRole
from app.routers import defect_jira as defect_jira_router
from app.services import defect_jira_service as svc
from tests.fakes.fake_defect_world import (
    ONLINE_CFG,
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

PID = uuid.uuid4()
ISSUE = "/rest/api/3/issue/ABC-123"
TRANSITIONS = "/rest/api/3/issue/ABC-123/transitions"


# ── jira_browse_url: the only builder of a typed key's link ────────────────


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("acme.atlassian.net", "https://acme.atlassian.net/browse/ABC-123"),
        ("https://acme.atlassian.net/", "https://acme.atlassian.net/browse/ABC-123"),
        ("  acme.atlassian.net  ", "https://acme.atlassian.net/browse/ABC-123"),
        ("jira.example.test:8443", "https://jira.example.test:8443/browse/ABC-123"),
        ("", None),
        (None, None),
        ("acme.atlassian.net/jira", None),
        ("http://acme.atlassian.net", None),
        ("javascript:alert(1)", None),
        ("user@acme.atlassian.net", None),
    ],
)
def test_browse_url_is_built_from_the_configured_host_only(domain, expected):
    assert svc.jira_browse_url({"domain": domain}, "ABC-123") == expected


def test_browse_url_refuses_a_non_key():
    assert svc.jira_browse_url({"domain": "acme.atlassian.net"}, "ABC-123/../x") is None


# ── get_issue ──────────────────────────────────────────────────────────────


@pytest.fixture
def http(monkeypatch):
    return HttpRecorder().install(monkeypatch)


@pytest.mark.asyncio
async def test_get_issue_reads_status_and_summary(http):
    http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="In Progress", category="indeterminate")))

    issue = await svc.get_issue(dict(ONLINE_CFG), "ABC-123")

    assert issue == {"key": "ABC-123", "status": "In Progress", "status_category": "indeterminate",
                     "summary": "Checkout total off by a cent"}
    (request,) = http.requests
    assert request.url.host == "acme.atlassian.net"
    assert request.headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "code", "detail"),
    [
        ((404, {"errorMessages": ["Issue does not exist"]}), 422, "Jira has no issue ABC-123."),
        ((500, {}), 502, "Jira answered HTTP 500 for ABC-123."),
        ((401, {}), 502, "Jira answered HTTP 401 for ABC-123."),
        (httpx.ConnectError("refused by tok-secret-value"), 502, "Could not reach Jira: ConnectError"),
        (httpx.ReadTimeout("slow"), 502, "Could not reach Jira: ReadTimeout"),
    ],
)
async def test_get_issue_errors_name_the_class_only(http, answer, code, detail):
    http.route("GET", ISSUE, answer)

    with pytest.raises(HTTPException) as exc:
        await svc.get_issue(dict(ONLINE_CFG), "ABC-123")

    assert exc.value.status_code == code
    assert exc.value.detail == detail
    assert "tok-secret-value" not in str(exc.value.detail)


# ── transition_issue: idempotent by state, never raises ────────────────────


def _transitions(*pairs):
    return {"transitions": [
        {"id": tid, "name": name, "to": {"statusCategory": {"key": category}}} for tid, name, category in pairs
    ]}


@pytest.mark.asyncio
async def test_a_resolve_moves_the_issue_through_the_first_done_transition(http):
    http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="In Progress", category="indeterminate")))
    http.route("GET", TRANSITIONS, (200, _transitions(("21", "Start", "indeterminate"), ("31", "Done", "done"),
                                                      ("41", "Won't do", "done"))))
    http.route("POST", TRANSITIONS, (204, None))

    out = await svc.transition_issue(dict(ONLINE_CFG), "ABC-123", "RESOLVED")

    assert out == {"status": "sent", "reason": None}
    assert [(r.method, r.url.path) for r in http.requests] == [
        ("GET", ISSUE), ("GET", TRANSITIONS), ("POST", TRANSITIONS),
    ]
    assert http.body(http.calls("POST")[0]) == {"transition": {"id": "31"}}


@pytest.mark.asyncio
async def test_an_issue_already_in_the_category_is_skipped_without_a_post(http):
    http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="Done", category="done")))

    out = await svc.transition_issue(dict(ONLINE_CFG), "ABC-123", "INVALID")

    assert out == {"status": "skipped", "reason": "already_in_status"}
    assert http.calls("POST") == []
    assert http.calls("GET", TRANSITIONS) == []


@pytest.mark.asyncio
async def test_no_transition_into_the_category_is_skipped(http):
    http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="Done", category="done")))
    http.route("GET", TRANSITIONS, (200, _transitions(("31", "Close", "done"))))

    out = await svc.transition_issue(dict(ONLINE_CFG), "ABC-123", "IN_PROGRESS")

    assert out == {"status": "skipped", "reason": "no_transition"}
    assert http.calls("POST") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post", "reason"),
    [((500, {}), "HTTP 500"), ((400, {"errors": {"resolution": "required"}}), "HTTP 400"),
     (httpx.ConnectError("x"), "ConnectError")],
)
async def test_a_failed_write_back_is_reported_not_raised(http, post, reason):
    http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123")))
    http.route("GET", TRANSITIONS, (200, _transitions(("31", "Done", "done"))))
    http.route("POST", TRANSITIONS, post)

    out = await svc.transition_issue(dict(ONLINE_CFG), "ABC-123", "RESOLVED")

    assert out == {"status": "failed", "reason": reason}


@pytest.mark.asyncio
async def test_the_category_map_covers_every_settable_status():
    from app.services.defect_status import DEFECT_STATUSES

    assert set(svc.TARGET_CATEGORY) == set(DEFECT_STATUSES)
    assert svc.TARGET_CATEGORY["OPEN"] == "new"
    assert svc.TARGET_CATEGORY["IN_PROGRESS"] == "indeterminate"
    assert svc.TARGET_CATEGORY["RESOLVED"] == svc.TARGET_CATEGORY["INVALID"] == "done"


# ── the PATCH, offline and online ──────────────────────────────────────────


@pytest.fixture
def world(monkeypatch, http):
    engineer = user(UserRole.QA_ENGINEER)
    row = defect(PID)
    pool = OneConnectionPool()
    journal: list[str] = []
    db = MemDB([engineer, row, membership(engineer, PID)], pool=pool, journal=journal)
    checked_out_during_jira: list[int] = []

    original_send = http.send

    async def _send(request):
        checked_out_during_jira.append(pool.held)
        journal.append(f"http {request.method} {request.url.path}")
        return await original_send(request)

    http.send = _send

    async def _bump(project_id):
        journal.append("bump")

    async def _row(session, project_id, defect_id):
        await session.execute(select(Defect).where(Defect.id == row.id))
        return {"id": str(row.id), "project_id": str(row.project_id), "jira_ticket_id": row.jira_ticket_id,
                "jira_ticket_url": row.jira_ticket_url, "resolution_status": row.resolution_status}

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    monkeypatch.setattr("app.services.analytics_service.defect_row", _row, raising=False)

    async def patch(body):
        app = make_app(defect_jira_router.router, lambda: db, engineer)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await asyncio.wait_for(client.patch(f"/api/v1/projects/{PID}/defects/{row.id}", json=body),
                                          timeout=5)

    return SimpleNamespace(db=db, defect=row, pool=pool, journal=journal, http=http,
                           checked_out_during_jira=checked_out_during_jira, patch=patch)


def _ledger(db):
    return [r for r in db.added if isinstance(r, ProjectActivityEvent)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "url"),
    [
        ("acme.atlassian.net", "https://acme.atlassian.net/browse/ABC-123"),
        ("https://acme.atlassian.net/", "https://acme.atlassian.net/browse/ABC-123"),
        ("", None),
        ("acme.atlassian.net/secure", None),
    ],
)
async def test_offline_a_key_is_saved_locally_with_zero_http_calls(world, monkeypatch, domain, url):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    patch_jira_config(monkeypatch, {**ONLINE_CFG, "domain": domain})

    response = await world.patch({"jira_key": " abc-123 "})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jira"] == {"status": "saved_locally", "reason": "offline_mode", "jira_key": "ABC-123",
                            "jira_url": url, "summary": None}
    assert world.defect.jira_ticket_id == "ABC-123"
    assert world.defect.jira_ticket_url == url
    assert world.defect.jira_status is None
    assert world.http.requests == []


@pytest.mark.asyncio
async def test_a_client_sent_url_is_refused_and_nothing_is_called(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    patch_jira_config(monkeypatch)

    response = await world.patch({"jira_key": "ABC-123", "jira_url": "javascript:alert(1)"})

    assert response.status_code == 422
    assert world.defect.jira_ticket_id is None
    assert world.http.requests == []


@pytest.mark.asyncio
async def test_a_malformed_key_is_one_422(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    patch_jira_config(monkeypatch)

    response = await world.patch({"jira_key": "abc-1x"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Not a Jira issue key (expected something like ABC-123)."
    assert world.http.requests == []


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    patch_jira_config(monkeypatch)


@pytest.mark.asyncio
async def test_online_a_key_is_checked_and_mirrored(world, online):
    world.http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="In Progress", category="indeterminate")))

    response = await world.patch({"jira_key": "ABC-123"})

    assert response.status_code == 200, response.text
    jira = response.json()["jira"]
    assert jira["status"] == "validated" and jira["reason"] is None
    assert jira["summary"] == "Checkout total off by a cent"
    assert jira["jira_url"] == "https://acme.atlassian.net/browse/ABC-123"
    assert world.defect.jira_status == "In Progress"
    assert world.defect.external_status_at is not None
    (row,) = _ledger(world.db)
    assert row.context == {"ticket_key": "ABC-123", "validated": True}


@pytest.mark.asyncio
async def test_the_jira_lookup_runs_with_no_pooled_connection_checked_out(world, online):
    world.http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123")))

    response = await world.patch({"jira_key": "ABC-123"})

    assert response.status_code == 200, response.text
    assert world.checked_out_during_jira == [0], "the request held its connection across the Jira call"
    assert world.pool.max_held == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "code", "detail"),
    [((404, {}), 422, "Jira has no issue ABC-123."),
     (httpx.ConnectError("down"), 502, "Could not reach Jira: ConnectError")],
)
async def test_a_refused_lookup_saves_nothing(world, online, answer, code, detail):
    world.http.route("GET", ISSUE, answer)

    response = await world.patch({"jira_key": "ABC-123", "resolution_status": "IN_PROGRESS"})

    assert response.status_code == code
    assert response.json()["detail"] == detail
    assert world.defect.jira_ticket_id is None
    assert world.defect.resolution_status == "OPEN"
    assert _ledger(world.db) == []
    assert "commit" not in world.journal and "bump" not in world.journal


# ── the write-back, after the commit ───────────────────────────────────────


def _linked(world):
    world.defect.jira_ticket_id = "ABC-123"
    world.defect.jira_ticket_url = "https://acme.atlassian.net/browse/ABC-123"


@pytest.mark.asyncio
async def test_a_status_change_on_a_linked_defect_is_written_back_after_the_commit(world, online):
    _linked(world)
    world.http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123")))
    world.http.route("GET", TRANSITIONS, (200, _transitions(("31", "Done", "done"))))
    world.http.route("POST", TRANSITIONS, (204, None))

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 200, response.text
    assert response.json()["write_back"] == {"status": "sent", "reason": None}
    assert world.http.body(world.http.calls("POST")[0]) == {"transition": {"id": "31"}}
    commit_at = world.journal.index("commit")
    first_http = next(i for i, e in enumerate(world.journal) if e.startswith("http "))
    assert commit_at < world.journal.index("bump") < first_http, world.journal
    (row,) = _ledger(world.db)
    assert row.context["jira_write_back"] == "planned"


@pytest.mark.asyncio
async def test_a_failed_write_back_keeps_the_saved_status(world, online):
    _linked(world)
    world.http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123")))
    world.http.route("GET", TRANSITIONS, (200, _transitions(("31", "Done", "done"))))
    world.http.route("POST", TRANSITIONS, (500, {}))

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 200
    assert response.json()["write_back"] == {"status": "failed", "reason": "HTTP 500"}
    assert world.defect.resolution_status == "RESOLVED"
    assert "commit" in world.journal


@pytest.mark.asyncio
async def test_already_done_in_jira_is_skipped(world, online):
    _linked(world)
    world.http.route("GET", ISSUE, (200, jira_issue_payload("ABC-123", name="Done", category="done")))

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.json()["write_back"] == {"status": "skipped", "reason": "already_in_status"}
    assert world.http.calls("POST") == []


@pytest.mark.asyncio
async def test_offline_and_linked_the_write_back_is_not_sent(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    patch_jira_config(monkeypatch)
    _linked(world)

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 200
    assert response.json()["write_back"] == {"status": "not_sent", "reason": "offline_mode"}
    assert world.http.requests == []
    (row,) = _ledger(world.db)
    assert row.context["jira_write_back"] == "not_sent:offline_mode"


@pytest.mark.asyncio
async def test_no_status_change_or_no_link_means_no_write_back(world, online):
    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.json()["write_back"] is None
    assert world.http.requests == []
