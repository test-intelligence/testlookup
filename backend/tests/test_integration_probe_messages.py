"""Integration health speaks plain English (D-77).

A probe stored ``str(exc)[:300]`` as its message, so the health page showed
"[Errno -3] Temporary failure in name resolution" for the optional ChromaDB
and Ollama services a default install does not run. Each failure now maps to
one sentence; the raw exception goes to the server log only.
"""
from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.services import integration_probe_service as svc

RESOLVER_ERROR = socket.gaierror(-3, "Temporary failure in name resolution")


def _client_raising(exc: BaseException) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(side_effect=exc)
    return client


def _httpx_connect_error_from(cause: BaseException) -> httpx.ConnectError:
    try:
        try:
            raise cause
        except BaseException as inner:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution") from inner
    except httpx.ConnectError as outer:
        return outer


@pytest.mark.parametrize("exc, kind", [
    (RESOLVER_ERROR, "dns"),
    (_httpx_connect_error_from(RESOLVER_ERROR), "dns"),
    (httpx.ConnectError("All connection attempts failed"), "refused"),
    (ConnectionRefusedError(111, "Connection refused"), "refused"),
    (httpx.ReadTimeout("timed out"), "timeout"),
    (RuntimeError("certificate verify failed: self signed certificate"), "tls"),
    (RuntimeError("(535, '5.7.8 Username and Password not accepted')"), "auth"),
    (ValueError("something odd at chroma.internal.svc:8001"), "unreachable"),
])
def test_each_failure_maps_to_a_sentence(exc, kind):
    assert svc.probe_failure_kind(exc) == kind
    sentence = svc.probe_failure_sentence("x", exc)
    assert sentence == svc.PROBE_FAILURE_SENTENCES[kind]


def test_no_sentence_carries_errno_text_or_a_host():
    for sentence in svc.PROBE_FAILURE_SENTENCES.values():
        assert "Errno" not in sentence and "[" not in sentence
        assert ":" not in sentence.split(" ", 1)[0]


def test_the_raw_text_goes_to_the_log(caplog):
    with caplog.at_level("WARNING", logger="services.integration_probe"):
        svc.probe_failure_sentence("chromadb", _httpx_connect_error_from(RESOLVER_ERROR))
    assert "Temporary failure in name resolution" in caplog.text


@pytest.mark.asyncio
async def test_chromadb_down_without_errno_text():
    with patch.object(svc, "get_http_client", lambda: _client_raising(_httpx_connect_error_from(RESOLVER_ERROR))), \
            patch("app.services.storage_config_service.get_effective_storage_config",
                  AsyncMock(return_value={"chroma_host": "chromadb", "chroma_port": 8000})):
        result = await svc.probe_chromadb()
    assert result.status == "down"
    assert "Errno" not in result.message
    assert "name resolution" not in result.message
    assert "chromadb" not in result.message.lower()
    assert result.message == svc.PROBE_FAILURE_SENTENCES["dns"]


@pytest.mark.asyncio
async def test_ollama_down_without_errno_text(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama:11434")
    with patch.object(svc, "get_http_client", lambda: _client_raising(RESOLVER_ERROR)):
        result = await svc.probe_ollama()
    assert result.status == "down"
    assert "Errno" not in result.message and "ollama:11434" not in result.message


@pytest.mark.asyncio
async def test_ollama_is_skipped_when_nothing_runs_on_it(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "lmstudio")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "localai")
    client = _client_raising(AssertionError("must not probe"))
    with patch.object(svc, "get_http_client", lambda: client):
        result = await svc.probe_ollama()
    assert result.status == "skipped"
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_jira_down_without_errno_text(monkeypatch):
    config = {"jira_enabled": True, "jira_domain": "jira.internal.corp", "jira_email": "a@b.c", "jira_api_token": "t"}
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    with patch.object(svc, "get_http_client", lambda: _client_raising(_httpx_connect_error_from(RESOLVER_ERROR))):
        result = await svc.probe_jira(config)
    assert result.status == "down"
    assert "Errno" not in result.message and "jira.internal.corp" not in result.message


def test_project_probe_exception_is_a_sentence():
    target = SimpleNamespace(provider="github", project_id="p", project_name="P", resolution=None)
    with patch.object(svc, "_project_result", lambda t, status, ms, message, *a, **k: SimpleNamespace(
            status=status, message=message)):
        result = svc._exception_outcome(target, _httpx_connect_error_from(RESOLVER_ERROR), 0, base="b")
    assert result.status == "down"
    assert result.message == svc.PROBE_FAILURE_SENTENCES["dns"]
