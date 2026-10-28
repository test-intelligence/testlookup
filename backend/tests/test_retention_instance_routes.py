"""W22b ACT-06 W-6 — the four instance-default routes, through FastAPI's real guards.

A mini app mounts ``routers/app_settings.py`` alone: ``require_role(ADMIN)``
runs as a dependency (only ``get_current_active_user`` and ``get_db`` are
overridden), so the role table, the project-bound key refusal and the JWT-only
check on the two writes are exercised for real. The session is the
statement-routing fake of ``test_retention_instance_service``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    CREDENTIAL_KIND_JWT,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.routers import app_settings as route  # noqa: E402
from app.services import retention_instance_service as inst_svc  # noqa: E402
from app.services.retention_vocab import POLICY_ERROR_SENTENCES  # noqa: E402
from tests.test_retention_instance_service import _affected_row, _instance, _InstDB  # noqa: E402

_BASE = "/api/v1/settings/retention-defaults"
_ROUTES = (
    ("GET", _BASE, None),
    ("PUT", _BASE, {"runs_days": 180}),
    ("GET", f"{_BASE}/enable-preview", None),
    ("POST", f"{_BASE}/enable", {"confirmation": "enable retention for 1 project", "preview_hash": "a" * 64}),
)


def _user(role, *, credential=CREDENTIAL_KIND_JWT, bound_project=None):
    user = SimpleNamespace(
        id=uuid.uuid4(), role=role, full_name="Avery Admin", username="avery-admin",
        email="avery@example.invalid", is_active=True,
    )
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


def _client(session, user) -> AsyncClient:
    app = FastAPI()
    app.include_router(route.router)

    async def _db():
        yield session

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _call(client, method, path, body):
    return await client.request(method, path, json=body) if body is not None else await client.request(method, path)


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
@pytest.mark.asyncio
async def test_every_route_is_admin_only(role, method, path, body):
    session = _InstDB(instance=_instance())
    async with _client(session, _user(role)) as client:
        resp = await _call(client, method, path, body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least ADMIN role"
    assert session.statements == [] and session.added == [] and session.commits == 0


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
@pytest.mark.asyncio
async def test_a_project_bound_admin_key_is_refused_everywhere(method, path, body):
    session = _InstDB(instance=_instance())
    user = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=uuid.uuid4())
    async with _client(session, user) as client:
        resp = await _call(client, method, path, body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    assert session.added == [] and session.commits == 0


@pytest.mark.parametrize(("method", "path", "body"), [r for r in _ROUTES if r[0] != "GET"])
@pytest.mark.asyncio
async def test_an_admin_api_key_cannot_write(method, path, body):
    session = _InstDB(instance=_instance())
    user = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY)
    async with _client(session, user) as client:
        resp = await _call(client, method, path, body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {
        "code": "instance_interactive_only", "message": POLICY_ERROR_SENTENCES["instance_interactive_only"],
    }
    assert session.statements == [] and session.added == [] and session.commits == 0


@pytest.mark.asyncio
async def test_an_admin_api_key_can_read_both():
    session = _InstDB(instance=_instance(runs_days=180), affected=[_affected_row("Payments sentinel")])
    user = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY)
    async with _client(session, user) as client:
        read = await client.get(_BASE)
        preview = await client.get(f"{_BASE}/enable-preview")
    assert read.status_code == 200 and read.json()["runs_days"] == 180
    assert preview.status_code == 200 and preview.json()["total"] == 1


@pytest.mark.asyncio
async def test_the_preview_writes_nothing():
    session = _InstDB(instance=_instance(), affected=[_affected_row("Payments sentinel"), _affected_row("Ledger sentinel")])
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(f"{_BASE}/enable-preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confirmation_phrase"] == "enable retention for 2 projects"
    assert [item["name"] for item in body["items"]] == ["Payments sentinel", "Ledger sentinel"]
    assert (session.added, session.flushes, session.commits) == ([], 0, 0)


@pytest.mark.asyncio
async def test_put_enabled_true_is_refused_with_its_sentence():
    session = _InstDB(instance=_instance())
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.put(_BASE, json={"enabled": True})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "enable_via_flow", "message": POLICY_ERROR_SENTENCES["enable_via_flow"]}
    assert session.added == [] and session.commits == 0


@pytest.mark.asyncio
async def test_put_saves_audits_and_commits_once():
    session = _InstDB(instance=_instance(), counts=(3, 3, 4, 3, 3, 3, 3))   # INHERITABLE_FIELDS order
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.put(_BASE, json={"runs_days": 180, "clear": ["artifacts_days"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runs_days"] == 180 and body["effective"]["runs_days"] == 180
    assert body["inheriting_projects"]["runs_days"] == 4
    assert [type(o).__name__ for o in session.added] == ["SettingsAuditLog"]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_put_audit_below_runs_answers_the_plain_sentence():
    session = _InstDB(instance=_instance())
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.put(_BASE, json={"runs_days": 3000})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("audit_days must be >= runs_days")
    assert session.commits == 0


@pytest.mark.asyncio
async def test_enable_with_the_preview_and_the_phrase_switches_it_on():
    rows = [_affected_row("Payments sentinel")]
    session = _InstDB(instance=_instance(), affected=rows)
    digest = inst_svc.preview_hash([rows[0][0]], inst_svc.InstanceRetentionValues())
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(
            f"{_BASE}/enable", json={"confirmation": "enable retention for 1 project", "preview_hash": digest},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True
    assert session.commits == 1


@pytest.mark.asyncio
async def test_enable_refusals_answer_code_and_message():
    rows = [_affected_row("Payments sentinel")]
    digest = inst_svc.preview_hash([rows[0][0]], inst_svc.InstanceRetentionValues())
    session = _InstDB(instance=_instance(), affected=rows)
    async with _client(session, _user("ADMIN")) as client:
        stale = await client.post(f"{_BASE}/enable", json={"confirmation": "enable retention for 1 project",
                                                           "preview_hash": "b" * 64})
        typo = await client.post(f"{_BASE}/enable", json={"confirmation": "enable retention for 1 projects",
                                                          "preview_hash": digest})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "preview_stale"
    assert typo.status_code == 422 and typo.json()["detail"]["code"] == "confirmation_mismatch"
    assert session.commits == 0 and session.instance.enabled is False
