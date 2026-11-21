"""Both URL knowledge source types obey the offline switch and the domain allow-list.

``internal_url`` and ``external_url`` are labels the caller picks; both are
served by the same connector, and that connector only ever fetches public
addresses. So the two types get the same gates:

* offline mode (``AI_OFFLINE_MODE``): the sync of either type is skipped
  before a connector is resolved;
* the ADMIN domain allow-list, when it is not empty:
  - create refuses a URL of either type whose host is not on the list (422);
  - the sync passes the list to the connector, which refuses the stored URL
    when its host is not on the list (rows stored before the rule, or a list
    tightened later) and refuses every redirect target that is not on the
    list, before that target is requested.

An empty allow-list stays permissive, and a fetch that stays on allowed
domains still works.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import knowledge_source_service as source_svc  # noqa: E402
from app.services import knowledge_sync_service as sync_svc  # noqa: E402
from app.services.connectors.base import ConnectorFetchError  # noqa: E402
from app.services.connectors.url_connector import URLConnector  # noqa: E402

pytestmark = pytest.mark.regression

_URL_TYPES = ["internal_url", "external_url"]
_ALLOWED = ["docs.example.test"]
_PROJECT = uuid.uuid4()


# ── offline mode ───────────────────────────────────────────────────────────


def _source(source_type: str, url: str = "https://docs.example.test/page") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=_PROJECT, source_type=source_type,
        canonical_url=url, external_id=None, title="t",
        content_hash="h", sync_status="synced", sync_error=None,
        last_synced_at=None, storage_path=None, created_at=None,
    )


@pytest.mark.parametrize("source_type", _URL_TYPES)
def test_both_url_types_count_as_external_fetches(source_type):
    assert source_type in sync_svc._EXTERNAL_FETCH_TYPES


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", _URL_TYPES)
async def test_offline_mode_skips_the_sync_without_resolving_a_connector(source_type):
    db = AsyncMock()
    get_connector = MagicMock(side_effect=AssertionError("must not fetch in offline mode"))
    with patch.object(settings, "AI_OFFLINE_MODE", True), \
         patch.object(sync_svc, "get_connector", get_connector):
        result = await sync_svc.run_sync(db, _source(source_type), trigger="manual")

    assert result["status"] == "skipped"
    assert result["reason"] == "offline_mode"
    get_connector.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", _URL_TYPES)
async def test_online_sync_calls_the_connector_with_the_allow_list(source_type):
    connector = MagicMock()
    connector.fetch_content = AsyncMock(side_effect=ConnectorFetchError("stop here", retryable=False))
    db = AsyncMock()
    db.add = MagicMock()
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(sync_svc, "get_connector", MagicMock(return_value=connector)), \
         patch.object(source_svc, "get_domain_allowlist", AsyncMock(return_value=list(_ALLOWED))):
        result = await sync_svc.run_sync(db, _source(source_type), trigger="manual")

    assert result["status"] == "failed"  # the stub stopped it; the gate let it through
    connector.fetch_content.assert_awaited_once()
    assert connector.fetch_content.await_args.kwargs["allowed_domains"] == _ALLOWED


@pytest.mark.asyncio
async def test_other_source_types_are_not_handed_an_allow_list():
    connector = MagicMock()
    connector.fetch_content = AsyncMock(side_effect=ConnectorFetchError("stop here", retryable=False))
    db = AsyncMock()
    db.add = MagicMock()
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(sync_svc, "get_connector", MagicMock(return_value=connector)), \
         patch.object(sync_svc, "bind_to_deployment", AsyncMock(return_value=connector)), \
         patch.object(source_svc, "confluence_spaces_for_project", AsyncMock(return_value=["DOCS"])):
        await sync_svc.run_sync(db, _source("confluence_page"), trigger="manual")

    # A Confluence page is held to its project's approved spaces instead
    # (test_confluence_knowledge_sources_stay_in_approved_spaces.py).
    assert connector.fetch_content.await_args.kwargs["allowed_space_keys"] == ["DOCS"]
    assert "allowed_domains" not in connector.fetch_content.await_args.kwargs


# ── create ─────────────────────────────────────────────────────────────────


def _db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=_PROJECT))
    result = MagicMock()
    result.scalar.return_value = 0  # source count
    result.scalar_one_or_none.return_value = None  # no duplicate
    db.execute = AsyncMock(return_value=result)
    return db


async def _create(db, source_type: str, url: str, allowlist: list[str]):
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    with patch.object(source_svc, "require_rag_enabled_async", AsyncMock()), \
         patch.object(source_svc, "_check_project_access", AsyncMock()), \
         patch.object(source_svc, "get_domain_allowlist", AsyncMock(return_value=allowlist)):
        return await source_svc.create_source(
            db,
            project_id=_PROJECT,
            payload={"source_type": source_type, "title": "doc", "canonical_url": url},
            user=user,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", _URL_TYPES)
async def test_create_refuses_a_domain_that_is_not_on_the_allow_list(source_type):
    db = _db()
    with pytest.raises(HTTPException) as refused:
        await _create(db, source_type, "https://elsewhere.example.test/page", list(_ALLOWED))
    assert refused.value.status_code == 422
    assert "elsewhere.example.test" in refused.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", _URL_TYPES)
@pytest.mark.parametrize(
    "url, allowlist",
    [
        pytest.param("https://docs.example.test/page", list(_ALLOWED), id="domain on the list"),
        pytest.param("https://team.docs.example.test/page", list(_ALLOWED), id="subdomain of a listed domain"),
        pytest.param("https://elsewhere.example.test/page", [], id="empty list is permissive"),
    ],
)
async def test_create_accepts_an_allowed_domain(source_type, url, allowlist):
    db = _db()
    source = await _create(db, source_type, url, allowlist)
    assert source.canonical_url == url
    assert source.source_type == source_type
    db.add.assert_called_once()


# ── fetch: the stored URL and every redirect target ────────────────────────


class _FakeResp:
    def __init__(self, *, status_code=200, headers=None, text="allowed page text"):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/plain"}
        self.encoding = "utf-8"
        self._body = text.encode("utf-8")
        self.url = "https://docs.example.test/final"

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self._body


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


def _client_returning(responses):
    requested: list[str] = []
    queue = list(responses)

    class _Client:
        def stream(self, method, url, headers=None, **kwargs):
            requested.append(url)
            return _StreamContext(queue.pop(0))

    return _Client(), requested


async def _fetch(url: str, responses, allowed_domains):
    client, requested = _client_returning(responses)
    # Every host here counts as public: the allow-list is the gate under test.
    with patch("app.services.connectors.url_connector.get_public_http_client", return_value=client), \
         patch("app.services.connectors.url_connector.is_safe_public_url", lambda _u: (True, "")):
        try:
            return await URLConnector().fetch_content(url, allowed_domains=allowed_domains), requested
        except ConnectorFetchError as exc:
            return exc, requested


@pytest.mark.asyncio
async def test_a_redirect_to_a_domain_off_the_list_is_refused_before_it_is_requested():
    redirect = _FakeResp(status_code=302, headers={"location": "https://elsewhere.example.test/collect?x=1"})
    outcome, requested = await _fetch("https://docs.example.test/start", [redirect], list(_ALLOWED))

    assert isinstance(outcome, ConnectorFetchError)
    assert outcome.retryable is False
    assert "elsewhere.example.test" in str(outcome)
    assert "allow-list" in str(outcome)
    assert requested == ["https://docs.example.test/start"]


@pytest.mark.asyncio
async def test_a_relative_redirect_stays_on_the_allowed_domain_and_is_followed():
    redirect = _FakeResp(status_code=302, headers={"location": "/moved"})
    final = _FakeResp()
    outcome, requested = await _fetch("https://docs.example.test/start", [redirect, final], list(_ALLOWED))

    assert outcome.raw_text == "allowed page text"
    assert requested == ["https://docs.example.test/start", "https://docs.example.test/moved"]


@pytest.mark.asyncio
async def test_a_stored_url_off_the_list_is_refused_without_any_request():
    outcome, requested = await _fetch("https://elsewhere.example.test/page", [_FakeResp()], list(_ALLOWED))

    assert isinstance(outcome, ConnectorFetchError)
    assert "elsewhere.example.test" in str(outcome)
    assert requested == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_domains", [None, []], ids=["no list passed", "empty list"])
async def test_an_empty_allow_list_lets_a_cross_domain_redirect_through(allowed_domains):
    redirect = _FakeResp(status_code=302, headers={"location": "https://elsewhere.example.test/page"})
    final = _FakeResp()
    outcome, requested = await _fetch("https://docs.example.test/start", [redirect, final], allowed_domains)

    assert outcome.raw_text == "allowed page text"
    assert requested == ["https://docs.example.test/start", "https://elsewhere.example.test/page"]
