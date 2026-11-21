"""A Jira knowledge source reads only the Jira project its TestLookup project may use.

Jira is one service account for the whole deployment. A knowledge source of
type ``jira_issue`` or ``jira_epic`` names an issue key, and the sync fetches
that issue's description, comments and (for an epic) its child issues into the
project's knowledge base. The key came from the caller with no tie to the
project, so a QA engineer in one project could pull in the text of any issue
the account can read. Filing, linking and release sync were bound to the
project's Jira key earlier; this is the same rule for knowledge sources.

The rule, in three places:

* on create (``knowledge_source_service.create_source``): the issue key must
  be inside what the caller may reach from the project
  (``defect_jira_service.JiraKeyScope``): the project's own Jira key when it
  has one; without one, the default Jira project for callers below QA lead,
  any Jira project for a QA lead or administrator. Otherwise 422 and no row.
  An update cannot change the key, so create is the only way in;
* at sync (``knowledge_sync_service.run_sync``): on a project that has a Jira
  key, a source stored earlier with a key from another Jira project is not
  fetched; the sync fails with the reason. A project without a key is not
  narrowed at sync (a QA lead may have named the other Jira project on
  purpose, and the sync cannot tell who created the source);
* an epic's child search (``JiraKnowledgeConnector``): on a project that has a
  Jira key, only children in that Jira project are read.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.services import defect_jira_service  # noqa: E402
from app.services import knowledge_source_service as sources  # noqa: E402
from app.services import knowledge_sync_service as sync  # noqa: E402
from app.services.connectors import jira_connector  # noqa: E402
from app.services.connectors.base import ConnectorFetchError, FetchedContent  # noqa: E402
from app.services.connectors.jira_connector import JiraKnowledgeConnector  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT = uuid.uuid4()
BROWSE = "https://jira.example.test/browse/"

BOUND = dict(project_key="TEAM", default_key="QA", any_key=False)
NO_KEY_ENGINEER = dict(project_key=None, default_key="QA", any_key=False)
NO_KEY_LEAD = dict(project_key=None, default_key="QA", any_key=True)


def _scope(monkeypatch, **fields) -> list:
    """``jira_key_scope`` answers one fixed scope; the refusal logic is the real one."""
    asked: list = []
    scope = defect_jira_service.JiraKeyScope(**fields)

    async def _fixed(db, project_id, user, *, cfg=None):
        asked.append((project_id, user))
        return scope

    monkeypatch.setattr(defect_jira_service, "jira_key_scope", _fixed)
    return asked


# ── create ───────────────────────────────────────────────────────────────────


def _db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=PROJECT))
    result = MagicMock()
    result.scalar.return_value = 0
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


async def _create(db, source_type: str, *, external_id: str | None = None, url: str | None = None):
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    payload = {"source_type": source_type, "title": "requirements",
               "canonical_url": url or f"{BROWSE}{external_id}"}
    if external_id is not None:
        payload["external_id"] = external_id
    with patch.object(sources, "require_rag_enabled_async", AsyncMock()), \
         patch.object(sources, "_check_project_access", AsyncMock()), \
         patch.object(sources, "get_domain_allowlist", AsyncMock(return_value=[])), \
         patch.object(sources, "confluence_spaces_for_project", AsyncMock(return_value=frozenset({"QA"}))), \
         patch.object(sources, "_validate_url_domain", MagicMock()):
        return await sources.create_source(db, project_id=PROJECT, payload=payload, user=user), user


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["jira_issue", "jira_epic"])
@pytest.mark.parametrize("how", ["external_id", "url"])
async def test_create_refuses_an_issue_outside_the_projects_jira_project(monkeypatch, source_type, how):
    _scope(monkeypatch, **BOUND)
    db = _db()
    with pytest.raises(HTTPException) as refused:
        if how == "external_id":
            await _create(db, source_type, external_id="OTHER-12")
        else:
            await _create(db, source_type, url=f"{BROWSE}OTHER-12")
    assert refused.value.status_code == 422
    assert "OTHER-12" in refused.value.detail and "TEAM" in refused.value.detail  # what, and what is allowed
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["jira_issue", "jira_epic"])
async def test_create_accepts_an_issue_in_the_projects_jira_project(monkeypatch, source_type):
    asked = _scope(monkeypatch, **BOUND)
    db = _db()
    source, user = await _create(db, source_type, external_id="TEAM-7")
    assert source.external_id == "TEAM-7"
    assert asked == [(PROJECT, user)]          # judged for this project and this caller
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_without_a_project_key_an_engineer_reads_only_the_default_jira_project(monkeypatch):
    _scope(monkeypatch, **NO_KEY_ENGINEER)
    db = _db()
    with pytest.raises(HTTPException) as refused:
        await _create(db, "jira_issue", external_id="OTHER-12")
    assert refused.value.status_code == 422
    db.add.assert_not_called()

    source, _ = await _create(db, "jira_issue", external_id="QA-3")
    assert source.external_id == "QA-3"


@pytest.mark.asyncio
async def test_without_a_project_key_a_lead_may_name_another_jira_project(monkeypatch):
    _scope(monkeypatch, **NO_KEY_LEAD)
    source, _ = await _create(_db(), "jira_epic", external_id="OTHER-12")
    assert source.external_id == "OTHER-12"


@pytest.mark.asyncio
async def test_a_jira_source_with_no_issue_key_is_refused(monkeypatch):
    _scope(monkeypatch, **NO_KEY_LEAD)
    db = _db()
    with pytest.raises(HTTPException) as refused:
        await _create(db, "jira_issue", url="https://jira.example.test/secure/Dashboard.jspa")
    assert refused.value.status_code == 422
    assert "issue key" in refused.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["external_url", "internal_url", "confluence_page"])
async def test_other_source_types_do_not_ask_for_a_jira_scope(monkeypatch, source_type):
    async def _never(*_args, **_kwargs):
        raise AssertionError("a non-Jira source asked for the Jira scope")

    monkeypatch.setattr(defect_jira_service, "jira_key_scope", _never)
    source, _ = await _create(_db(), source_type, url="https://docs.example.test/TEAM-1")
    assert source.source_type == source_type


def test_an_update_cannot_change_the_issue():
    from app.models.schemas import KnowledgeSourceUpdate

    for field in ("canonical_url", "external_id", "source_type", "project_id"):
        assert field not in KnowledgeSourceUpdate.model_fields


# ── sync (rows stored before the rule) ───────────────────────────────────────


def _source(source_type: str, issue_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=PROJECT, source_type=source_type,
        canonical_url=f"{BROWSE}{issue_key}", external_id=issue_key,
        content_hash="h", sync_status="synced", sync_error=None,
        last_synced_at=None, storage_path=None, created_at=None,
    )


def _connector() -> MagicMock:
    """A connector that records the fetch."""
    conn = MagicMock()
    # Stop right after the fetch: what was asked of the connector is the subject.
    conn.fetch_content = AsyncMock(side_effect=ConnectorFetchError("stop after the fetch", retryable=False))
    return conn


async def _run_sync(source, conn):
    db = AsyncMock()
    db.add = MagicMock()
    with patch.object(sync.settings, "AI_OFFLINE_MODE", False), \
         patch.object(sync, "get_connector", MagicMock(return_value=conn)), \
         patch.object(sync, "bind_to_deployment", AsyncMock(return_value=conn)):
        return await sync.run_sync(db, source, trigger="scheduled")


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["jira_issue", "jira_epic"])
async def test_sync_does_not_fetch_a_stored_issue_outside_the_projects_jira_project(monkeypatch, source_type):
    asked = _scope(monkeypatch, **BOUND)
    source, conn = _source(source_type, "OTHER-12"), _connector()

    result = await _run_sync(source, conn)

    conn.fetch_content.assert_not_awaited()            # nothing was asked of Jira
    assert result["status"] == "failed"
    assert "OTHER-12" in source.sync_error and "TEAM" in source.sync_error
    assert asked == [(PROJECT, None)]                  # no person: the project's key decides


@pytest.mark.asyncio
async def test_sync_fetches_an_issue_in_the_projects_jira_project_and_keeps_an_epic_inside_it(monkeypatch):
    _scope(monkeypatch, **BOUND)
    source, conn = _source("jira_epic", "TEAM-7"), _connector()

    await _run_sync(source, conn)

    conn.fetch_content.assert_awaited_once()
    assert conn.fetch_content.await_args.kwargs == {"jira_project_key": "TEAM"}
    assert source.sync_error == "stop after the fetch"


@pytest.mark.asyncio
async def test_sync_on_a_project_without_a_jira_key_is_not_narrowed(monkeypatch):
    _scope(monkeypatch, **NO_KEY_ENGINEER)
    source, conn = _source("jira_epic", "OTHER-12"), _connector()

    await _run_sync(source, conn)

    conn.fetch_content.assert_awaited_once()
    assert conn.fetch_content.await_args.kwargs == {}


# ── the epic's child search ──────────────────────────────────────────────────


class _Http:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        if "/search/" in url:
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"issues": []})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"key": "TEAM-7", "fields": {
            "summary": "Checkout", "issuetype": {"name": "Epic"}, "status": {"name": "Open"},
        }})


async def _fetch_epic(monkeypatch, **kwargs) -> str:
    http = _Http()
    monkeypatch.setattr(jira_connector, "get_http_client", lambda: http)
    connector = JiraKnowledgeConnector().with_config(
        {"domain": "jira.example.test", "email": "bot@example.test", "api_token": "not-a-real-token"},
    )
    content = await connector.fetch_content(f"{BROWSE}TEAM-7", "TEAM-7", **kwargs)
    assert isinstance(content, FetchedContent)
    (search,) = [c for c in http.calls if "/search/" in c["url"]]
    return search["params"]["jql"]


@pytest.mark.asyncio
async def test_an_epics_children_are_searched_only_in_the_projects_jira_project(monkeypatch):
    jql = await _fetch_epic(monkeypatch, jira_project_key="TEAM")
    assert jql == '("Epic Link" = "TEAM-7" OR parent = "TEAM-7") AND project = "TEAM"'


@pytest.mark.asyncio
async def test_without_a_project_key_the_child_search_stays_in_the_epics_own_jira_project(monkeypatch):
    """Security review 2026-11-20, TL-01: nobody authorised the other Jira projects.

    A TestLookup project without a Jira key is not narrowed to one key at
    sync, but an epic's children were then read from every Jira project the
    account can see. They are read from the epic's own Jira project.
    """
    jql = await _fetch_epic(monkeypatch)
    assert jql == '("Epic Link" = "TEAM-7" OR parent = "TEAM-7") AND project = "TEAM"'


# ── what Jira returned must be what was asked for (TL-01) ────────────────────


class _Answer:
    """One issue response and one child-search response."""

    def __init__(self, issue: dict, children: list[dict] | None = None) -> None:
        self.issue, self.children = issue, children or []

    async def get(self, url, headers=None, params=None, timeout=None):
        body = {"issues": self.children} if "/search/" in url else self.issue
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)


async def _fetch(monkeypatch, answer: _Answer, **kwargs) -> FetchedContent:
    monkeypatch.setattr(jira_connector, "get_http_client", lambda: answer)
    connector = JiraKnowledgeConnector().with_config(
        {"domain": "jira.example.test", "email": "bot@example.test", "api_token": "not-a-real-token"},
    )
    return await connector.fetch_content(f"{BROWSE}TEAM-7", "TEAM-7", **kwargs)


_STORY = {"summary": "Private roadmap", "issuetype": {"name": "Story"}, "status": {"name": "Open"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issue",
    [
        # Moved: Jira answers the old key with the issue under its new one.
        {"key": "SECRET-41", "fields": {**_STORY, "project": {"key": "SECRET"}}},
        # The key matches but the project Jira reports does not.
        {"key": "TEAM-7", "fields": {**_STORY, "project": {"key": "SECRET"}}},
        # Nothing ties the response to the request.
        {"fields": _STORY},
        {"key": None, "fields": _STORY},
    ],
    ids=["moved-to-another-project", "project-field-disagrees", "no-key", "null-key"],
)
@pytest.mark.parametrize("kwargs", [{}, {"jira_project_key": "TEAM"}], ids=["unkeyed", "keyed"])
async def test_a_response_that_is_not_the_requested_issue_is_not_imported(monkeypatch, issue, kwargs):
    with pytest.raises(ConnectorFetchError) as refused:
        await _fetch(monkeypatch, _Answer(issue), **kwargs)

    assert "Nothing was imported" in str(refused.value)
    assert "Private roadmap" not in str(refused.value)
    assert refused.value.retryable is False


@pytest.mark.asyncio
async def test_the_requested_issue_in_its_own_project_is_imported(monkeypatch):
    content = await _fetch(
        monkeypatch, _Answer({"key": "TEAM-7", "fields": {**_STORY, "project": {"key": "TEAM"}}}),
    )

    assert "Private roadmap" in content.raw_text


@pytest.mark.asyncio
async def test_a_child_from_another_jira_project_is_left_out_whatever_the_search_returned(monkeypatch):
    epic = {"key": "TEAM-7", "fields": {"summary": "Checkout", "issuetype": {"name": "Epic"}, "project": {"key": "TEAM"}}}
    children = [
        {"key": "TEAM-8", "fields": {"summary": "Totals off by tax"}},
        {"key": "SECRET-9", "fields": {"summary": "Acquisition plan"}},
        {"fields": {"summary": "No key at all"}},
    ]

    content = await _fetch(monkeypatch, _Answer(epic, children))

    assert "TEAM-8: Totals off by tax" in content.raw_text
    assert "Acquisition plan" not in content.raw_text
    assert "No key at all" not in content.raw_text


@pytest.mark.asyncio
async def test_a_project_key_that_is_not_key_shaped_never_reaches_the_jql(monkeypatch):
    with pytest.raises(ConnectorFetchError, match="Not a Jira project key"):
        await _fetch_epic(monkeypatch, jira_project_key='TEAM" OR project != "TEAM')
