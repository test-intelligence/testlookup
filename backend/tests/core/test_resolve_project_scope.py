"""Unit tests for app.core.deps.resolve_project_scope — tenant isolation."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# app.core.deps pulls in python-jose and asyncpg at import time. Both are
# available in the Docker test image but may be absent from a bare local
# checkout, so we skip cleanly when they are.
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402

from app.core.deps import resolve_project_scope  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402


def _user(role: UserRole) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role)


@pytest.mark.asyncio
async def test_admin_no_project_returns_unrestricted():
    db = AsyncMock()
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)):
        project_id, allowed = await resolve_project_scope(db, _user(UserRole.ADMIN), None)
    assert project_id is None
    assert allowed is None


@pytest.mark.asyncio
async def test_admin_with_project_pins_to_project():
    db = AsyncMock()
    pid = uuid.uuid4()
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)):
        project_id, allowed = await resolve_project_scope(db, _user(UserRole.ADMIN), str(pid))
    assert project_id == pid
    assert allowed is None


@pytest.mark.asyncio
async def test_non_admin_no_project_fans_out_to_memberships():
    db = AsyncMock()
    member_ids = {uuid.uuid4(), uuid.uuid4()}
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=member_ids)):
        project_id, allowed = await resolve_project_scope(db, _user(UserRole.QA_ENGINEER), None)
    assert project_id is None
    assert allowed == member_ids


@pytest.mark.asyncio
async def test_non_admin_with_allowed_project_pins_to_that_project():
    db = AsyncMock()
    pid = uuid.uuid4()
    other = uuid.uuid4()
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={pid, other})):
        project_id, allowed = await resolve_project_scope(db, _user(UserRole.QA_ENGINEER), str(pid))
    assert project_id == pid
    assert allowed is None  # pinning clears the fan-out set


@pytest.mark.asyncio
async def test_non_admin_with_forbidden_project_raises_403():
    """Core tenant isolation: non-member cannot query another project's data."""
    db = AsyncMock()
    pid_theirs = uuid.uuid4()
    pid_forbidden = uuid.uuid4()
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={pid_theirs})):
        with pytest.raises(HTTPException) as exc_info:
            await resolve_project_scope(db, _user(UserRole.QA_ENGINEER), str(pid_forbidden))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_with_zero_memberships_returns_empty_set():
    db = AsyncMock()
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=set())):
        project_id, allowed = await resolve_project_scope(db, _user(UserRole.VIEWER), None)
    assert project_id is None
    assert allowed == set()


@pytest.mark.asyncio
async def test_invalid_project_id_string_raises_400():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await resolve_project_scope(db, _user(UserRole.ADMIN), "not-a-uuid")
    assert exc_info.value.status_code == 400
