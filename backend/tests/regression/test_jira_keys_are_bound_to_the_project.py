"""A TestLookup project reaches only its own Jira project through the shared Jira account.

Jira is configured once for the deployment, with one service account. Every
route that takes a Jira project key or a Jira issue key from the caller checks
it against the TestLookup project first (``defect_jira_service.jira_key_scope``):

* the project has ``jira_project_key`` set: only that Jira project, and only
  issues whose key starts with it, for every role;
* the project has none: a caller below QA_LEAD (in that project) may use only
  the instance default Jira project; a QA_LEAD or an ADMIN may name another;
* a refused key answers 422 with a sentence, and no request is sent to Jira;
* a key stored on a defect before this rule is not written back to, read or
  commented on when it is out of scope: the local change still saves;
* the metadata route needs QA_ENGINEER and lists only the allowed Jira projects;
* changing ``Project.jira_project_key`` needs an instance ADMIN signed in as a
  person or with an unbound key: it decides the project's Jira reach.

Jira is the recorder over every ``httpx.AsyncClient`` in the process
(``tests/fakes/fake_defect_world.HttpRecorder``), so "nothing was sent" is an
assertion over everything.
"""
from __future__ import annotations

import inspect
import typing
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import _bind_api_key_project
from app.models.postgres import Defect, Project, UserRole
from app.models.postgres import TestRun as RunRow
from app.models.postgres import TestCase as CaseRow
from app.routers import agents as agents_router
from app.routers import analytics as analytics_router
from app.routers import defect_jira as defect_jira_router
from app.routers import integrations as integrations_router
from app.routers import projects as projects_router
from app.routers import releases as releases_router
from app.services import cluster_epic_service as ces
from app.services import defect_jira_service as svc
from app.services import defect_promotion_service as promotion
from app.services import tool_call_idempotency as tci
from tests.fakes.fake_defect_world import (
    ONLINE_CFG,
    HttpRecorder,
    MemDB,
    defect,
    jira_issue_payload,
    make_app,
    membership,
    patch_jira_config,
    user,
)

pytestmark = pytest.mark.regression

PID = uuid.uuid4()
MEMBERSHIP_KEY = "0123456789abcdef0123456789abcdef"  # gitleaks:allow (a made-up cluster membership key, not a credential)
ISSUE_POST = "/rest/api/3/issue"


def _project(key: str | None = "TEAM", **fields) -> Project:
    return Project(id=PID, name="Payments", slug="payments", jira_project_key=key, **fields)


def _world(role: UserRole = UserRole.QA_ENGINEER, *, key: str | None = "TEAM",
           member_role: str | None = None, rows=(), **project_fields):
    """A session holding the project, one member of ``role`` and ``rows``."""
    person = user(role)
    db = MemDB([_project(key, **project_fields), person,
                membership(person, PID, member_role or role.value), *rows])
    return SimpleNamespace(db=db, user=person)


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    patch_jira_config(monkeypatch)
    monkeypatch.setattr(integrations_router, "resolve_jira_config", svc.resolve_jira_config)
    svc._metadata_cache_clear()
    recorder = HttpRecorder().install(monkeypatch)
    recorder.route("POST", ISSUE_POST, (201, {"key": "TEAM-7"}))
    yield recorder
    svc._metadata_cache_clear()


@pytest.fixture
def quiet(monkeypatch):
    """The parts of a filing that are not this rule's: cache bumps, the lock, the ledger claim."""
    async def _nothing(*_a, **_kw):
        return None

    async def _zero(*_a, **_kw):
        return 0

    async def _run_once(*, call, **_kw):
        return tci.ToolCallOutcome(status="executed", key="k", result=dict(await call()))

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _nothing)
    monkeypatch.setattr(svc, "_lock_signature", _nothing)
    monkeypatch.setattr(svc, "find_open_linked_defect", _nothing)
    monkeypatch.setattr(svc, "_signature_generation", _zero)
    monkeypatch.setattr(svc.tci, "run_once", _run_once)


def _prefill() -> dict:
    return {
        "signature": "f" * 16, "summary": "[TestLookup] checkout_flow_test",
        "description": "Automated defect filed from TestLookup", "test_name": "checkout_flow_test",
        "suite_name": "checkout", "cluster_id": "c-1", "error_message": "AssertionError",
        "occurrences": {"first_seen": None, "last_seen": None, "failing_runs": 2},
        "context": {"branch": "main", "build_number": "12", "ci_run_url": None},
        "ai_analysis": None, "deep_link": "http://localhost:3000/runs/abc",
        "latest_run_id": "abc", "existing_defect": None,
    }


def _client(router, world) -> AsyncClient:
    app = make_app(router, lambda: world.db, world.user)
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


def _filed_under(http) -> list[str]:
    return [http.body(r)["fields"]["project"]["key"] for r in http.calls("POST", ISSUE_POST)]


def _refusal_text(response) -> str:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    return detail


# ── the rule itself ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_key", "role", "key", "allowed"),
    [
        ("TEAM", UserRole.QA_ENGINEER, "TEAM", True),
        ("TEAM", UserRole.QA_ENGINEER, "team", True),
        ("TEAM", UserRole.QA_ENGINEER, "TEAM-12", True),
        ("TEAM", UserRole.QA_ENGINEER, "OTHER", False),
        ("TEAM", UserRole.QA_ENGINEER, "OTHER-12", False),
        ("TEAM", UserRole.QA_ENGINEER, "TEAMS-12", False),
        ("TEAM", UserRole.QA_ENGINEER, "QA", False),          # the instance default is not this project's
        ("TEAM", UserRole.QA_LEAD, "OTHER", False),           # a set key binds a lead too
        ("TEAM", UserRole.ADMIN, "OTHER-12", False),          # and an administrator
        (None, UserRole.QA_ENGINEER, "QA", True),
        (None, UserRole.QA_ENGINEER, "QA-3", True),
        (None, UserRole.QA_ENGINEER, "OTHER", False),
        (None, UserRole.QA_ENGINEER, "OTHER-3", False),
        (None, UserRole.QA_LEAD, "OTHER", True),
        (None, UserRole.QA_LEAD, "OTHER-3", True),
        (None, UserRole.ADMIN, "OTHER", True),
        ("", UserRole.QA_ENGINEER, "OTHER", False),           # an empty key is no key
    ],
)
async def test_a_key_is_allowed_only_inside_the_projects_jira_project(project_key, role, key, allowed):
    world = _world(role, key=project_key)

    if allowed:
        await svc.assert_jira_key_allowed(world.db, PID, key, world.user, cfg=dict(ONLINE_CFG))
        return
    with pytest.raises(HTTPException) as refused:
        await svc.assert_jira_key_allowed(world.db, PID, key, world.user, cfg=dict(ONLINE_CFG))
    assert refused.value.status_code == 422
    assert key.upper() in refused.value.detail            # what was refused
    assert "administrator" in refused.value.detail        # what to do


@pytest.mark.asyncio
async def test_an_instance_lead_who_is_only_an_engineer_in_the_project_is_held_to_the_default():
    world = _world(UserRole.QA_LEAD, key=None, member_role="QA_ENGINEER")

    with pytest.raises(HTTPException) as refused:
        await svc.assert_jira_key_allowed(world.db, PID, "OTHER", world.user, cfg=dict(ONLINE_CFG))
    assert refused.value.status_code == 422


@pytest.mark.asyncio
async def test_a_path_with_no_person_uses_the_projects_key_or_the_default():
    cfg = dict(ONLINE_CFG)
    keyed, bare = _world(key="TEAM"), _world(key=None)

    assert await svc.resolve_jira_project_key(keyed.db, PID, None, None, cfg=cfg) == "TEAM"
    assert await svc.resolve_jira_project_key(bare.db, PID, None, None, cfg=cfg) == "QA"
    for world in (keyed, bare):
        with pytest.raises(HTTPException):
            await svc.resolve_jira_project_key(world.db, PID, None, "OTHER", cfg=cfg)


@pytest.mark.asyncio
async def test_with_no_project_key_and_no_default_a_lead_keeps_today_and_an_engineer_is_told_what_to_do():
    cfg = {**ONLINE_CFG, "default_project_key": None}
    lead, engineer = _world(UserRole.QA_LEAD, key=None), _world(UserRole.QA_ENGINEER, key=None)

    assert await svc.resolve_jira_project_key(lead.db, PID, lead.user, None, cfg=cfg) is None
    assert await svc.resolve_jira_project_key(lead.db, PID, lead.user, "other", cfg=cfg) == "OTHER"
    for requested in (None, "OTHER"):
        with pytest.raises(HTTPException) as refused:
            await svc.resolve_jira_project_key(engineer.db, PID, engineer.user, requested, cfg=cfg)
        assert refused.value.status_code == 422
        assert "administrator" in refused.value.detail


# ── every route that takes a key from the caller ───────────────────────────


async def _one_click(world, key, monkeypatch):
    monkeypatch.setattr(svc, "build_prefill", AsyncMock(return_value=_prefill()))
    async with _client(defect_jira_router.router, world) as client:
        return await client.post(f"/api/v1/projects/{PID}/defects/jira",
                                 json={"cluster_id": "c-1", "jira_project_key": key})


async def _epic(world, key, monkeypatch):
    prefill = {"signature": "e" * 16, "summary": "[TestLookup] Recurring failure cluster: c", "description": "d",
               "adf": {"type": "doc", "version": 1, "content": []}, "cluster_label": "c",
               "member_fingerprints": ["a", "b"], "member_count": 2, "co_failure_runs": 5}
    monkeypatch.setattr(ces, "build_epic_prefill", AsyncMock(return_value=prefill))
    world.reads_from_jira = AsyncMock(return_value="Epic")
    monkeypatch.setattr(ces, "_require_epic_type", world.reads_from_jira)
    monkeypatch.setattr(ces, "_stored_link", AsyncMock(return_value=None))
    monkeypatch.setattr(ces, "record_activity", AsyncMock())
    async with _client(defect_jira_router.router, world) as client:
        return await client.post(f"/api/v1/projects/{PID}/systemic-clusters/{MEMBERSHIP_KEY}/epic",
                                 json={"jira_project_key": key})


async def _legacy_ticket(world, key, monkeypatch):
    from app.services.action_policy import ActionStatus

    run = RunRow(id=uuid.uuid4(), project_id=PID)
    case = CaseRow(id=uuid.uuid4(), test_run_id=run.id, test_name="test_checkout", status="FAILED",
                   error_message="boom")
    world.db.rows += [run, case]
    monkeypatch.setattr(integrations_router, "jira_unavailable_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(integrations_router, "check_jira_ticket_creation_policy", AsyncMock(return_value={
        "initial_status": ActionStatus.APPROVED, "policy_reasons": [], "requires_approval": False}))

    async def _create(*, project_key, cfg, **_kw):
        response = await svc._jira_post(cfg, ISSUE_POST, {"fields": {"project": {"key": project_key}}})
        return {"ticket_key": response.json()["key"], "ticket_url": "https://acme.atlassian.net/browse/TEAM-7"}

    monkeypatch.setattr(integrations_router, "create_jira_issue", _create)
    async with _client(integrations_router.router, world) as client:
        return await client.post("/api/v1/integrations/jira", json={
            "project_key": key, "test_case_id": str(case.id), "test_name": "test_checkout",
            "run_id": str(run.id), "ai_summary": "Checkout fails", "recommended_action": "Fix it"})


ROUTES = {"one-click defect": _one_click, "cluster epic": _epic, "legacy ticket": _legacy_ticket}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_a_route_refuses_a_foreign_jira_project_and_sends_nothing(route, http, quiet, monkeypatch):
    world = _world(key="TEAM", cluster_epic_suggestions=True)

    response = await ROUTES[route](world, "OTHER", monkeypatch)

    detail = _refusal_text(response)
    assert "OTHER" in detail and "TEAM" in detail
    assert http.requests == []
    assert [row for row in world.db.added if isinstance(row, Defect)] == []
    if route == "cluster epic":
        # Refused before the Epic issue type is read from Jira.
        world.reads_from_jira.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("sent_key", ["TEAM", "team"])
async def test_a_route_files_under_the_projects_own_jira_project(route, sent_key, http, quiet, monkeypatch):
    world = _world(key="TEAM", cluster_epic_suggestions=True)

    response = await ROUTES[route](world, sent_key, monkeypatch)

    assert response.status_code in (200, 201), response.text
    assert _filed_under(http) == ["TEAM"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["one-click defect", "cluster epic"])
async def test_no_key_in_the_request_means_the_projects_key_not_the_instance_default(
    route, http, quiet, monkeypatch,
):
    world = _world(key="TEAM", cluster_epic_suggestions=True)

    response = await ROUTES[route](world, None, monkeypatch)

    assert response.status_code in (200, 201), response.text
    assert _filed_under(http) == ["TEAM"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_without_a_project_key_an_engineer_files_only_under_the_default(route, http, quiet, monkeypatch):
    refused = await ROUTES[route](_world(key=None, cluster_epic_suggestions=True), "OTHER", monkeypatch)
    assert "OTHER" in _refusal_text(refused) and http.requests == []

    allowed = await ROUTES[route](_world(key=None, cluster_epic_suggestions=True), "QA", monkeypatch)
    assert allowed.status_code in (200, 201), allowed.text
    assert _filed_under(http) == ["QA"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_without_a_project_key_a_lead_may_name_another_jira_project(route, http, quiet, monkeypatch):
    world = _world(UserRole.QA_LEAD, key=None, cluster_epic_suggestions=True)

    response = await ROUTES[route](world, "OTHER", monkeypatch)

    assert response.status_code in (200, 201), response.text
    assert _filed_under(http) == ["OTHER"]


# ── the defect PATCH: linking a key, and the write-back on a stored one ────


def _patch_world(monkeypatch, role=UserRole.QA_ENGINEER, *, key="TEAM", **defect_fields):
    row = defect(PID, **defect_fields)
    world = _world(role, key=key, rows=[row])

    async def _row(session, project_id, defect_id):
        return {"id": str(row.id), "jira_ticket_id": row.jira_ticket_id}

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr("app.services.analytics_service.defect_row", _row, raising=False)
    world.defect = row

    async def patch(body):
        async with _client(defect_jira_router.router, world) as client:
            return await client.patch(f"/api/v1/projects/{PID}/defects/{row.id}", json=body)

    world.patch = patch
    return world


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True])
async def test_linking_an_issue_of_another_jira_project_is_refused_before_jira_is_asked(http, monkeypatch, offline):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", offline)
    world = _patch_world(monkeypatch)

    response = await world.patch({"jira_key": "OTHER-1"})

    detail = _refusal_text(response)
    assert "OTHER-1" in detail and "TEAM" in detail
    assert http.requests == []
    assert world.defect.jira_ticket_id is None


@pytest.mark.asyncio
async def test_linking_an_issue_of_the_projects_jira_project_is_checked_against_jira(http, monkeypatch):
    http.route("GET", "/rest/api/3/issue/TEAM-1", (200, jira_issue_payload("TEAM-1")))
    world = _patch_world(monkeypatch)

    response = await world.patch({"jira_key": "team-1"})

    assert response.status_code == 200, response.text
    assert response.json()["jira"]["status"] == "validated"
    assert world.defect.jira_ticket_id == "TEAM-1"
    assert [(r.method, r.url.path) for r in http.requests] == [("GET", "/rest/api/3/issue/TEAM-1")]


@pytest.mark.asyncio
async def test_a_stored_out_of_scope_key_is_not_written_back_to_but_the_status_still_saves(http, monkeypatch):
    world = _patch_world(monkeypatch, jira_ticket_id="OTHER-9")

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 200, response.text
    assert response.json()["write_back"] == {"status": "not_sent", "reason": "jira_key_not_allowed"}
    assert world.defect.resolution_status == "RESOLVED"
    assert http.requests == []


@pytest.mark.asyncio
async def test_a_stored_key_of_the_projects_jira_project_is_still_written_back_to(http, monkeypatch):
    issue = "/rest/api/3/issue/TEAM-9"
    http.route("GET", issue, (200, jira_issue_payload("TEAM-9", name="In Progress", category="indeterminate")))
    http.route("GET", f"{issue}/transitions",
               (200, {"transitions": [{"id": "31", "name": "Done", "to": {"statusCategory": {"key": "done"}}}]}))
    http.route("POST", f"{issue}/transitions", (204, None))
    world = _patch_world(monkeypatch, jira_ticket_id="TEAM-9")

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 200, response.text
    assert response.json()["write_back"] == {"status": "sent", "reason": None}
    assert len(http.calls("POST", f"{issue}/transitions")) == 1


@pytest.mark.asyncio
async def test_a_lead_on_a_project_without_a_key_still_writes_back_to_another_jira_project(http, monkeypatch):
    issue = "/rest/api/3/issue/OTHER-9"
    http.route("GET", issue, (200, jira_issue_payload("OTHER-9", name="Done", category="done")))
    world = _patch_world(monkeypatch, UserRole.QA_LEAD, key=None, jira_ticket_id="OTHER-9")

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.json()["write_back"] == {"status": "skipped", "reason": "already_in_status"}
    assert len(http.calls("GET", issue)) == 1


@pytest.mark.asyncio
async def test_a_recurrence_comment_is_not_posted_on_a_stored_out_of_scope_issue(http, quiet, monkeypatch):
    stored = defect(PID, jira_ticket_id="OTHER-4", jira_ticket_url="https://acme.atlassian.net/browse/OTHER-4")
    monkeypatch.setattr(svc, "find_open_linked_defect", AsyncMock(return_value=stored))
    world = _world(key="TEAM")

    response = await _one_click(world, None, monkeypatch)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deduplicated"] is True and body["recurrence_comment_posted"] is False
    assert stored.recurrence_count == 1
    assert http.requests == []


# ── the routes that hand the key to an agent or keep it for an approval ────


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "status", "passed"), [("OTHER", 422, None), ("team", 200, "TEAM"), (None, 200, None)])
async def test_the_defect_command_route_checks_the_key_before_the_agent_runs(http, monkeypatch, key, status, passed):
    run = RunRow(id=uuid.uuid4(), project_id=PID, build_number="12")
    world = _world(key="TEAM", rows=[run])
    commander = AsyncMock(return_value={"defect_id": None})
    monkeypatch.setattr("app.agents.defect_commander.run_defect_commander", commander)
    monkeypatch.setattr(agents_router, "record_activity", AsyncMock())
    params = {"cluster_id": "c-1", "run_id": str(run.id), "project_id": str(PID)}
    if key is not None:
        params["project_key"] = key

    async with _client(agents_router.router, world) as client:
        response = await client.post("/api/v1/agents/defect-command", params=params)

    assert response.status_code == status, response.text
    if status == 422:
        commander.assert_not_awaited()
        assert "OTHER" in response.json()["detail"]
    else:
        assert commander.await_args.kwargs["project_key"] == passed
    assert http.requests == []


@pytest.mark.asyncio
async def test_the_commander_itself_drops_a_key_the_project_may_not_use(monkeypatch):
    from app.agents import defect_commander

    keyed = _world(key="TEAM")

    assert await defect_commander.project_key_for_filing(keyed.db, str(PID), "OTHER", None) == (
        None, "jira_key_not_allowed")
    assert await defect_commander.project_key_for_filing(keyed.db, str(PID), "team", None) == ("TEAM", None)
    assert await defect_commander.project_key_for_filing(keyed.db, str(PID), None, None) == (None, None)


@pytest.mark.asyncio
async def test_promoting_a_cluster_checks_the_key_before_anything_is_read_or_staged(http):
    world = _world(key="TEAM")
    run_id = str(uuid.uuid4())

    with pytest.raises(HTTPException) as refused:
        await promotion.promote_cluster(run_id=run_id, cluster_id="c-1", project_id=str(PID),
                                        request={"title": "t", "project_key": "OTHER"}, db=world.db,
                                        actor=world.user)
    assert refused.value.status_code == 422 and "OTHER" in refused.value.detail
    assert world.db.added == [] and http.requests == []
    assert all("failure_clusters" not in str(stmt) for stmt in world.db.statements)

    # The project's own key passes the check: the next thing to fail is the missing cluster.
    with pytest.raises(ValueError, match="Cluster c-1 not found"):
        await promotion.promote_cluster(run_id=run_id, cluster_id="c-1", project_id=str(PID),
                                        request={"title": "t", "project_key": "TEAM"}, db=world.db,
                                        actor=world.user)


@pytest.mark.asyncio
async def test_approving_a_defect_does_not_file_under_a_stored_out_of_scope_key(http, quiet, monkeypatch):
    lead = _world(UserRole.QA_LEAD, key="TEAM")
    pending = defect(PID, cluster_id="c-1", signature_fingerprint="f" * 16)
    monkeypatch.setattr(svc, "_prefill_for_defect", AsyncMock(return_value=_prefill()))

    result = await svc.file_issue_for_defect(lead.db, pending, lead.user, jira_project_key="OTHER")

    assert result["status"] == "failed" and "OTHER" in result["error"]
    assert pending.jira_ticket_id is None and http.requests == []

    filed = await svc.file_issue_for_defect(lead.db, pending, lead.user, jira_project_key="TEAM")
    assert filed["status"] == "filed" and _filed_under(http) == ["TEAM"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "expected"), [("OTHER", 422), ("TEAM", "TEAM"), (None, "TEAM")])
async def test_the_release_sync_reads_fix_versions_of_the_projects_jira_project_only(monkeypatch, key, expected):
    world = _world(UserRole.QA_LEAD, key="TEAM")
    patch_jira_config(monkeypatch)
    synced = AsyncMock(return_value={"created": 0, "updated": 0, "skipped": 0})
    monkeypatch.setattr(releases_router.jira_release_sync, "sync_fix_versions", synced)
    monkeypatch.setattr(releases_router, "record_activity", AsyncMock(), raising=False)
    body = releases_router.ReleaseSyncIn(project_id=str(PID), source="jira", jira_project_key=key)

    if expected == 422:
        with pytest.raises(HTTPException) as refused:
            await releases_router.sync_releases_from_external(body=body, db=world.db, current_user=world.user)
        assert refused.value.status_code == 422 and "OTHER" in refused.value.detail
        synced.assert_not_awaited()
    else:
        await releases_router.sync_releases_from_external(body=body, db=world.db, current_user=world.user)
        assert synced.await_args.args[2] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "refused"), [
    ("https://acme.atlassian.net/browse/OTHER-5", True),
    ("https://acme.atlassian.net/browse/TEAM-5", False),
    ("https://example.test/not-a-jira-link", False),
])
async def test_a_manual_defect_stores_a_jira_link_only_for_the_projects_jira_project(monkeypatch, url, refused):
    from app.models.schemas import DefectIntakeRequest

    world = _world(key="TEAM")
    patch_jira_config(monkeypatch)
    created = defect(PID, severity="HIGH")
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(analytics_router.analytics_service, "create_manual_defect", create)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    payload = DefectIntakeRequest(project_id=PID, title="Checkout total", severity="P1", jira_ticket_url=url)

    if refused:
        with pytest.raises(HTTPException) as exc:
            await analytics_router.create_defect(payload, db=world.db, current_user=world.user)
        assert exc.value.status_code == 422 and "OTHER-5" in exc.value.detail
        create.assert_not_awaited()
    else:
        try:
            await analytics_router.create_defect(payload, db=world.db, current_user=world.user)
        except Exception:  # noqa: BLE001 - the response model of the fake row is not this test's subject
            pass
        create.assert_awaited_once()


# ── the background sync ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_status_sync_does_not_read_an_issue_outside_the_projects_jira_project(http, monkeypatch):
    http.route("GET", "/rest/api/3/issue/TEAM-1", (200, jira_issue_payload("TEAM-1", name="In Progress")))
    inside = defect(PID, jira_ticket_id="TEAM-1")
    outside = defect(PID, jira_ticket_id="OTHER-1", jira_status="To Do")
    db = MemDB([_project("TEAM"), inside, outside])

    summary = await svc.sync_external_statuses(db)

    assert [r.url.path for r in http.requests] == ["/rest/api/3/issue/TEAM-1"]
    assert inside.jira_status == "In Progress"
    assert outside.jira_status == "To Do" and outside.external_status_at is not None
    assert summary["checked"] == 1 and summary["out_of_scope"] == 1


# ── the metadata route ─────────────────────────────────────────────────────


def _metadata_routes(http):
    http.route("GET", "/rest/api/3/project/search", (200, {"values": [
        {"key": "TEAM", "name": "Team"}, {"key": "OTHER", "name": "Other"}, {"key": "QA", "name": "Quality"}]}))
    http.route("GET", "/rest/api/3/issuetype", (200, [{"name": "Bug"}, {"name": "Epic"}]))


async def _metadata(world, monkeypatch):
    monkeypatch.setattr(svc, "_webhook_target_available", AsyncMock(return_value=False))
    async with _client(defect_jira_router.router, world) as client:
        return await client.get(f"/api/v1/projects/{PID}/defects/jira/metadata")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.TESTER])
async def test_the_metadata_route_needs_qa_engineer(http, monkeypatch, role):
    _metadata_routes(http)

    response = await _metadata(_world(role, key="TEAM"), monkeypatch)

    assert response.status_code == 403
    assert http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "project_key", "listed", "default"), [
    (UserRole.QA_ENGINEER, "TEAM", ["TEAM"], "TEAM"),
    (UserRole.ADMIN, "TEAM", ["TEAM"], "TEAM"),
    (UserRole.QA_ENGINEER, None, ["QA"], "QA"),
    (UserRole.QA_LEAD, None, ["TEAM", "OTHER", "QA"], "QA"),
    (UserRole.ADMIN, None, ["TEAM", "OTHER", "QA"], "QA"),
])
async def test_the_metadata_route_lists_only_the_allowed_jira_projects(
    http, monkeypatch, role, project_key, listed, default,
):
    _metadata_routes(http)

    # Twice: the second answer comes from the cache, which holds the whole list.
    for _ in range(2):
        response = await _metadata(_world(role, key=project_key), monkeypatch)
        assert response.status_code == 200, response.text
        body = response.json()
        assert [p["key"] for p in body["projects"]] == listed
        assert body["default_project_key"] == default
        assert body["issue_types"] == ["Bug", "Epic"]


# ── who may set a project's Jira key ───────────────────────────────────────


async def _update(world, **fields):
    from app.models.schemas import ProjectUpdate

    return await projects_router.update_project(PID, ProjectUpdate(**fields), db=world.db, current_user=world.user)


@pytest.fixture
def no_ledger(monkeypatch):
    monkeypatch.setattr(projects_router, "record_activity", AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("new_key", ["OTHER", ""])
async def test_a_lead_cannot_change_the_projects_jira_key(no_ledger, new_key):
    world = _world(UserRole.QA_LEAD, key="TEAM")

    with pytest.raises(HTTPException) as refused:
        await _update(world, jira_project_key=new_key, description="new text")

    assert refused.value.status_code == 403
    assert "Jira" in refused.value.detail and "administrator" in refused.value.detail
    project = world.db.of(Project)[0]
    assert project.jira_project_key == "TEAM" and project.description is None
    assert "commit" not in world.db.journal


@pytest.mark.asyncio
async def test_a_lead_may_send_the_unchanged_key_with_other_fields(no_ledger):
    world = _world(UserRole.QA_LEAD, key="TEAM")

    await _update(world, jira_project_key="TEAM", description="new text")

    project = world.db.of(Project)[0]
    assert project.jira_project_key == "TEAM" and project.description == "new text"


@pytest.mark.asyncio
async def test_an_administrator_changes_the_projects_jira_key(no_ledger):
    world = _world(UserRole.ADMIN, key="TEAM")

    await _update(world, jira_project_key="OTHER")

    assert world.db.of(Project)[0].jira_project_key == "OTHER"


@pytest.mark.asyncio
async def test_a_project_bound_api_key_cannot_change_the_jira_key_even_for_an_administrator(no_ledger):
    world = _world(UserRole.ADMIN, key="TEAM")
    _bind_api_key_project(world.user, PID)

    with pytest.raises(HTTPException) as refused:
        await _update(world, jira_project_key="OTHER")

    assert refused.value.status_code == 403
    assert world.db.of(Project)[0].jira_project_key == "TEAM"


@pytest.mark.asyncio
async def test_a_lead_cannot_create_a_project_with_a_jira_key(monkeypatch):
    from app.models.schemas import ProjectCreate

    provision = AsyncMock(return_value=[])
    monkeypatch.setattr(projects_router, "provision_new_project", provision)
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    lead, admin = user(UserRole.QA_LEAD), user(UserRole.ADMIN)

    with pytest.raises(HTTPException) as refused:
        await projects_router.create_project(
            ProjectCreate(name="Orders", slug="orders", jira_project_key="OTHER"), db=MemDB(), current_user=lead)
    assert refused.value.status_code == 403 and "administrator" in refused.value.detail
    provision.assert_not_awaited()

    await projects_router.create_project(ProjectCreate(name="Orders", slug="orders"), db=MemDB(), current_user=lead)
    db = MemDB()
    await projects_router.create_project(
        ProjectCreate(name="Billing", slug="billing", jira_project_key="BILL"), db=db, current_user=admin)
    assert [p.jira_project_key for p in db.added if isinstance(p, Project)] == ["BILL"]


# ── structure: a new route that takes a Jira key must run the check ────────

#: Request fields that carry a Jira project key, a Jira issue key or a Jira link.
JIRA_KEY_FIELDS = frozenset({"project_key", "jira_project_key", "jira_key", "jira_ticket_url"})

#: What the handler (or the function it hands the key to) must call.
CHECKS = ("resolve_jira_project_key", "assert_jira_key_allowed", "jira_key_scope", "refuse_jira_key_change")


def _delegated() -> dict[tuple[str, str], list]:
    """Routes whose check lives in the service they call: each function names the next, the last one checks."""
    return {
        ("POST", "/api/v1/projects/{project_id}/defects/jira"): [svc.create_or_link_issue, svc._file_or_reuse],
        ("POST", "/api/v1/projects/{project_id}/systemic-clusters/{membership_key}/epic"): [
            ces.create_epic_for_cluster],
        ("POST", "/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/promote"): [promotion.promote_cluster],
    }


def _jira_fields_of(endpoint) -> set[str]:
    found: set[str] = set()
    try:
        hints = typing.get_type_hints(endpoint)
    except Exception:  # noqa: BLE001 - an unresolvable annotation: fall back to the raw ones
        hints = dict(getattr(endpoint, "__annotations__", {}))
    for name in inspect.signature(endpoint).parameters:
        if name in JIRA_KEY_FIELDS:
            found.add(name)
        annotation = hints.get(name)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found |= JIRA_KEY_FIELDS & set(annotation.model_fields)
    return found


def _has_check(function) -> bool:
    source = inspect.getsource(function)
    return any(check in source for check in CHECKS)


def _unchecked(routes, delegated) -> set[tuple[str, str]]:
    missing: set[tuple[str, str]] = set()
    for route in routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not _jira_fields_of(endpoint):
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            chain = [endpoint, *delegated.get((method, route.path), [])]
            linked = all(nxt.__name__ in inspect.getsource(cur) for cur, nxt in zip(chain, chain[1:]))
            if not (linked and _has_check(chain[-1])):
                missing.add((method, route.path))
    return missing


def test_every_route_that_takes_a_jira_key_runs_the_check():
    from app.main import app

    missing = _unchecked(app.routes, _delegated())

    assert missing == set(), (
        "These routes take a Jira project key, issue key or link from the request and do not check it "
        "against the project (defect_jira_service.resolve_jira_project_key / assert_jira_key_allowed): "
        f"{sorted(missing)}"
    )


class _KeyInBody(BaseModel):
    jira_project_key: str


def test_the_structural_check_sees_a_route_that_skips_the_check():
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/in-body")
    async def in_body(payload: _KeyInBody):
        return payload.jira_project_key

    @router.post("/in-query")
    async def in_query(project_key: str):
        return project_key

    @router.post("/checked")
    async def checked(project_key: str):
        return await svc.resolve_jira_project_key(None, None, None, project_key)

    assert _unchecked(router.routes, {}) == {("POST", "/in-body"), ("POST", "/in-query")}


@pytest.mark.asyncio
async def test_the_scope_is_read_from_the_database_not_from_the_request():
    """The check reads ``projects.jira_project_key`` of the project it is given."""
    world = _world(key="TEAM")

    scope = await svc.jira_key_scope(world.db, PID, world.user, cfg=dict(ONLINE_CFG))

    assert (scope.project_key, scope.default_key, scope.any_key) == ("TEAM", "QA", False)
    assert any(str(stmt).startswith("SELECT projects.jira_project_key") for stmt in world.db.statements
               if isinstance(stmt, type(select(Project.jira_project_key))))
