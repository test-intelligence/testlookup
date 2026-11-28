"""A Confluence knowledge source reads only the spaces an ADMIN approved for its project.

Security review 2026-11-20, TL-01. Confluence is one service account for the
whole deployment (with a fallback to the Jira account). Any QA engineer of a
project could add a ``confluence_page`` source naming any page id, and the
sync copied that page into the project's knowledge base: project membership
decided who may add a source, and nothing decided which Confluence content
the project may copy.

The rule:

* an ADMIN states the approved spaces: a ``default`` list for every project and
  a per-project list that replaces it (``knowledge_source_service``, AppSetting
  ``knowledge_rag.confluence_space_scope``). Nothing is approved until then;
* on create, a project with no approved space gets 422 and no row;
* at sync, the approved spaces are read again and handed to the connector; a
  project with none is not fetched at all;
* the connector uses the page only when Confluence says it is the page asked
  for, in an approved space. ``*`` is the explicit "any space" exception.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.models.schemas import KnowledgeConfluenceSpaceScopeUpdate
from app.services import knowledge_source_service as sources
from app.services import knowledge_sync_service as sync
from app.services.connectors import confluence_connector
from app.services.connectors.base import ConnectorFetchError
from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector

pytestmark = pytest.mark.regression

PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()
PAGE_URL = "https://wiki.example.test/wiki/spaces/QA/pages/12345/Plan"
SECRET_TEXT = "Unannounced acquisition plan"


# ── the stored scope ─────────────────────────────────────────────────────────


def _db_with_setting(value: object) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None if value is None else (
        value if isinstance(value, str) else json.dumps(value)
    )
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "not json", "[]", {"default": "QA"}, {"projects": ["x"]}])
async def test_nothing_is_approved_until_an_admin_approves_it(stored):
    assert await sources.confluence_spaces_for_project(_db_with_setting(stored), PROJECT) == frozenset()


@pytest.mark.asyncio
async def test_a_projects_own_entry_replaces_the_default_also_when_empty():
    stored = {"default": ["qa", "DOCS"], "projects": {str(PROJECT): ["team"], str(OTHER_PROJECT): []}}

    assert await sources.confluence_spaces_for_project(_db_with_setting(stored), PROJECT) == {"TEAM"}
    assert await sources.confluence_spaces_for_project(_db_with_setting(stored), OTHER_PROJECT) == frozenset()
    assert await sources.confluence_spaces_for_project(_db_with_setting(stored), uuid.uuid4()) == {"QA", "DOCS"}


def test_the_update_payload_accepts_space_keys_and_refuses_anything_else():
    update = KnowledgeConfluenceSpaceScopeUpdate(default=[" qa ", "~jdoe", "*", ""], projects={PROJECT: ["Docs"]})
    assert update.default == ["*", "QA", "~JDOE"]
    assert update.projects == {PROJECT: ["DOCS"]}

    for bad in ["QA/../X", "a b", "QA?x=1", "**"]:
        with pytest.raises(ValidationError):
            KnowledgeConfluenceSpaceScopeUpdate(default=[bad])
    with pytest.raises(ValidationError):
        KnowledgeConfluenceSpaceScopeUpdate(projects={"not-a-project-id": ["QA"]})


# ── create ───────────────────────────────────────────────────────────────────


async def _create(allowed: frozenset[str]):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=PROJECT))
    result = MagicMock()
    result.scalar.return_value = 0
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    payload = {"source_type": "confluence_page", "title": "plan", "canonical_url": PAGE_URL, "external_id": "12345"}
    with patch.object(sources, "require_rag_enabled_async", AsyncMock()), \
         patch.object(sources, "_check_project_access", AsyncMock()), \
         patch.object(sources, "confluence_spaces_for_project", AsyncMock(return_value=allowed)) as asked:
        try:
            return await sources.create_source(db, project_id=PROJECT, payload=payload, user=user), db, asked
        except HTTPException as refused:
            return refused, db, asked


@pytest.mark.asyncio
async def test_create_refuses_a_confluence_page_when_the_project_has_no_approved_space():
    refused, db, asked = await _create(frozenset())

    assert isinstance(refused, HTTPException) and refused.status_code == 422
    assert "No Confluence space is approved" in refused.detail
    db.add.assert_not_called()
    assert asked.await_args.args[1] == PROJECT


@pytest.mark.asyncio
async def test_create_accepts_a_confluence_page_when_a_space_is_approved():
    source, db, _ = await _create(frozenset({"QA"}))

    assert source.source_type == "confluence_page"
    db.add.assert_called()


# ── sync ─────────────────────────────────────────────────────────────────────


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=PROJECT, source_type="confluence_page",
        canonical_url=PAGE_URL, external_id="12345", content_hash="h", sync_status="synced",
        sync_error=None, last_synced_at=None, storage_path=None, created_at=None, title="plan",
    )


async def _run_sync(allowed: frozenset[str], *, fetch_error: Exception | None = None):
    from app.services import knowledge_chunking_service as chunking

    conn = MagicMock()
    conn.fetch_content = AsyncMock(
        side_effect=fetch_error or ConnectorFetchError("stop after the fetch", retryable=False)
    )
    db = AsyncMock()
    db.add = MagicMock()
    source = _source()
    with patch.object(sync.settings, "AI_OFFLINE_MODE", False), \
         patch.object(sync, "get_connector", MagicMock(return_value=conn)), \
         patch.object(sync, "bind_to_deployment", AsyncMock(return_value=conn)), \
         patch.object(chunking, "retire_chunks_for_source", AsyncMock(return_value=4)) as retire, \
         patch.object(sources, "confluence_spaces_for_project", AsyncMock(return_value=allowed)) as asked:
        result = await sync.run_sync(db, source, trigger="scheduled")
    source.retired_with = retire
    return result, source, conn, asked


@pytest.mark.asyncio
async def test_sync_fetches_nothing_for_a_project_with_no_approved_space():
    """Also the row stored before the rule, and the space an ADMIN has since removed."""
    result, source, conn, asked = await _run_sync(frozenset())

    conn.fetch_content.assert_not_awaited()
    assert result["status"] == "failed"
    assert "No Confluence space is approved" in source.sync_error
    assert asked.await_args.args[1] == PROJECT


@pytest.mark.asyncio
async def test_sync_hands_the_connector_the_projects_approved_spaces():
    _result, source, conn, _ = await _run_sync(frozenset({"QA", "DOCS"}))

    conn.fetch_content.assert_awaited_once()
    assert conn.fetch_content.await_args.kwargs == {"allowed_space_keys": frozenset({"QA", "DOCS"})}
    assert source.sync_error == "stop after the fetch"
    # An ordinary fetch failure (an outage) leaves the indexed content alone.
    source.retired_with.assert_not_awaited()
    assert source.content_hash == "h"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["before-the-fetch", "in-the-connector"])
async def test_a_scope_refusal_retires_what_an_earlier_sync_indexed(where):
    """Content imported before the rule, or before a space was removed, stops being retrieved."""
    from app.services.connectors.base import ConnectorScopeRefused

    if where == "before-the-fetch":
        result, source, _conn, _ = await _run_sync(frozenset())
    else:
        result, source, _conn, _ = await _run_sync(
            frozenset({"QA"}), fetch_error=ConnectorScopeRefused("the page is in another space"),
        )

    assert result["status"] == "failed"
    source.retired_with.assert_awaited_once()
    assert source.retired_with.await_args.args[1] == source.id
    # Cleared: a later, allowed sync must index again, not skip as unchanged.
    assert source.content_hash is None


# ── the connector ────────────────────────────────────────────────────────────


async def _fetch(page: dict, **kwargs):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=page)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(settings, "CONFLUENCE_DOMAIN", "wiki.example.test"), \
         patch.object(settings, "CONFLUENCE_EMAIL", "bot@example.test"), \
         patch.object(settings, "CONFLUENCE_API_TOKEN", "not-a-real-token"), \
         patch.object(confluence_connector, "get_integration_http_client", return_value=client):
        try:
            return await ConfluenceKnowledgeConnector().fetch_content(PAGE_URL, "12345", **kwargs), seen
        except ConnectorFetchError as refused:
            return refused, seen


def _page(**overrides) -> dict:
    page = {
        "id": "12345", "title": "Plan", "space": {"key": "SECRET"},
        "body": {"storage": {"value": f"<p>{SECRET_TEXT}</p>"}}, "version": {"number": 3},
    }
    page.update(overrides)
    return page


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"allowed_space_keys": None}, {"allowed_space_keys": frozenset()}])
async def test_the_connector_asks_confluence_nothing_without_an_approved_space(kwargs):
    refused, seen = await _fetch(_page(), **kwargs)

    assert isinstance(refused, ConnectorFetchError)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page",
    [
        _page(),                                   # a space nobody approved
        _page(space=None),                         # no space in the answer
        _page(space={"key": ""}),
        _page(id="99999", space={"key": "QA"}),    # not the page that was asked for
        _page(id=None, space={"key": "QA"}),
    ],
    ids=["other-space", "no-space", "blank-space", "other-page", "no-id"],
)
async def test_a_page_outside_the_approved_spaces_is_not_imported(page):
    refused, _seen = await _fetch(page, allowed_space_keys=frozenset({"QA", "DOCS"}))

    assert isinstance(refused, ConnectorFetchError)
    assert "Nothing was imported" in str(refused)
    assert SECRET_TEXT not in str(refused) and "SECRET" not in str(refused)
    assert refused.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("space", "allowed"),
    [("QA", {"QA"}), ("qa", {"QA"}), ("SECRET", {"*"}), ("~jdoe", {"~JDOE", "QA"})],
)
async def test_a_page_in_an_approved_space_is_imported(space, allowed):
    content, seen = await _fetch(_page(space={"key": space}), allowed_space_keys=frozenset(allowed))

    assert SECRET_TEXT in content.raw_text
    assert [r.url.path for r in seen] == ["/wiki/rest/api/content/12345"]
    assert content.metadata["space_key"] == space
