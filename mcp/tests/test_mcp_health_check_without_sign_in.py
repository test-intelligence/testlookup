"""``health_check`` still answers when the MCP session has no sign-in.

``/health/details`` needs a signed-in user. A stdio session started with no
credential used to read it anonymously; the tool then reported "Backend
unreachable" for a backend that was up. On a 401 the tool now reads the two
public probes (``/health/version`` and ``/health/ready``) and reports the
version and the state of the data stores. Any other failure is still
"unreachable".
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _register


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://backend.invalid/health/details")
    return httpx.HTTPStatusError(f"{code}", request=request, response=httpx.Response(code, request=request))


@pytest.fixture
def tool(monkeypatch):
    import client
    from tools import auth

    state = {"details": None, "public": None, "public_calls": 0}

    async def _get(path, params=None):
        assert str(path) == "/health/details"
        if isinstance(state["details"], Exception):
            raise state["details"]
        return state["details"]

    async def _public_health():
        state["public_calls"] += 1
        if isinstance(state["public"], Exception):
            raise state["public"]
        return state["public"]

    monkeypatch.setattr(client, "get", _get)
    monkeypatch.setattr(client, "public_health", _public_health)
    mcp = _FakeMCP()
    auth.register(mcp)
    state["tool"] = mcp.tools["health_check"]
    return state


def test_a_signed_in_session_reads_the_detailed_route(tool):
    tool["details"] = {"status": "healthy", "version": "9.9.9", "checks": {"postgres": {"status": "ok"}}}

    result = asyncio.run(tool["tool"]())

    assert "9.9.9" in result and "postgres: ok" in result
    assert tool["public_calls"] == 0


def test_without_a_sign_in_the_public_probes_answer(tool):
    tool["details"] = _status_error(401)
    tool["public"] = {"status": "ready", "version": "9.9.9", "checks": {"redis": {"status": "ok"}}}

    result = asyncio.run(tool["tool"]())

    assert "unreachable" not in result.lower()
    assert "9.9.9" in result and "redis: ok" in result
    assert tool["public_calls"] == 1


@pytest.mark.parametrize("code", [403, 500, 503])
def test_another_failure_is_still_unreachable(tool, code):
    tool["details"] = _status_error(code)

    result = asyncio.run(tool["tool"]())

    assert result.startswith("Backend unreachable")
    assert tool["public_calls"] == 0


def test_a_backend_that_is_down_is_unreachable_after_the_fallback_too(tool):
    tool["details"] = _status_error(401)
    tool["public"] = httpx.ConnectError("refused")

    assert asyncio.run(tool["tool"]()).startswith("Backend unreachable")


def test_the_public_read_takes_a_503_body_and_sends_no_credential(monkeypatch):
    import client

    seen = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/health/version":
            return httpx.Response(200, json={"version": "9.9.9", "status": "ok"})
        return httpx.Response(503, json={"status": "not_ready", "checks": {"mongo": {"status": "error"}}})

    fake = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://backend.invalid")
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    merged = asyncio.run(client.public_health())

    assert merged["version"] == "9.9.9" and merged["status"] == "not_ready"
    assert merged["checks"] == {"mongo": {"status": "error"}}
    assert seen == [("/health/version", None), ("/health/ready", None)]
