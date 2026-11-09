"""Public read-only demo (DEMO_MODE): writes are refused, demo sign-in is safe.

The public demo is reachable by anyone and everyone shares one account, so:

* every non-GET/HEAD/OPTIONS request is refused with 403 ``demo_read_only``
  before routing or auth, except a short allowlist — and password login is NOT
  on it (the demo signs in with ``/auth/demo-login``);
* ``/auth/demo-login`` does not exist unless DEMO_MODE is on and
  DEMO_USERNAME is set, refuses an admin / service / MFA account, locks the
  user row like every other session issuer, and is per-IP rate limited;
* with DEMO_MODE off nothing changes for a normal deployment.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core import demo_mode
from app.core.config import settings
from app.models.postgres import UserRole

pytestmark = pytest.mark.regression

#: A project id in the shape the onboarding routes take.
PROJECT = "9bc7a197-520e-4d3f-87f9-134a4a726580"


# ── The rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_safe_methods_are_never_blocked(method):
    assert demo_mode.demo_blocks(method, "/api/v1/projects") is False


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects",
        "/api/v1/auth/login",  # password login is closed on the demo
        "/api/v1/auth/register",
        "/api/v1/auth/change-password",
        "/api/v1/keys",
        "/api/v1/ingest",
        "/api/v1/auth/me/dismissals",
        f"/api/v1/onboarding/{PROJECT}/complete",
        f"/api/v1/onboarding/{PROJECT}/skip",
    ],
)
def test_writes_are_blocked(method, path):
    assert demo_mode.demo_blocks(method, path) is True


def test_onboarding_progress_detection_passes_for_a_project_id_only():
    # The onboarding page reads its status through this POST (found by the
    # demo route sweep). It takes no body and recomputes from stored data.
    assert demo_mode.demo_blocks("POST", f"/api/v1/onboarding/{PROJECT}/detect") is False
    assert demo_mode.demo_blocks("POST", f"/api/v1/onboarding/{PROJECT}/detect/") is False
    for path in (
        "/api/v1/onboarding/not-a-project-id/detect",
        f"/api/v1/onboarding/{PROJECT}/detect/extra",
        f"/api/v1/onboarding/{PROJECT}/detectx",
        f"/api/v1/x/onboarding/{PROJECT}/detect",
        f"/api/v1/onboarding/{PROJECT}/detect\n",  # a decoded %0A must not ride on "$"
    ):
        assert demo_mode.demo_blocks("POST", path) is True, path


def test_the_pattern_allowlist_is_only_onboarding_detection():
    assert [p.pattern for p in demo_mode.DEMO_WRITE_PATTERNS] == [
        r"^/api/v1/onboarding/[0-9a-fA-F-]{36}/detect$",
    ]


@pytest.mark.parametrize("path", sorted(demo_mode.DEMO_WRITE_ALLOWLIST))
def test_the_allowlist_passes_with_or_without_a_trailing_slash(path):
    assert demo_mode.demo_blocks("POST", path) is False
    assert demo_mode.demo_blocks("POST", path + "/") is False


def test_the_allowlist_is_exactly_the_session_routes_and_the_simulation():
    # Growing this list widens what an anonymous visitor can do to shared
    # state. A new entry needs the same argument as these four.
    assert demo_mode.DEMO_WRITE_ALLOWLIST == {
        "/api/v1/auth/demo-login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/release-gate-policies/simulate",
    }


def test_the_refusal_is_the_code_message_shape_the_spa_renders():
    assert demo_mode.DEMO_READ_ONLY["code"] == "demo_read_only"
    assert demo_mode.DEMO_READ_ONLY["message"].strip()


def test_demo_mode_is_off_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["DEMO_MODE"].default is False
    assert Settings.model_fields["DEMO_USERNAME"].default == ""


# ── Through the real app ─────────────────────────────────────────────────────


def _client() -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_the_app_refuses_writes_in_demo_mode(monkeypatch, method):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    async with _client() as client:
        response = await client.request(method, "/api/v1/projects", json={})
    assert response.status_code == 403
    assert response.json() == {"detail": demo_mode.DEMO_READ_ONLY}


@pytest.mark.asyncio
async def test_password_login_is_refused_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    async with _client() as client:
        response = await client.post("/api/v1/auth/login", data={"username": "x", "password": "y"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "demo_read_only"


@pytest.mark.asyncio
async def test_writes_reach_the_route_when_demo_mode_is_off(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    async with _client() as client:
        response = await client.post("/api/v1/projects", json={})
    # Unauthenticated, so the route's own guard answers — not the demo block.
    assert response.status_code == 401
    assert "demo_read_only" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_auth_options_reports_demo_mode(monkeypatch, enabled):
    async def _no_smtp() -> bool:
        return False

    monkeypatch.setattr("app.services.account_links_service.smtp_enabled", _no_smtp)
    monkeypatch.setattr(settings, "DEMO_MODE", enabled)
    async with _client() as client:
        response = await client.get("/api/v1/auth/options")
    assert response.status_code == 200
    assert response.json()["demo_mode"] is enabled


def test_demo_login_is_mounted_and_rate_limited():
    from app.main import _AUTH_RATE_LIMITS, app

    path = "/api/v1/auth/demo-login"
    assert path in _AUTH_RATE_LIMITS
    limit, message = _AUTH_RATE_LIMITS[path]
    assert limit.endswith("/minute") and int(limit.split("/")[0]) <= 10 and message
    assert any(
        getattr(r, "path", "") == path and "POST" in getattr(r, "methods", set()) for r in app.routes
    )


# ── demo_login itself ────────────────────────────────────────────────────────


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _Session:
    def __init__(self, user):
        self.user = user
        self.statements: list = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.user)

    async def refresh(self, _obj):
        return None

    async def commit(self):
        self.committed = True


def _user(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "role": UserRole.QA_LEAD,
        "is_service_account": False,
        "mfa_enabled": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def issued(monkeypatch):
    from app.routers import auth

    calls: list[str] = []

    async def _access(_db, user_id):
        calls.append(f"access:{user_id}")
        return "access-token-demo"

    async def _refresh(_db, user_id):
        calls.append(f"refresh:{user_id}")
        return "refresh-token-demo"

    monkeypatch.setattr(auth, "issue_access_jwt", _access)
    monkeypatch.setattr(auth, "issue_refresh_token", _refresh)
    return calls


def _enable(monkeypatch, username="qa_lead"):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "DEMO_USERNAME", username)


@pytest.mark.asyncio
async def test_demo_login_does_not_exist_when_demo_mode_is_off(monkeypatch, issued):
    from app.routers.auth import demo_login

    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "DEMO_USERNAME", "qa_lead")
    with pytest.raises(HTTPException) as exc:
        await demo_login(db=_Session(_user()))
    assert exc.value.status_code == 404
    assert issued == []


@pytest.mark.asyncio
async def test_demo_login_does_not_exist_without_a_username(monkeypatch, issued):
    from app.routers.auth import demo_login

    _enable(monkeypatch, username="   ")
    with pytest.raises(HTTPException) as exc:
        await demo_login(db=_Session(_user()))
    assert exc.value.status_code == 404
    assert issued == []


@pytest.mark.asyncio
async def test_demo_login_is_503_while_the_account_is_missing(monkeypatch, issued):
    from app.routers.auth import demo_login

    _enable(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await demo_login(db=_Session(None))
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "demo_unavailable"
    assert issued == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"role": UserRole.ADMIN},
        {"role": "ADMIN"},
        {"is_service_account": True},
        {"mfa_enabled": True},
    ],
)
async def test_demo_login_refuses_an_account_that_matters(monkeypatch, issued, overrides):
    from app.routers.auth import demo_login

    _enable(monkeypatch)
    session = _Session(_user(**overrides))
    with pytest.raises(HTTPException) as exc:
        await demo_login(db=session)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "demo_account_refused"
    assert issued == [] and session.committed is False


@pytest.mark.asyncio
async def test_demo_login_signs_in_the_demo_account_and_locks_the_row(monkeypatch, issued):
    from app.routers.auth import demo_login

    _enable(monkeypatch)
    user = _user()
    session = _Session(user)
    token = await demo_login(db=session)

    assert token.access_token == "access-token-demo"
    assert token.refresh_token == "refresh-token-demo"
    assert token.token_type == "bearer"
    assert issued == [f"access:{user.id}", f"refresh:{user.id}"]
    assert session.committed is True
    assert any(getattr(s, "_for_update_arg", None) is not None for s in session.statements), (
        "demo_login must lock the user row before issuing a session"
    )


@pytest.mark.asyncio
async def test_demo_login_works_end_to_end_through_the_middleware(monkeypatch, issued):
    from app.db.postgres import get_db
    from app.main import app

    _enable(monkeypatch)
    session = _Session(_user())

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    try:
        async with _client() as client:
            response = await client.post("/api/v1/auth/demo-login")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200, response.text
    assert response.json()["access_token"] == "access-token-demo"
