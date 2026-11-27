"""Backend diagnostics and outbound tool correlation stay per call."""
from __future__ import annotations

import asyncio
import contextvars
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client
from telemetry_context import operation_context, outgoing_operation_headers

TRACE = "00-11111111111111111111111111111111-2222222222222222-01"
OTHER_TRACE = "00-33333333333333333333333333333333-4444444444444444-01"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def status_error(status=403, request_id=None, *, text=None):
    request = httpx.Request("GET", "http://fixture/api/v1/projects")
    response = httpx.Response(status, request=request, headers={"X-Request-ID": request_id} if request_id is not None else {}, **({"text": text} if text is not None else {"json": {"detail": "Denied"}}))
    return httpx.HTTPStatusError("synthetic refusal", request=request, response=response)


@pytest.mark.parametrize("request_id", ["a", "a" * 128, "backend:job_42.req-7"])
def test_structured_and_human_errors_keep_valid_response_id(request_id):
    error = status_error(request_id=request_id)
    payload = client.error_payload(error)
    assert payload == {"ok": False, "error": "synthetic refusal", "status_code": 403, "detail": "Denied", "request_id": request_id}
    assert client.error_text(error) == f"HTTP 403: Denied [request_id={request_id}]"


@pytest.mark.parametrize("request_id", [None, "", " padded", "trailing ", "a" * 129, "line\nnext", "\x1b[31mred", "a,b", "[link=x]", "https://fixture"])
def test_invalid_or_missing_response_id_keeps_previous_error_contract(request_id):
    error = status_error(request_id=request_id)
    assert "request_id" not in client.error_payload(error)
    assert client.error_text(error) == "HTTP 403: Denied"


def test_duplicate_headers_and_non_json_error_handling():
    error = status_error(502, "proxy-request", text="upstream unavailable")
    assert client.error_payload(error)["request_id"] == "proxy-request"
    assert client.error_text(error) == "HTTP 502: upstream unavailable [request_id=proxy-request]"
    error.response.headers = httpx.Headers([("X-Request-ID", "one"), ("X-Request-ID", "two")])
    assert "request_id" not in client.error_payload(error)


def test_no_response_and_plain_errors_do_not_inherit_active_or_previous_id():
    with operation_context("local-operation", TRACE):
        client.error_payload(status_error(request_id="prior-response"))
        for error in (httpx.ReadTimeout(""), httpx.ConnectError("synthetic unavailable"), ValueError("Invalid input")):
            assert "request_id" not in client.error_payload(error)
            assert "request_id=" not in client.error_text(error)
    assert client.error_payload(ValueError("Invalid input")) == {"ok": False, "error": "Invalid input"}


@pytest.mark.parametrize("verb", ["get", "post", "patch", "put", "delete"])
def test_real_verbs_preserve_credentials_and_operation_headers(monkeypatch, verb):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(403, headers={"X-Request-ID": "backend-final"}, json={"detail": "Denied"})

    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
            monkeypatch.setattr(client, "_stdio_access_token", "unrelated-stdio-token")
            with operation_context("local-operation", TRACE) as operation:
                with pytest.raises(httpx.HTTPStatusError) as caught:
                    kwargs = {"extra_headers": {"Authorization": "other-token", "authorization": "lowercase-other-token", "X-API-Key": "unrelated-key", "X-Request-ID": "other-operation", "x-request-id": "lowercase-operation", "traceparent": "other-trace", "X-Extra": "preserved"}} if verb == "post" else {}
                    await getattr(client, verb)(client.path("/api/v1/projects"), **kwargs)
                assert client.error_payload(caught.value)["request_id"] == "backend-final"
                assert operation.backend_failed is True
            assert client._stdio_access_token == "unrelated-stdio-token"

    asyncio.run(exercise())
    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer caller-token"
    assert seen[0].headers["X-Request-ID"] == "local-operation"
    assert seen[0].headers["traceparent"] == TRACE
    assert "baggage" not in seen[0].headers and "tracestate" not in seen[0].headers
    if verb == "post":
        assert seen[0].headers["X-Extra"] == "preserved"
        assert "X-API-Key" not in seen[0].headers
    assert outgoing_operation_headers() == {}


@pytest.mark.parametrize("final_id", ["retry-final", None])
@pytest.mark.parametrize("verb", ["get", "post", "patch", "put", "delete"])
def test_stdio_auth_retry_reports_only_final_response_id(monkeypatch, final_id, verb):
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401, headers={"X-Request-ID": "expired-response"}, json={"detail": "Expired"}) if len(seen) == 1 else httpx.Response(403, headers={"X-Request-ID": final_id} if final_id else {}, json={"detail": "Denied"})

    async def exercise():
        async def authenticate():
            client._stdio_access_token = "new-token"
            return "new-token"

        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: None)
            monkeypatch.setattr(client, "_stdio_access_token", "old-token")
            monkeypatch.setattr(client.settings, "username", "synthetic-user")
            monkeypatch.setattr(client, "authenticate", authenticate)
            with operation_context("retry-operation") as operation:
                with pytest.raises(httpx.HTTPStatusError) as caught:
                    await getattr(client, verb)(client.path("/api/v1/projects"))
                payload = client.error_payload(caught.value)
                assert payload.get("request_id") == final_id
                assert "expired-response" not in client.error_text(caught.value)
                assert operation.backend_failed is True

    asyncio.run(exercise())
    assert seen == ["Bearer old-token", "Bearer new-token"]


def test_recovered_401_does_not_mark_the_operation_failed(monkeypatch):
    count = [0]

    def handler(request):
        count[0] += 1
        return httpx.Response(401 if count[0] == 1 else 200, json={"items": []})

    async def exercise():
        async def authenticate():
            client._stdio_access_token = "new-token"
            return "new-token"

        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: None)
            monkeypatch.setattr(client, "_stdio_access_token", "old-token")
            monkeypatch.setattr(client.settings, "username", "synthetic-user")
            monkeypatch.setattr(client, "authenticate", authenticate)
            with operation_context("retry-operation") as operation:
                assert await client.get(client.path("/api/v1/projects")) == {"items": []}
                assert operation.backend_failed is False
                assert operation.backend_error_seen is False

    asyncio.run(exercise())


def test_interleaved_network_callers_keep_ids_trace_headers_and_auth_isolated(monkeypatch):
    token = contextvars.ContextVar("synthetic-caller")

    async def exercise():
        arrived = asyncio.Event()
        seen = []

        async def handler(request):
            seen.append((request.headers["Authorization"], request.headers["X-Request-ID"], request.headers.get("traceparent")))
            if len(seen) == 2:
                arrived.set()
            await asyncio.wait_for(arrived.wait(), 2)
            return httpx.Response(403, headers={"X-Request-ID": request.headers["X-Request-ID"] + ".backend"}, json={"detail": "Denied"})

        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", token.get)
            monkeypatch.setattr(client, "_stdio_access_token", "configured-stdio-token")

            async def call(name):
                reset = token.set(name + "-token")
                try:
                    with operation_context(name + "-operation", TRACE if name == "alice" else OTHER_TRACE) as operation:
                        with pytest.raises(httpx.HTTPStatusError) as caught:
                            await client.get(client.path("/api/v1/projects"))
                        payload = client.error_payload(caught.value)
                        assert operation.backend_failed is True
                        return payload
                finally:
                    token.reset(reset)

            results = await asyncio.gather(call("alice"), call("bob"))
            assert [result["request_id"] for result in results] == ["alice-operation.backend", "bob-operation.backend"]
            assert set(seen) == {("Bearer alice-token", "alice-operation", TRACE), ("Bearer bob-token", "bob-operation", OTHER_TRACE)}
            assert client._stdio_access_token == "configured-stdio-token"
            assert outgoing_operation_headers() == {}

    asyncio.run(exercise())


def test_authenticate_and_public_health_propagate_correlation_without_credential_leak(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "new-stdio-token"})
        return httpx.Response(200, json={"status": "ready"})

    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client.settings, "username", "synthetic-user")
            monkeypatch.setattr(client.settings, "password", "synthetic-password")
            monkeypatch.setattr(client, "_stdio_access_token", None)
            with operation_context("health-operation", TRACE):
                await client.authenticate()
                assert await client.public_health() == {"status": "ready"}

    asyncio.run(exercise())
    assert len(seen) == 3
    assert all(request.headers["X-Request-ID"] == "health-operation" and request.headers["traceparent"] == TRACE for request in seen)
    assert all("Authorization" not in request.headers and "X-API-Key" not in request.headers for request in seen)


def test_local_login_tool_and_failed_health_surface_response_id(monkeypatch):
    from tools import auth

    def handler(request):
        return httpx.Response(403, headers={"X-Request-ID": "tool-refused"}, json={"detail": "Denied"})

    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: None)
            monkeypatch.setattr(client, "_stdio_access_token", None)
            monkeypatch.setattr(client.settings, "username", "")
            tools = FakeMCP()
            auth.register(tools)
            for name, args in (("login", ("synthetic-user", "synthetic-password")), ("health_check", ())):
                with operation_context(name + "-operation") as operation:
                    text = await tools.tools[name](*args)
                    assert text.endswith("[request_id=tool-refused]")
                    assert operation.backend_failed is True

    asyncio.run(exercise())


def test_successful_public_health_fallback_does_not_mark_an_error(monkeypatch):
    from tools import auth

    def handler(request):
        if request.url.path == "/health/details":
            return httpx.Response(401, headers={"X-Request-ID": "expected-auth-refusal"}, json={"detail": "Sign in required"})
        return httpx.Response(200, json={"status": "ready", "version": "synthetic"})

    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: None)
            monkeypatch.setattr(client, "_stdio_access_token", None)
            monkeypatch.setattr(client.settings, "username", "")
            tools = FakeMCP()
            auth.register(tools)
            with operation_context("health-operation") as operation:
                assert "synthetic" in await tools.tools["health_check"]()
                assert operation.backend_failed is False
                assert operation.backend_error_seen is True

    asyncio.run(exercise())


@pytest.mark.parametrize("status, request_id", [(200, "success-response"), (403, "backend-refused"), (503, "backend-unavailable"), (500, "invalid\nrequest-id")])
def test_real_sdk_normal_tool_preserves_safe_backend_diagnostic_on_the_wire(monkeypatch, status, request_id):
    sdk = pytest.importorskip("mcp.server.mcpserver", reason="requires the locked MCP 2.3 SDK")
    import anyio
    from mcp.client import Client
    from tools import projects

    def handler(request):
        body = [] if status == 200 else {"detail": "synthetic-body-must-stay-private"}
        return httpx.Response(status, headers={"X-Request-ID": request_id}, json=body)

    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setattr(client, "_get_client", lambda: http)
            monkeypatch.setattr(client, "_request_access_token", lambda: "synthetic-caller-token")
            adapter = sdk.MCPServer(name="Synthetic diagnostics fixture")
            projects.register(adapter)
            # The public legacy mode performs initialize and JSON-RPC framing
            # over the SDK's in-memory transport, including its error masking.
            with anyio.fail_after(5):
                async with Client(adapter, mode="legacy") as peer:
                    result = await peer.call_tool("list_projects", {})
                    wire = result.model_dump(mode="json", by_alias=True)
            assert wire["isError"] is (status != 200)
            text = "\n".join(item["text"] for item in wire["content"] if item["type"] == "text")
            if status == 200:
                assert text == "No active projects found."
                assert "request_id=" not in text
            else:
                assert f"TestLookup API returned HTTP {status}" in text
                if "\n" not in request_id:
                    assert f"[request_id={request_id}]" in text
                else:
                    assert "request_id=" not in text and request_id not in text
                assert "http://fixture" not in text and "synthetic-body-must-stay-private" not in text

    asyncio.run(exercise())
