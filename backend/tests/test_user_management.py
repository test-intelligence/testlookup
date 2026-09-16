"""
Unit tests for user management endpoints.
All DB calls and heavy dependencies (bcrypt, jose) are mocked.

Strategy: stub only native deps (bcrypt, jose) when they are not installed.
The real app.core / app.db modules import without Docker or env vars, so
they are never stubbed (see tests/test_no_stubbed_app_modules.py).
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    """Create a lightweight stub module."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Only stub packages that are NOT installed in the local venv.
# sqlalchemy, fastapi, pydantic ARE installed — do not stub them.
#
# NOTE: Instead of writing into sys.modules at import time (which leaks
# globally for the entire pytest session), we scope these stubs to each
# test via an autouse fixture and pytest's monkeypatch support.

@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub native deps and app.core/app.db modules for each test in this file."""
    with monkeypatch.context() as m:
        # Native deps: bcrypt
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(
                sys.modules,
                "bcrypt",
                _make_stub(
                    "bcrypt",
                    checkpw=MagicMock(return_value=True),
                    hashpw=MagicMock(return_value=b"$2b$fake"),
                    gensalt=MagicMock(return_value=b"$2b$12$salt"),
                ),
            )

        # Native deps: jose
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub(
                "jose.jwt",
                encode=MagicMock(return_value="tok"),
                decode=MagicMock(return_value={}),
            )
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(
                sys.modules,
                "jose",
                _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception),
            )

        # The membership routes invalidate a Redis cache after a write. The
        # real function waits out a Redis connect timeout (~4s) before it
        # swallows the error, so patch that one attribute on the real module.
        m.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())

        # Yield control to the test; monkeypatch will restore sys.modules after.
        yield
# ── Now safe to import app modules ────────────────────────────────────────────

from app.models.postgres import UserRole  # noqa: E402
from app.models.schemas import (  # noqa: E402
    AdminCreateUserRequest,
    ApiKeyCreate,
    InviteUserRequest,
)


# ── Helpers ────────────────────────────────────────────────────

def _make_user(role: UserRole = UserRole.QA_ENGINEER, is_active: bool = True) -> MagicMock:
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "test@example.com"
    u.username = "testuser"
    u.full_name = "Test User"
    u.role = role
    u.is_active = is_active
    u.hashed_password = "hashed"
    u.created_at = datetime.now(timezone.utc)
    return u


def _execute_result(scalar_value=None, all_value=None) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_value
    r.scalars.return_value.all.return_value = all_value or []
    return r


# ── Schema validation ──────────────────────────────────────────

class TestAdminCreateUserRequest:
    def test_valid_payload(self):
        req = AdminCreateUserRequest(
            email="newuser@example.com",
            username="newuser",
            role=UserRole.QA_ENGINEER,
        )
        assert req.email == "newuser@example.com"
        assert req.username == "newuser"
        assert req.role == UserRole.QA_ENGINEER
        assert req.full_name is None

    def test_username_too_short_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AdminCreateUserRequest(email="x@x.com", username="ab")

    def test_username_too_long_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AdminCreateUserRequest(email="x@x.com", username="a" * 51)

    def test_invalid_email_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AdminCreateUserRequest(email="not-an-email", username="validuser")

    def test_full_name_optional(self):
        req = AdminCreateUserRequest(
            email="u@example.com",
            username="myuser",
            full_name="Jane Doe",
        )
        assert req.full_name == "Jane Doe"

    def test_default_role_is_qa_engineer(self):
        req = AdminCreateUserRequest(email="u@example.com", username="usr123")
        assert req.role == UserRole.QA_ENGINEER


# ── AdminCreateUser endpoint ───────────────────────────────────

class TestAdminCreateUser:
    @pytest.mark.asyncio
    async def test_happy_path_returns_forced_reset_temp_password(self):
        """The one-time admin credential must require replacement after login."""
        from app.routers.users import admin_create_user

        admin = _make_user(role=UserRole.ADMIN)
        user_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)

        db = AsyncMock()
        db.add = MagicMock()
        db.execute.return_value = _execute_result(scalar_value=None)

        # Simulate DB refresh: set PK and server-default fields on the ORM object
        async def _refresh(obj):
            obj.id = user_id
            obj.is_active = True
            obj.created_at = created_at

        db.refresh = AsyncMock(side_effect=_refresh)

        payload = AdminCreateUserRequest(
            email="new@example.com",
            username="newuser",
            role=UserRole.QA_ENGINEER,
        )

        with patch("app.routers.users.get_password_hash", return_value="hashed_temp"):
            result = await admin_create_user(payload=payload, db=db, current_user=admin)

        db.add.assert_called_once()
        created_user = db.add.call_args.args[0]
        assert created_user.must_change_password is True
        db.commit.assert_awaited_once()
        assert result.temp_password
        assert len(result.temp_password) >= 8
        assert result.email == "new@example.com"
        assert result.username == "newuser"

    @pytest.mark.asyncio
    async def test_duplicate_email_or_username_raises_409(self):
        from fastapi import HTTPException
        from app.routers.users import admin_create_user

        admin = _make_user(role=UserRole.ADMIN)
        existing = _make_user()

        db = AsyncMock()
        db.execute.return_value = _execute_result(scalar_value=existing)

        payload = AdminCreateUserRequest(email="existing@example.com", username="existing")

        with pytest.raises(HTTPException) as exc_info:
            await admin_create_user(payload=payload, db=db, current_user=admin)

        assert exc_info.value.status_code == 409

# ── list_api_keys endpoint ─────────────────────────────────────

class TestListApiKeys:
    @pytest.mark.asyncio
    async def test_returns_active_keys_for_current_user(self):
        from app.routers.api_keys import list_api_keys

        user = _make_user()
        key = MagicMock()
        key.id = uuid.uuid4()
        key.name = "CI Key"
        key.key_hint = "qai_abc123..."
        key.scopes = ["test:read"]
        key.project_id = None
        key.is_active = True
        key.expires_at = None
        key.last_used_at = None
        key.created_at = datetime.now(timezone.utc)

        db = AsyncMock()
        db.execute.return_value = _execute_result(all_value=[key])

        result = await list_api_keys(db=db, current_user=user)
        assert len(result) == 1
        assert result[0].id == key.id
        assert result[0].name == key.name
        assert result[0].scopes == ["test:read"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_keys(self):
        from app.routers.api_keys import list_api_keys

        user = _make_user()
        db = AsyncMock()
        db.execute.return_value = _execute_result(all_value=[])

        result = await list_api_keys(db=db, current_user=user)
        assert result == []

    @pytest.mark.asyncio
    async def test_normalizes_null_scopes_for_legacy_keys(self):
        from app.routers.api_keys import list_api_keys

        user = _make_user()
        key = MagicMock()
        key.id = uuid.uuid4()
        key.name = "Legacy Key"
        key.key_hint = "qai_legacy..."
        key.scopes = None
        key.project_id = None
        key.is_active = True
        key.expires_at = None
        key.last_used_at = None
        key.created_at = datetime.now(timezone.utc)

        db = AsyncMock()
        db.execute.return_value = _execute_result(all_value=[key])

        result = await list_api_keys(db=db, current_user=user)
        assert len(result) == 1
        assert result[0].scopes == []


# ── create_api_key endpoint ────────────────────────────────────

class TestCreateApiKey:
    @pytest.mark.asyncio
    async def test_raw_key_starts_with_qai_prefix(self):
        from app.routers.api_keys import create_api_key

        user = _make_user()
        new_key = MagicMock()
        new_key.id = uuid.uuid4()
        new_key.name = "Test Key"
        new_key.key_hint = "qai_test12..."
        new_key.scopes = []
        new_key.project_id = None
        new_key.is_active = True
        new_key.expires_at = None
        new_key.last_used_at = None
        new_key.created_at = datetime.now(timezone.utc)

        db = AsyncMock()
        db.add = MagicMock()
        db.refresh = AsyncMock()

        payload = ApiKeyCreate(name="Test Key", scopes=[])

        with patch("app.routers.api_keys.ApiKey", return_value=new_key):
            result = await create_api_key(payload=payload, db=db, current_user=user)

        assert result.raw_key.startswith("qai_")
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    def test_hash_function_uses_sha256(self):
        from app.routers.api_keys import _hash_key
        raw = "qai_mysecrettoken"
        assert _hash_key(raw) == hashlib.sha256(raw.encode()).hexdigest()

    def test_raw_key_not_stored_plaintext(self):
        from app.routers.api_keys import _hash_key
        raw = "qai_mysecrettoken"
        assert _hash_key(raw) != raw


# ── invite_user endpoint ───────────────────────────────────────

class TestInviteUser:
    @pytest.mark.asyncio
    async def test_creates_invitation_with_register_link(self):
        from app.routers.users import invite_user

        admin = _make_user(role=UserRole.ADMIN)
        inv_id = uuid.uuid4()

        db = AsyncMock()
        db.add = MagicMock()
        db.execute.return_value = _execute_result(scalar_value=None)

        # Simulate DB refresh: set the PK that the DB would normally assign
        async def _refresh(obj):
            obj.id = inv_id

        db.refresh = AsyncMock(side_effect=_refresh)

        payload = InviteUserRequest(email="newbie@example.com")

        result = await invite_user(payload=payload, db=db, current_user=admin)

        assert "register" in result.invitation_link
        assert "newbie@example.com" in result.invitation_link
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_already_registered_email(self):
        from fastapi import HTTPException
        from app.routers.users import invite_user

        admin = _make_user(role=UserRole.ADMIN)
        existing_user = _make_user()

        db = AsyncMock()
        db.execute.return_value = _execute_result(scalar_value=existing_user)

        payload = InviteUserRequest(email="existing@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await invite_user(payload=payload, db=db, current_user=admin)

        assert exc_info.value.status_code == 409


class TestProjectMembers:
    @pytest.mark.asyncio
    async def test_list_project_members_normalizes_legacy_role_strings(self):
        from app.routers.users import list_project_members

        current_user = _make_user(role=UserRole.ADMIN)
        project = MagicMock()
        project.id = uuid.uuid4()

        member = MagicMock()
        member.id = uuid.uuid4()
        member.user_id = uuid.uuid4()
        member.project_id = project.id
        member.role = "UserRole.ADMIN"
        member.created_at = datetime.now(timezone.utc)

        member_user = _make_user(role=UserRole.ADMIN)
        member_user.id = member.user_id

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _execute_result(scalar_value=project),
            MagicMock(all=MagicMock(return_value=[(member, member_user)])),
        ])

        result = await list_project_members(project_id=project.id, db=db, current_user=current_user)

        assert len(result) == 1
        assert result[0].role == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_add_project_member_stores_canonical_role_value(self):
        from app.models.schemas import AddProjectMemberRequest
        from app.routers.users import add_project_member

        current_user = _make_user(role=UserRole.ADMIN)
        project = MagicMock()
        project.id = uuid.uuid4()
        member_user = _make_user(role=UserRole.TESTER)
        member_user.id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _execute_result(scalar_value=project),
            _execute_result(scalar_value=member_user),
            _execute_result(scalar_value=None),
        ])
        db.add = MagicMock()

        async def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = created_at

        db.refresh = AsyncMock(side_effect=_refresh)
        payload = AddProjectMemberRequest(user_id=member_user.id, role=UserRole.QA_LEAD)

        result = await add_project_member(project_id=project.id, payload=payload, db=db, current_user=current_user)

        added_member = next(
            call.args[0]
            for call in db.add.call_args_list
            if hasattr(call.args[0], "project_id") and hasattr(call.args[0], "role")
        )
        assert added_member.role == UserRole.QA_LEAD.value
        assert result.role == UserRole.QA_LEAD

    @pytest.mark.asyncio
    async def test_update_project_member_role_rewrites_legacy_enum_string(self):
        from app.models.schemas import UpdateProjectMemberRoleRequest
        from app.routers.users import update_project_member_role

        current_user = _make_user(role=UserRole.ADMIN)
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        member = MagicMock()
        member.id = uuid.uuid4()
        member.user_id = user_id
        member.project_id = project_id
        member.role = "UserRole.TESTER"
        member.created_at = datetime.now(timezone.utc)

        member_user = _make_user(role=UserRole.TESTER)
        member_user.id = user_id

        row = (member, member_user)
        db = AsyncMock()
        db.execute.return_value = MagicMock(one_or_none=MagicMock(return_value=row))
        db.refresh = AsyncMock()

        result = await update_project_member_role(
            project_id=project_id,
            user_id=user_id,
            payload=UpdateProjectMemberRoleRequest(role=UserRole.ADMIN),
            db=db,
            current_user=current_user,
        )

        assert member.role == UserRole.ADMIN.value
        assert result.role == UserRole.ADMIN
