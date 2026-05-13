"""Tests for stream_service.resolve_project — the small helper that lets SDK
callers configure either ``testlookup.project=<uuid>`` or
``testlookup.project=<name>`` when starting a live session."""
import uuid

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

from app.models.postgres import Project
from app.services import stream_service


class _FakeResult:
    """Minimal mimic of SQLAlchemy's Result.scalar_one_or_none()."""
    def __init__(self, value): self._value = value
    def scalar_one_or_none(self): return self._value


def _project(name: str) -> Project:
    p = Project(name=name, slug=name.lower().replace(' ', '-'))
    p.id = uuid.uuid4()
    return p


@pytest.mark.asyncio
async def test_resolves_by_uuid_when_input_is_a_uuid_string():
    target = _project("Acme")
    db = AsyncMock()
    db.get.return_value = target

    out = await stream_service.resolve_project(db, str(target.id))

    assert out is target
    db.get.assert_awaited_once_with(Project, target.id)
    # Name lookup should NOT have run — UUID matched on first try.
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_name_lookup_when_input_is_not_a_uuid():
    target = _project("GoogleSearch")
    db = AsyncMock()
    db.execute.return_value = _FakeResult(target)

    out = await stream_service.resolve_project(db, "GoogleSearch")

    assert out is target
    # No UUID attempt → db.get never called
    db.get.assert_not_called()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_name_lookup_is_case_insensitive():
    target = _project("GoogleSearch")
    db = AsyncMock()
    db.execute.return_value = _FakeResult(target)

    out = await stream_service.resolve_project(db, "googlesearch")

    assert out is target


@pytest.mark.asyncio
async def test_falls_back_to_name_when_uuid_format_but_no_match():
    """A well-formed UUID that doesn't match any project should still try
    the name path (the user might have a project literally named like a UUID).
    That edge case won't actually resolve, so we should end up at 404."""
    db = AsyncMock()
    db.get.return_value = None                # UUID didn't match
    db.execute.return_value = _FakeResult(None)  # name didn't match either

    with pytest.raises(HTTPException) as exc:
        await stream_service.resolve_project(db, str(uuid.uuid4()))

    assert exc.value.status_code == 404
    # Both lookups attempted
    db.get.assert_awaited_once()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_identifier_returns_404():
    db = AsyncMock()
    db.execute.return_value = _FakeResult(None)

    with pytest.raises(HTTPException) as exc:
        await stream_service.resolve_project(db, "NotAProject")

    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail.lower()
