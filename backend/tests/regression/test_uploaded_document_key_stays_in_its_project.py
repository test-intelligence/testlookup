"""An ``uploaded_document`` knowledge source can only point at its own project's objects.

For this source type ``canonical_url`` is the object key in the shared
knowledge bucket, where every project's files live under ``<project id>/``.
The key comes from the caller, so a source filed under project A could name an
object of project B and have B's text chunked into A's knowledge base.

The rule, checked in two places:

* on create (``knowledge_source_service.create_source``): the key must start
  with ``<the source's project id>/`` and have no ``.`` / ``..`` segment and no
  backslash, else 422 and no row. ``canonical_url`` cannot be changed by an
  update, so create is the only way in;
* at fetch time (``DocumentConnector.fetch_content``): the same check against
  the project id the caller passes, so a row stored before the rule existed is
  refused at sync, before storage is read. A fetch with no project id is
  refused too.

Other source types are not object keys and are not affected.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.services import knowledge_source_service as svc  # noqa: E402
from app.services.connectors.base import ConnectorFetchError  # noqa: E402
from app.services.connectors.document_connector import DocumentConnector  # noqa: E402

pytestmark = pytest.mark.regression

_PROJECT_A = uuid.uuid4()
_PROJECT_B = uuid.uuid4()
_KEY_TAIL = f"{uuid.uuid4()}/{'ab' * 32}.txt"

#: Keys a source in project A must not be able to use.
_FOREIGN_KEYS = [
    pytest.param(f"{_PROJECT_B}/{_KEY_TAIL}", id="another project's key"),
    pytest.param(f"{_PROJECT_A}/../{_PROJECT_B}/{_KEY_TAIL}", id="own prefix then .. into another project"),
    pytest.param(f"{_PROJECT_A}/./../{_PROJECT_B}/{_KEY_TAIL}", id="dot segments"),
    pytest.param(f"{_PROJECT_A}\\..\\{_PROJECT_B}\\{_KEY_TAIL}", id="backslashes"),
    pytest.param(f"{_PROJECT_A}/x\\..\\..\\{_PROJECT_B}/{_KEY_TAIL}", id="backslash traversal after own prefix"),
    pytest.param(f"/{_PROJECT_A}/{_KEY_TAIL}", id="leading slash"),
    pytest.param(f"{_PROJECT_A}", id="project id with no object"),
    pytest.param(f"{_PROJECT_A}/", id="project prefix only"),
    pytest.param(f"{str(_PROJECT_A)[:-1]}/{_KEY_TAIL}", id="prefix of the project id"),
    pytest.param(_KEY_TAIL, id="no project prefix"),
]


# ── create ─────────────────────────────────────────────────────────────────


def _db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=_PROJECT_A))
    # Source count, then the duplicate lookup: nothing there.
    result = MagicMock()
    result.scalar.return_value = 0
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


async def _create(db, source_type: str, canonical_url: str):
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    with patch.object(svc, "require_rag_enabled_async", AsyncMock()), \
         patch.object(svc, "_check_project_access", AsyncMock()), \
         patch.object(svc, "get_domain_allowlist", AsyncMock(return_value=[])), \
         patch.object(svc, "_validate_url_domain", MagicMock()):
        return await svc.create_source(
            db,
            project_id=_PROJECT_A,
            payload={"source_type": source_type, "title": "doc", "canonical_url": canonical_url},
            user=user,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", _FOREIGN_KEYS)
async def test_create_refuses_a_key_outside_the_sources_project(key):
    db = _db()
    with pytest.raises(HTTPException) as refused:
        await _create(db, "uploaded_document", key)
    assert refused.value.status_code == 422
    assert str(_PROJECT_A) in refused.value.detail  # says what the key must start with
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_accepts_a_key_under_the_sources_project():
    db = _db()
    key = f"{_PROJECT_A}/{_KEY_TAIL}"
    source = await _create(db, "uploaded_document", key)
    assert source.canonical_url == key
    assert source.project_id == _PROJECT_A
    db.add.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["external_url", "internal_url", "confluence_page", "jira_issue"])
async def test_other_source_types_are_not_object_keys(source_type):
    db = _db()
    source = await _create(db, source_type, "https://docs.example.test/page")
    assert source.canonical_url == "https://docs.example.test/page"
    db.add.assert_called_once()


def test_an_update_cannot_change_the_key():
    # The create check is the only one needed while this holds. If
    # canonical_url ever becomes patchable, update_source needs the same check.
    from app.models.schemas import KnowledgeSourceUpdate

    assert "canonical_url" not in KnowledgeSourceUpdate.model_fields
    assert "source_type" not in KnowledgeSourceUpdate.model_fields
    assert "project_id" not in KnowledgeSourceUpdate.model_fields


# ── fetch (rows stored before the rule) ────────────────────────────────────


def _storage(content: bytes = b"project text") -> SimpleNamespace:
    return SimpleNamespace(get_object_content=AsyncMock(return_value=content))


@pytest.mark.asyncio
@pytest.mark.parametrize("key", _FOREIGN_KEYS)
async def test_fetch_refuses_a_stored_key_outside_the_sources_project(key):
    storage = _storage()
    with patch("app.db.storage.get_storage_provider", return_value=storage):
        with pytest.raises(ConnectorFetchError) as refused:
            await DocumentConnector().fetch_content(key, project_id=_PROJECT_A)
    assert refused.value.retryable is False
    storage.get_object_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_without_a_project_reads_nothing():
    storage = _storage()
    with patch("app.db.storage.get_storage_provider", return_value=storage):
        with pytest.raises(ConnectorFetchError):
            await DocumentConnector().fetch_content(f"{_PROJECT_A}/{_KEY_TAIL}")
    storage.get_object_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_reads_a_key_under_the_sources_project():
    storage = _storage(b"project A text")
    key = f"{_PROJECT_A}/{_KEY_TAIL}"
    with patch("app.db.storage.get_storage_provider", return_value=storage):
        content = await DocumentConnector().fetch_content(key, project_id=_PROJECT_A)
    assert content.raw_text == "project A text"
    assert storage.get_object_content.await_args.kwargs["key"] == key


def test_the_sync_passes_the_sources_project_to_the_document_connector():
    """``sync_source`` is the one caller of ``fetch_content``. For an uploaded
    document it must hand over the source's project id, or every such sync is
    refused by the connector."""
    from app.services import knowledge_sync_service

    tree = ast.parse(inspect.getsource(knowledge_sync_service))
    fetch_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fetch_content"
    ]
    assert fetch_calls, "knowledge_sync_service no longer calls fetch_content; move this check"
    with_project = [
        call for call in fetch_calls
        if any(kw.arg == "project_id" or kw.arg is None for kw in call.keywords)
    ]
    assert with_project, (
        "knowledge_sync_service must call fetch_content(..., project_id=source.project_id) "
        "for uploaded_document sources; without it the connector refuses every such sync"
    )
