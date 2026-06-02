"""Guard for knowledge_source_service tenant-access enforcement.

Reviewed in review/knowledge-source-service (2026-06-02): clean. This service
implements the CORRECT IDOR-prevention pattern that several sibling services
lacked (runs/releases/test_management/rag_generation all had to be fixed):
``get_source_or_404`` fetches the row then verifies ``source.project_id`` access,
and ``create_source``/``list_sources`` verify the supplied ``project_id``. These
tests pin that so a refactor can't silently regress it into a by-id IDOR.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.services import knowledge_source_service as svc  # noqa: E402


def _user():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_check_project_access_denies_foreign_project():
    with patch.object(svc, "get_accessible_project_ids",
                      AsyncMock(return_value={uuid.uuid4()})):
        with pytest.raises(HTTPException) as exc:
            await svc._check_project_access(AsyncMock(), _user(), uuid.uuid4())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_check_project_access_admin_bypass():
    # accessible None = admin → no restriction.
    with patch.object(svc, "get_accessible_project_ids", AsyncMock(return_value=None)):
        await svc._check_project_access(AsyncMock(), _user(), uuid.uuid4())  # no raise


@pytest.mark.asyncio
async def test_get_source_or_404_denies_foreign_project_source():
    """The by-id read verifies the FETCHED source's project — not just that it
    exists. This is the pattern the IDOR'd siblings were missing."""
    source = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: source))
    with patch.object(svc, "require_rag_enabled_async", AsyncMock()), \
         patch.object(svc, "get_accessible_project_ids",
                      AsyncMock(return_value={uuid.uuid4()})):  # source.project_id NOT in set
        with pytest.raises(HTTPException) as exc:
            await svc.get_source_or_404(db, source.id, _user())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_source_or_404_missing_is_404():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    with patch.object(svc, "require_rag_enabled_async", AsyncMock()), \
         patch.object(svc, "get_accessible_project_ids", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.get_source_or_404(db, uuid.uuid4(), _user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_source_denies_foreign_project_before_write():
    db = AsyncMock()
    db.add = MagicMock()
    with patch.object(svc, "require_rag_enabled_async", AsyncMock()), \
         patch.object(svc, "get_accessible_project_ids",
                      AsyncMock(return_value={uuid.uuid4()})):
        with pytest.raises(HTTPException) as exc:
            await svc.create_source(db, uuid.uuid4(), {"source_type": "external_url",
                                                       "title": "t", "canonical_url": "https://x"},
                                    _user())
    assert exc.value.status_code == 403
    db.add.assert_not_called()  # nothing staged for a forbidden project
