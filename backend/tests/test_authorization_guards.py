"""
Negative-access tests for every authorization guard in ``app.core.deps``.

The architectural ratchet (``test_architectural_authorization.py``) asserts
that every scoped route has a guard wired into its dependency chain. That's
a **structural** check. This file is the **behavioral** half:

    * non-members get 403
    * owners / project members get through
    * admin bypasses
    * missing/invalid path params fail loud
    * missing resources return 404

Each guard's ``_check`` closure is called directly with a mocked DB and a
fake user, so these tests don't need a running database or HTTP client.
``db.execute`` is stubbed with ``side_effect`` returning :class:`FakeExecuteResult`
instances in the order the guard's queries fire.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.deps import (
    require_api_key_owner,
    require_generation_batch_access,
    require_knowledge_source_access,
    require_link_access,
    require_live_session_access,
    require_project_access,
    require_release_access,
    require_run_access,
    require_session_access,
)
from app.models.postgres import UserRole

from tests.conftest import FakeExecuteResult


# ── Helpers ─────────────────────────────────────────────────────────────────


def _user(role: UserRole = UserRole.QA_ENGINEER, user_id: uuid.UUID | None = None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role)


def _request(**path_params):
    return SimpleNamespace(path_params=path_params)


def _db_returning(*results):
    """Fake AsyncSession whose ``execute`` returns the given FakeExecuteResults in order."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    return db


def _scalar(value):
    return FakeExecuteResult(scalar_value=value)


# ── require_project_access ──────────────────────────────────────────────────


class TestRequireProjectAccess:
    @pytest.mark.asyncio
    async def test_non_member_gets_403(self):
        guard = require_project_access()
        db = _db_returning(_scalar(None))  # no ProjectMember row
        req = _request(project_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_member_passes(self):
        guard = require_project_access()
        db = _db_returning(_scalar(uuid.uuid4()))  # membership row found
        req = _request(project_id=str(uuid.uuid4()))
        user = _user()
        result = await guard(req, db, user)
        assert result is user

    @pytest.mark.asyncio
    async def test_admin_bypasses_without_query(self):
        guard = require_project_access()
        db = _db_returning()  # zero queries expected
        req = _request(project_id=str(uuid.uuid4()))
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is admin
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_400(self):
        guard = require_project_access()
        db = _db_returning()
        req = _request(project_id="not-a-uuid")
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 400


# ── require_run_access ──────────────────────────────────────────────────────


class TestRequireRunAccess:
    @pytest.mark.asyncio
    async def test_non_member_gets_403(self):
        guard = require_run_access()
        db = _db_returning(
            _scalar(uuid.uuid4()),  # TestRun.project_id
            _scalar(None),           # no ProjectMember
        )
        req = _request(run_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_run_not_found_returns_404(self):
        guard = require_run_access()
        db = _db_returning(_scalar(None))  # TestRun.project_id lookup empty
        req = _request(run_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_bypasses(self):
        guard = require_run_access()
        db = _db_returning()
        req = _request(run_id=str(uuid.uuid4()))
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is admin
        db.execute.assert_not_awaited()


# ── Factory-backed project-scoped guards ────────────────────────────────────
#
# ``require_release_access`` / ``require_knowledge_source_access`` /
# ``require_generation_batch_access`` all share the same two-query shape:
#   1. SELECT model.project_id WHERE model.id = :rid
#   2. SELECT ProjectMember.id WHERE user_id = :uid AND project_id = :pid
# The tests below cover the denial path for each so a regression in any
# one factory wrapper trips a distinct failure.


class TestFactoryProjectScopedGuards:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "guard_factory, path_param",
        [
            (require_release_access, "release_id"),
            (require_knowledge_source_access, "source_id"),
            (require_generation_batch_access, "batch_id"),
            (require_live_session_access, "session_id"),
        ],
    )
    async def test_non_member_gets_403(self, guard_factory, path_param):
        guard = guard_factory()
        db = _db_returning(
            _scalar(uuid.uuid4()),  # resource exists, returns project_id
            _scalar(None),           # no membership
        )
        req = _request(**{path_param: str(uuid.uuid4())})
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "guard_factory, path_param",
        [
            (require_release_access, "release_id"),
            (require_knowledge_source_access, "source_id"),
            (require_generation_batch_access, "batch_id"),
            (require_live_session_access, "session_id"),
        ],
    )
    async def test_resource_not_found_returns_404(self, guard_factory, path_param):
        guard = guard_factory()
        db = _db_returning(_scalar(None))  # resource lookup empty
        req = _request(**{path_param: str(uuid.uuid4())})
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "guard_factory, path_param",
        [
            (require_release_access, "release_id"),
            (require_knowledge_source_access, "source_id"),
            (require_generation_batch_access, "batch_id"),
            (require_live_session_access, "session_id"),
        ],
    )
    async def test_admin_bypasses(self, guard_factory, path_param):
        guard = guard_factory()
        db = _db_returning()
        req = _request(**{path_param: str(uuid.uuid4())})
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is admin
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_path_param_is_dev_error_500(self):
        guard = require_release_access()
        db = _db_returning()
        req = _request()  # no release_id
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 500


# ── require_session_access (chat sessions, creator-only) ────────────────────


class TestRequireSessionAccess:
    @pytest.mark.asyncio
    async def test_non_owner_denied(self):
        guard = require_session_access()
        session_owner_id = uuid.uuid4()
        session_row = SimpleNamespace(id=uuid.uuid4(), user_id=session_owner_id, project_id=None)
        db = _db_returning(_scalar(session_row))
        req = _request(session_id=str(session_row.id))
        # Caller is NOT the owner
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user(user_id=uuid.uuid4()))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_receives_session(self):
        guard = require_session_access()
        owner_id = uuid.uuid4()
        session_row = SimpleNamespace(id=uuid.uuid4(), user_id=owner_id, project_id=None)
        db = _db_returning(_scalar(session_row))
        req = _request(session_id=str(session_row.id))
        caller = _user(user_id=owner_id)
        result = await guard(req, db, caller)
        assert result is session_row  # guard returns the resource, not the user

    @pytest.mark.asyncio
    async def test_admin_bypasses_ownership(self):
        guard = require_session_access()
        session_row = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=None)
        db = _db_returning(_scalar(session_row))
        req = _request(session_id=str(session_row.id))
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is session_row

    @pytest.mark.asyncio
    async def test_session_not_found_returns_404(self):
        guard = require_session_access()
        db = _db_returning(_scalar(None))
        req = _request(session_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 404


# ── require_link_access (report share links) ────────────────────────────────


class TestRequireLinkAccess:
    @pytest.mark.asyncio
    async def test_creator_passes_without_membership_query(self):
        guard = require_link_access()
        creator_id = uuid.uuid4()
        link_row = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            created_by_id=creator_id,
        )
        db = _db_returning(_scalar(link_row))  # single query — no membership check needed
        req = _request(link_id=str(link_row.id))
        result = await guard(req, db, _user(user_id=creator_id))
        assert result is link_row

    @pytest.mark.asyncio
    async def test_project_member_passes(self):
        guard = require_link_access()
        link_row = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            created_by_id=uuid.uuid4(),  # someone else created it
        )
        db = _db_returning(
            _scalar(link_row),
            _scalar(uuid.uuid4()),  # membership row found
        )
        req = _request(link_id=str(link_row.id))
        caller = _user()
        result = await guard(req, db, caller)
        assert result is link_row

    @pytest.mark.asyncio
    async def test_non_member_non_creator_denied(self):
        guard = require_link_access()
        link_row = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            created_by_id=uuid.uuid4(),
        )
        db = _db_returning(
            _scalar(link_row),
            _scalar(None),  # no membership
        )
        req = _request(link_id=str(link_row.id))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_bypasses(self):
        guard = require_link_access()
        link_row = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            created_by_id=uuid.uuid4(),
        )
        db = _db_returning(_scalar(link_row))
        req = _request(link_id=str(link_row.id))
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is link_row

    @pytest.mark.asyncio
    async def test_link_not_found_returns_404(self):
        guard = require_link_access()
        db = _db_returning(_scalar(None))
        req = _request(link_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 404


# ── require_api_key_owner ───────────────────────────────────────────────────


class TestRequireApiKeyOwner:
    @pytest.mark.asyncio
    async def test_owner_passes(self):
        guard = require_api_key_owner()
        owner_id = uuid.uuid4()
        db = _db_returning(_scalar(owner_id))
        req = _request(key_id=str(uuid.uuid4()))
        caller = _user(user_id=owner_id)
        result = await guard(req, db, caller)
        assert result is caller

    @pytest.mark.asyncio
    async def test_non_owner_denied(self):
        guard = require_api_key_owner()
        db = _db_returning(_scalar(uuid.uuid4()))  # owner is someone else
        req = _request(key_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_bypasses_ownership(self):
        guard = require_api_key_owner()
        db = _db_returning(_scalar(uuid.uuid4()))  # key belongs to some other user
        req = _request(key_id=str(uuid.uuid4()))
        admin = _user(role=UserRole.ADMIN)
        result = await guard(req, db, admin)
        assert result is admin

    @pytest.mark.asyncio
    async def test_key_not_found_returns_404(self):
        guard = require_api_key_owner()
        db = _db_returning(_scalar(None))
        req = _request(key_id=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await guard(req, db, _user())
        assert exc.value.status_code == 404
