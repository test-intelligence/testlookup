"""A Jira or Confluence knowledge source's id is a strict key, never free text.

The id (``external_id``, or the one found in the source's URL) goes into the
REST path of a request that carries the deployment's Atlassian credential,
and for a Jira epic into a JQL string as well. So:

* Confluence: ``external_id`` is used only when the whole value is ASCII
  digits. Jira: only when the whole value is an issue key (``ABC-123``).
* Anything else is ignored and the id is taken from the URL; when the URL has
  none either, the fetch is refused before any request is made.
* The epic child query quotes the key.
* Creating a source of one of these types with an ``external_id`` of another
  shape is refused (422). Other source types keep accepting any value.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

httpx = pytest.importorskip("httpx")

from pydantic import ValidationError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.models import schemas  # noqa: E402
from app.models.schemas import KnowledgeSourceCreate  # noqa: E402
from app.services.connectors import confluence_connector, jira_connector  # noqa: E402
from app.services.connectors.base import ConnectorFetchError  # noqa: E402
from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector  # noqa: E402
from app.services.connectors.jira_connector import JiraKnowledgeConnector  # noqa: E402

pytestmark = pytest.mark.regression

_FULLWIDTH_DIGITS = chr(0xFF11) + chr(0xFF12) + chr(0xFF13)  # digits, but not ASCII

#: Values that must never reach a request path or a JQL string.
_BAD_IDS = [
    pytest.param("../../../rest/api/user/current#", id="dot segments and fragment"),
    pytest.param("../myself", id="parent segment"),
    pytest.param("123?x=1", id="query string"),
    pytest.param("ABC-1/../../x", id="key then dot segments"),
    pytest.param("ABC-1 OR project = OTHER", id="key then JQL"),
    pytest.param("12345/child", id="digits then a path"),
    pytest.param("12345\n", id="digits then newline"),
    pytest.param("ABC-123\n", id="key then newline"),
    pytest.param("abc-123", id="lower case key"),
    pytest.param(_FULLWIDTH_DIGITS, id="non-ASCII digits"),
]

_URL_WITHOUT_ID = "https://tracker.example.test/some/page"
_JIRA_URL = "https://tracker.example.test/browse/ABC-123"
_CONFLUENCE_URL = "https://tracker.example.test/wiki/spaces/ENG/pages/12345/Title"


# ── the id the connectors will use ─────────────────────────────────────────


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_confluence_refuses_a_malformed_id_when_the_url_has_none(bad):
    with pytest.raises(ConnectorFetchError, match="Cannot extract"):
        ConfluenceKnowledgeConnector()._extract_page_id(_URL_WITHOUT_ID, bad)


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_jira_refuses_a_malformed_id_when_the_url_has_none(bad):
    with pytest.raises(ConnectorFetchError, match="Cannot extract"):
        JiraKnowledgeConnector()._extract_issue_key(_URL_WITHOUT_ID, bad)


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_a_malformed_id_is_ignored_in_favour_of_the_url(bad):
    assert ConfluenceKnowledgeConnector()._extract_page_id(_CONFLUENCE_URL, bad) == "12345"
    assert JiraKnowledgeConnector()._extract_issue_key(_JIRA_URL, bad) == "ABC-123"


def test_a_well_formed_id_is_used():
    confluence = ConfluenceKnowledgeConnector()
    assert confluence._extract_page_id(_URL_WITHOUT_ID, "12345") == "12345"
    assert confluence._extract_page_id(_CONFLUENCE_URL, None) == "12345"
    assert confluence._extract_page_id(_CONFLUENCE_URL, "678") == "678"  # the id wins over the URL

    jira = JiraKnowledgeConnector()
    assert jira._extract_issue_key(_URL_WITHOUT_ID, "ABC-123") == "ABC-123"
    assert jira._extract_issue_key(_JIRA_URL, None) == "ABC-123"
    assert jira._extract_issue_key(_JIRA_URL, "XY2-7") == "XY2-7"


# ── the requests that are sent ─────────────────────────────────────────────


def _transport(handler):
    """An httpx client on a mock transport, plus the list of requests it saw."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_record)), seen


def _jira() -> JiraKnowledgeConnector:
    return JiraKnowledgeConnector(
        {"domain": "tracker.example.test", "email": "bot@example.test", "api_token": "not-a-real-token"}
    )


def _confluence_settings():
    return (
        patch.object(settings, "CONFLUENCE_DOMAIN", "tracker.example.test"),
        patch.object(settings, "CONFLUENCE_EMAIL", "bot@example.test"),
        patch.object(settings, "CONFLUENCE_API_TOKEN", "not-a-real-token"),
    )


def _page(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "12345", "title": "Page", "space": {"key": "QA"}, "body": {"storage": {"value": "<p>text</p>"}},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _BAD_IDS)
async def test_confluence_sends_nothing_for_a_malformed_id(bad):
    client, seen = _transport(_page)
    domain, email, token = _confluence_settings()
    with domain, email, token, patch.object(confluence_connector, "get_integration_http_client", return_value=client):
        with pytest.raises(ConnectorFetchError):
            await ConfluenceKnowledgeConnector().fetch_content(_URL_WITHOUT_ID, bad, allowed_space_keys=frozenset({"QA"}))
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _BAD_IDS)
async def test_jira_sends_nothing_for_a_malformed_id(bad):
    client, seen = _transport(lambda _r: httpx.Response(200, json={"fields": {"summary": "s"}}))
    with patch.object(jira_connector, "get_integration_http_client", return_value=client):
        with pytest.raises(ConnectorFetchError):
            await _jira().fetch_content(_URL_WITHOUT_ID, bad)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", ["12345", None, "../../../rest/api/user/current#"])
async def test_confluence_request_path_is_always_the_content_path(external_id):
    client, seen = _transport(_page)
    domain, email, token = _confluence_settings()
    with domain, email, token, patch.object(confluence_connector, "get_integration_http_client", return_value=client):
        content = await ConfluenceKnowledgeConnector().fetch_content(
            _CONFLUENCE_URL, external_id, allowed_space_keys=frozenset({"QA"}),
        )

    assert [r.url.path for r in seen] == ["/wiki/rest/api/content/12345"]
    assert "text" in content.raw_text


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", ["ABC-123", None, "../myself"])
async def test_jira_request_path_is_always_the_issue_path(external_id):
    client, seen = _transport(
        lambda _r: httpx.Response(200, json={"key": "ABC-123", "fields": {"summary": "s", "issuetype": {"name": "Story"}}})
    )
    with patch.object(jira_connector, "get_integration_http_client", return_value=client):
        content = await _jira().fetch_content(_JIRA_URL, external_id)

    assert [r.url.path for r in seen] == ["/rest/api/3/issue/ABC-123"]
    assert content.title == "ABC-123: s"


@pytest.mark.asyncio
async def test_the_epic_child_query_quotes_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/issue/ABC-123":
            return httpx.Response(200, json={"key": "ABC-123", "fields": {"summary": "epic", "issuetype": {"name": "Epic"}}})
        return httpx.Response(200, json={"issues": [{"key": "ABC-124", "fields": {"summary": "child"}}]})

    client, seen = _transport(handler)
    with patch.object(jira_connector, "get_integration_http_client", return_value=client):
        content = await _jira().fetch_content(_JIRA_URL, "ABC-123")

    assert [r.url.path for r in seen] == ["/rest/api/3/issue/ABC-123", "/rest/api/3/search/jql"]
    assert seen[1].url.params["jql"] == '("Epic Link" = "ABC-123" OR parent = "ABC-123") AND project = "ABC"'
    assert "ABC-124: child" in content.raw_text


# ── create ─────────────────────────────────────────────────────────────────


def _payload(source_type: str, external_id):
    return {
        "source_type": source_type,
        "title": "t",
        "canonical_url": "https://tracker.example.test/x",
        "external_id": external_id,
    }


@pytest.mark.parametrize("source_type", ["jira_issue", "jira_epic", "confluence_page"])
@pytest.mark.parametrize("bad", _BAD_IDS)
def test_create_refuses_a_malformed_id_for_an_atlassian_source(source_type, bad):
    with pytest.raises(ValidationError) as refused:
        KnowledgeSourceCreate(**_payload(source_type, bad))
    assert "external_id" in str(refused.value)


@pytest.mark.parametrize(
    "source_type, wrong_shape",
    [("jira_issue", "12345"), ("jira_epic", "12345"), ("confluence_page", "ABC-123")],
)
def test_create_refuses_the_other_products_id_shape(source_type, wrong_shape):
    with pytest.raises(ValidationError):
        KnowledgeSourceCreate(**_payload(source_type, wrong_shape))


@pytest.mark.parametrize(
    "source_type, external_id",
    [
        ("jira_issue", "ABC-123"),
        ("jira_epic", "XY2-7"),
        ("confluence_page", "12345"),
        ("jira_issue", None),
        ("confluence_page", None),
        ("jira_issue", ""),  # empty means "not given": the id comes from the URL
        ("confluence_page", ""),
    ],
)
def test_create_accepts_a_well_formed_or_absent_id(source_type, external_id):
    assert KnowledgeSourceCreate(**_payload(source_type, external_id)).external_id == external_id


@pytest.mark.parametrize("source_type", ["internal_url", "external_url", "uploaded_document"])
@pytest.mark.parametrize("external_id", ["../myself", "123?x=1", "free text id"])
def test_create_leaves_other_source_types_alone(source_type, external_id):
    assert KnowledgeSourceCreate(**_payload(source_type, external_id)).external_id == external_id


def test_create_and_the_connectors_use_the_same_patterns():
    # The create check is only useful while it refuses what the connectors refuse.
    assert schemas.KNOWLEDGE_JIRA_ISSUE_KEY_PATTERN == jira_connector._ISSUE_KEY_PATTERN
    assert schemas.KNOWLEDGE_CONFLUENCE_PAGE_ID_PATTERN == confluence_connector._PAGE_ID_PATTERN
