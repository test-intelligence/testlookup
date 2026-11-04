"""W31 STRAT-12: an erased account is never changed or restored; the pseudonym space is reserved (§3.2, E-4).

Every §3.2 user route refuses an erased target with 409 and the STRING sentence
(the user router's style, which its callers toast); the reserved ``erased-``
prefix is refused on create / invite / edit / SCIM create + update / SSO JIT
(and on the invitation accept and the self-registration, which also choose a
username); SCIM ``PATCH active=true`` of an erased account is a SCIM 409;
``verify_password`` answers False for the erased marker without raising.
The real routers on a bare FastAPI app over the SQLite world of
``test_subject_erasure_service.py``.
"""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.deps import get_current_active_user
from app.core.security import get_password_hash, verify_password
from app.db.postgres import get_db
from app.models.postgres import Base, User, UserRole
from app.routers import users as users_routes
from app.services import scim_service
from app.services.subject_rights_vocab import ERASED_HASH_MARKER, ERROR_SENTENCES
from app.services.user_account_guards import is_reserved_identity, refuse_reserved_username
from tests.test_subject_erasure_service import _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

ERASED = ERROR_SENTENCES["user_erased"]
RESERVED = ERROR_SENTENCES["username_reserved"]
APP = Path(__file__).resolve().parents[1] / "app"


def _client(maker: Any, user: Any, *routers: Any) -> AsyncClient:
    app = FastAPI()
    for router in routers or (users_routes.router, users_routes.projects_router):
        app.include_router(router)

    async def _db() -> Any:
        async with maker.session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def erased(maker: Any, world: SimpleNamespace) -> SimpleNamespace:
    """The subject, erased (the row as the Postgres phase leaves it), with one leftover membership row so the
    member-role route reaches its refusal, and a project to add them to."""
    project_id = uuid.uuid4()
    async with maker.session() as db:
        await db.execute(Base.metadata.tables["projects"].insert().values(id=project_id, name="zqxw project",
                                                                         slug=f"zqxw-{project_id.hex[:8]}"))
        row = await db.get(User, world.subject.id)
        row.username, row.email = "erased-0a1b2c3d", "erased-0a1b2c3d@erased.invalid"
        row.full_name, row.hashed_password, row.is_active = "Erased user", ERASED_HASH_MARKER, False
        row.erased_at = datetime.now(timezone.utc)
        await db.execute(Base.metadata.tables["project_members"].insert().values(
            id=uuid.uuid4(), user_id=world.subject.id, project_id=project_id, role="TESTER"))
        await db.commit()
    return SimpleNamespace(user_id=world.subject.id, project_id=project_id, admin=world.admin)


# ── an erased account: every mutating user route answers 409 with the sentence ─

@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/v1/users/{uid}/role", {"role": "VIEWER"}),
        ("PATCH", "/api/v1/users/{uid}/status", {"is_active": True}),
        ("PATCH", "/api/v1/users/{uid}", {"full_name": "Back Again"}),
        ("PATCH", "/api/v1/users/{uid}", {"is_active": True}),
        ("POST", "/api/v1/users/{uid}/password-reset-link", None),
        ("POST", "/api/v1/projects/{pid}/members", {"user_id": "{uid}", "role": "TESTER"}),
        ("PATCH", "/api/v1/projects/{pid}/members/{uid}", {"role": "QA_LEAD"}),
    ],
)
async def test_every_user_route_refuses_an_erased_account(
    maker: Any, erased: SimpleNamespace, method: str, path: str, body: Any,
) -> None:
    def fill(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("{uid}", str(erased.user_id)).replace("{pid}", str(erased.project_id))
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        return value

    async with _client(maker, erased.admin) as client:
        response = await client.request(method, fill(path), json=fill(body))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == ERASED
    async with maker.session() as db:
        row = await db.get(User, erased.user_id)
        assert row.is_active is False and row.full_name == "Erased user" and row.role == "TESTER"
        tokens = Base.metadata.tables["password_reset_tokens"]
        assert (await db.execute(select(func.count()).select_from(tokens))).scalar() == 0


async def test_a_live_account_still_changes(maker: Any, world: SimpleNamespace) -> None:
    async with _client(maker, world.admin) as client:
        response = await client.patch(f"/api/v1/users/{world.subject.id}", json={"full_name": "Zqxw Renamed"})
    assert response.status_code == 200 and response.json()["full_name"] == "Zqxw Renamed"
    assert response.json()["is_erased"] is False


# ── the reserved pseudonym space ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("username", "email", "reserved"),
    [
        ("erased-0a1b2c3d", None, True), ("Erased-X", None, True), (" erased-x", None, True),
        (None, "erased-x@example.org", True), (None, "ERASED-x@example.org", True),
        ("erasedx", "x.erased-@example.org", False), ("not-erased-x", "a@erased-mail.org", False), (None, None, False),
    ],
)
def test_the_reserved_prefix(username: Any, email: Any, reserved: bool) -> None:
    assert is_reserved_identity(username=username, email=email) is reserved
    if reserved:
        with pytest.raises(HTTPException) as refused:
            refuse_reserved_username(username=username, email=email)
        assert refused.value.status_code == 422 and refused.value.detail == RESERVED


async def test_create_invite_and_edit_refuse_the_reserved_prefix(maker: Any, world: SimpleNamespace) -> None:
    async with _client(maker, world.admin) as client:
        created = await client.post("/api/v1/users", json={"email": "zqxw.new@example.org", "username": "erased-0a1b"})
        created_mail = await client.post("/api/v1/users", json={"email": "erased-0a1b@example.org", "username": "zqxwnew"})
        invited = await client.post("/api/v1/users/invite", json={"email": "Erased-0a1b@example.org"})
        edited = await client.patch(f"/api/v1/users/{world.subject.id}", json={"username": "erased-0a1b2c3d"})
        edited_mail = await client.patch(f"/api/v1/users/{world.subject.id}", json={"email": "erased-q@example.org"})
    for response in (created, created_mail, invited, edited, edited_mail):
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == RESERVED
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).username == "zqxwsubj"
        assert (await db.execute(select(func.count()).select_from(User))).scalar() == 4


async def test_an_unchanged_legacy_name_does_not_block_other_edits(maker: Any, world: SimpleNamespace) -> None:
    async with maker.session() as db:
        db.add(_user(UserRole.TESTER, "erased-legacy"))
        await db.commit()
        legacy = (await db.execute(select(User).where(User.username == "erased-legacy"))).scalar_one()
    async with _client(maker, world.admin) as client:
        response = await client.patch(f"/api/v1/users/{legacy.id}",
                                      json={"username": "erased-legacy", "full_name": "Kept Name"})
    assert response.status_code == 200 and response.json()["full_name"] == "Kept Name"


async def test_accept_invitation_and_register_refuse_the_reserved_prefix(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "REGISTRATION_ENABLED", True)
    async with _client(maker, None, auth_routes.router) as client:
        accepted = await client.post("/api/v1/auth/invitations/accept", json={
            "token": "t" * 40, "username": "erased-0a1b", "password": "zqxw-Passw0rd!"})
        registered = await client.post("/api/v1/auth/register", json={
            "email": "zqxw.reg@example.org", "username": "Erased-reg", "password": "zqxw-Passw0rd!"})
    assert accepted.status_code == 422 and accepted.json()["detail"] == RESERVED
    assert registered.status_code == 422 and registered.json()["detail"] == RESERVED


# ── SCIM: reserved values on create / update; an erased account is a SCIM 409 ──

async def test_scim_refuses_the_reserved_prefix(maker: Any, world: SimpleNamespace) -> None:
    async with maker.session() as db:
        for username, email in (("erased-0a1b", "zqxw.scim@example.org"), ("zqxwscim", "erased-0a1b@example.org")):
            with pytest.raises(HTTPException) as refused:
                await scim_service.scim_create_user(db, username=username, email=email)
            assert refused.value.status_code == 400
            assert refused.value.detail == {"detail": RESERVED, "scimType": "invalidValue"}
        for change in ({"username": "erased-0a1b"}, {"email": "erased-0a1b@example.org"}):
            with pytest.raises(HTTPException) as refused:
                await scim_service.scim_update_user(db, world.subject.id, **change)
            assert refused.value.status_code == 400 and refused.value.detail["scimType"] == "invalidValue"
        await db.rollback()
        assert (await db.get(User, world.subject.id)).username == "zqxwsubj"


async def test_scim_patch_active_true_of_an_erased_account_is_409(maker: Any, erased: SimpleNamespace) -> None:
    from app.routers import scim as scim_routes

    app = FastAPI()
    app.include_router(scim_routes.router)

    async def _db() -> Any:
        async with maker.session() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[scim_routes.verify_scim_bearer] = lambda: SimpleNamespace(sso_config_id=None)
    body = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patched = await client.patch(f"/api/v1/scim/v2/Users/{erased.user_id}", json=body)
    assert patched.status_code == 409, patched.text
    assert ERASED in patched.text
    async with maker.session() as db:
        assert (await db.get(User, erased.user_id)).is_active is False
        # the IdP's DELETE (deactivate only) stays a no-op success for an erased row
        await scim_service.scim_update_user(db, erased.user_id, active=False)
        with pytest.raises(HTTPException) as refused:
            await scim_service.scim_update_user(db, erased.user_id, display_name="Back Again")
        assert refused.value.status_code == 409 and refused.value.detail == ERASED
        await db.rollback()


# ── SSO JIT: never re-link an erased row, never provision the reserved space ──

def test_the_jit_refusal() -> None:
    from app.routers.sso import _jit_refusal

    erased_row = SimpleNamespace(username="erased-0a1b", email="erased-0a1b@erased.invalid",
                                 erased_at=datetime.now(timezone.utc))
    assert _jit_refusal(erased_row, False) == "account is deactivated"   # no new message: no oracle
    squatter = SimpleNamespace(username="erased-joe", email="erased-joe@corp.example", erased_at=None)
    assert _jit_refusal(squatter, True) == RESERVED
    assert _jit_refusal(squatter, False) is None   # an existing (pre-W31) account keeps signing in
    assert _jit_refusal(SimpleNamespace(username="joe", email="joe@corp.example", erased_at=None), True) is None


def test_both_jit_call_sites_refuse_and_roll_back_first() -> None:
    """The OIDC callback and the SAML ACS each call ``_jit_refusal`` right after ``jit_provision_or_link``
    and roll the staged link / account back before answering (the OIDC refusal commits its audit event)."""
    tree = ast.parse((APP / "routers" / "sso.py").read_text(encoding="utf-8"))
    sites = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        calls = [node.func for node in ast.walk(fn) if isinstance(node, ast.Call)]
        names = [getattr(func, "id", None) or getattr(func, "attr", None) for func in calls]
        if "jit_provision_or_link" in names:
            sites += 1
            assert "_jit_refusal" in names and "rollback" in names, fn.name
    assert sites == 2


# ── verify_password and the erased marker ─────────────────────────────────────

@pytest.mark.parametrize("stored", [ERASED_HASH_MARKER, "", "plain-text", "$2b$broken"])
def test_verify_password_is_false_for_a_non_bcrypt_value_without_raising(stored: str) -> None:
    assert verify_password("x", stored) is False
    assert verify_password(ERASED_HASH_MARKER, stored) is False


def test_verify_password_still_verifies_a_real_hash() -> None:
    hashed = get_password_hash("zqxw-Passw0rd!")
    assert verify_password("zqxw-Passw0rd!", hashed) is True
    assert verify_password("wrong", hashed) is False
