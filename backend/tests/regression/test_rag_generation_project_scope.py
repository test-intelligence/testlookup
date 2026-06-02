"""Regression: RAG generation endpoints were a systemic cross-tenant IDOR.

Bug pinned (review/rag-generation-service, 2026-06-02):

``rag_retrieve`` / ``rag_generate`` (body ``project_id``), ``list_stale_cases``
(query ``project_id``), and the case-id endpoints ``get_case_citations`` /
``dismiss_stale`` only required a role — never the caller's access to the
target project/case. So a QA_ENGINEER could retrieve another tenant's RAG
chunks, GENERATE+persist test cases into a foreign project, list its stale
cases, and read/dismiss its generated cases. Fix: verify project access
(``resolve_project_scope``) on the project_id endpoints, and a
case→project access helper on the case-id endpoints.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import rag_generation as rg  # noqa: E402


def _deny(*_a, **_k):
    raise HTTPException(status_code=403, detail="Forbidden")


@pytest.mark.asyncio
async def test_rag_retrieve_denies_foreign_project():
    payload = SimpleNamespace(project_id=uuid.uuid4())
    with patch.object(rg, "resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await rg.rag_retrieve(
                payload=payload, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_rag_generate_denies_foreign_project_before_generating():
    payload = SimpleNamespace(project_id=uuid.uuid4())
    gen = AsyncMock(side_effect=AssertionError("must not generate for a foreign project"))
    with patch.object(rg, "resolve_project_scope", AsyncMock(side_effect=_deny)), \
         patch("app.services.rag_generation_service.grounded_generate", gen):
        with pytest.raises(HTTPException) as exc:
            await rg.rag_generate(
                payload=payload, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403
    gen.assert_not_called()


@pytest.mark.asyncio
async def test_list_stale_cases_denies_foreign_project():
    with patch.object(rg, "resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await rg.list_stale_cases(
                project_id=uuid.uuid4(), page=1, size=20, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403


# ── case → project access helper (citations / dismiss-stale) ─────────────────


@pytest.mark.asyncio
async def test_require_case_project_access_denies_foreign_case():
    case = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=case)
    with patch.object(rg, "resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await rg._require_case_project_access(db, SimpleNamespace(id=uuid.uuid4()), case.id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_case_project_access_404_when_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with patch.object(rg, "resolve_project_scope", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await rg._require_case_project_access(db, SimpleNamespace(id=uuid.uuid4()), uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_case_project_access_allows_own_case():
    case = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=case)
    scope = AsyncMock(return_value=(case.project_id, None))
    with patch.object(rg, "resolve_project_scope", scope):
        result = await rg._require_case_project_access(db, SimpleNamespace(id=uuid.uuid4()), case.id)
    assert result is case
    assert str(case.project_id) in str(scope.await_args)
