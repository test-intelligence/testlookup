"""Regression coverage for audit finding C01: remote MCP principal isolation.

Network MCP requests must carry their own TestLookup access token.  A request
must never inherit the configured stdio credential or mutate another caller's
identity through the interactive ``login`` tool.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import httpx
import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


MCP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_DIR.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from token_verifier import TestLookupTokenVerifier  # noqa: E402


def _access_token(subject: str, jti: str, exp: int = 2_000_000_000) -> str:
    return jwt.encode(
        {"sub": subject, "jti": jti, "exp": exp, "type": "access"},
        "test-only-secret-that-is-at-least-32-bytes-long",
        algorithm="HS256",
    )


def test_two_callers_forward_their_own_tokens_without_mutating_stdio_state(monkeypatch):
    """Interleaved network calls cannot read or replace the stdio token."""
    alice = _access_token("alice-id", "alice-jti")
    bob = _access_token("bob-id", "bob-jti")
    client._stdio_access_token = "configured-stdio-token"

    monkeypatch.setattr(client, "_request_access_token", lambda: alice)
    assert asyncio.run(client._auth_headers()) == {"Authorization": f"Bearer {alice}"}

    monkeypatch.setattr(client, "_request_access_token", lambda: bob)
    assert asyncio.run(client._auth_headers()) == {"Authorization": f"Bearer {bob}"}
    assert client._stdio_access_token == "configured-stdio-token"


def test_network_401_never_falls_back_to_configured_credentials(monkeypatch):
    """A rejected caller token must not silently gain the service identity."""
    caller_token = _access_token("alice-id", "alice-jti")
    request = httpx.Request("GET", "http://backend/api/v1/projects")
    response = httpx.Response(401, request=request, json={"detail": "revoked"})
    fake_http = AsyncMock()
    fake_http.get = AsyncMock(return_value=response)
    authenticate = AsyncMock(return_value="configured-service-token")

    monkeypatch.setattr(client, "_get_client", lambda: fake_http)
    monkeypatch.setattr(client, "_request_access_token", lambda: caller_token)
    monkeypatch.setattr(client, "authenticate", authenticate)

    try:
        asyncio.run(client.get("/api/v1/projects"))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 401
    else:  # pragma: no cover - assertion guard
        raise AssertionError("caller 401 unexpectedly succeeded")

    authenticate.assert_not_awaited()
    assert fake_http.get.await_count == 1


def test_network_login_cannot_switch_or_seed_process_identity(monkeypatch):
    """The login tool remains stdio-only and cannot mutate shared server state."""
    from tools import auth

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorator

    fake_mcp = FakeMCP()
    auth.register(fake_mcp)
    client._stdio_access_token = "configured-stdio-token"
    monkeypatch.setattr(client, "_request_access_token", lambda: "remote-caller-token")
    post = AsyncMock()
    monkeypatch.setattr(client, "_get_client", lambda: type("HTTP", (), {"post": post})())

    result = asyncio.run(fake_mcp.tools["login"]("other-user", "other-password"))

    assert "stdio" in result.lower()
    assert client._stdio_access_token == "configured-stdio-token"
    post.assert_not_awaited()


def test_token_verifier_binds_session_identity_to_subject_and_jti(monkeypatch):
    token = _access_token("alice-id", "alice-jti")
    monkeypatch.setattr(
        client,
        "verify_bearer_token",
        AsyncMock(
            return_value={
                "id": "alice-id",
                "username": "alice",
                "role": "QA_ENGINEER",
                "is_active": True,
            }
        ),
    )

    verified = asyncio.run(TestLookupTokenVerifier().verify_token(token))

    assert verified is not None
    assert verified.token == token
    assert verified.subject == "alice-id"
    assert verified.client_id == "alice-jti"
    assert verified.expires_at == 2_000_000_000
    assert verified.scopes == []
    assert verified.claims == {"username": "alice", "role": "QA_ENGINEER"}


def test_token_verifier_fails_closed_for_backend_or_claim_mismatch(monkeypatch):
    verifier = TestLookupTokenVerifier()
    token = _access_token("alice-id", "alice-jti")

    monkeypatch.setattr(client, "verify_bearer_token", AsyncMock(return_value=None))
    assert asyncio.run(verifier.verify_token(token)) is None

    verify = AsyncMock(
        return_value={
            "id": "bob-id",
            "username": "bob",
            "role": "VIEWER",
            "is_active": True,
        }
    )
    monkeypatch.setattr(
        client,
        "verify_bearer_token",
        verify,
    )
    assert asyncio.run(verifier.verify_token(token)) is None
    verify.assert_awaited_once_with(token)


def test_token_verifier_rejects_inactive_and_malformed_claims(monkeypatch):
    verifier = TestLookupTokenVerifier()
    token = _access_token("alice-id", "alice-jti")
    monkeypatch.setattr(
        client,
        "verify_bearer_token",
        AsyncMock(return_value={"id": "alice-id", "is_active": False}),
    )
    assert asyncio.run(verifier.verify_token(token)) is None

    monkeypatch.setattr(
        client,
        "verify_bearer_token",
        AsyncMock(return_value={"id": "alice-id", "is_active": True}),
    )
    assert asyncio.run(verifier.verify_token("not-a-jwt")) is None


def test_backend_auth_timeout_fails_closed(monkeypatch):
    request = httpx.Request("GET", "http://backend/api/v1/auth/me")
    auth_http = AsyncMock()
    auth_http.get = AsyncMock(side_effect=httpx.ReadTimeout("slow auth", request=request))
    monkeypatch.setattr(client, "_get_auth_client", lambda: auth_http)

    assert asyncio.run(client.verify_bearer_token("junk")) is None
    auth_http.get.assert_awaited_once()


def test_sse_and_message_endpoints_require_bearer_authentication():
    import server

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=server.mcp.sse_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as http:
            sse = await http.get("/sse")
            message = await http.post("/messages/?session_id=missing", json={})
        assert sse.status_code == 401
        assert message.status_code == 401
        assert sse.headers["www-authenticate"].startswith("Bearer ")

    asyncio.run(exercise())


def test_readiness_tracks_backend_auth_authority(monkeypatch):
    import server

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=server.mcp.sse_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as http:
            monkeypatch.setattr(client, "backend_ready", AsyncMock(return_value=False))
            unavailable = await http.get("/health/ready")
            monkeypatch.setattr(client, "backend_ready", AsyncMock(return_value=True))
            recovered = await http.get("/health/ready")
            alive = await http.get("/health/live")
        assert unavailable.status_code == 503
        assert recovered.status_code == 200
        assert alive.status_code == 200

    asyncio.run(exercise())


def test_two_real_sse_clients_remain_bound_to_their_presenting_tokens(monkeypatch):
    """Exercise the SDK transport, verifier, tool dispatch, and backend forwarding."""
    import uvicorn
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    import server as mcp_server

    alice = _access_token("alice-id", "alice-jti")
    bob = _access_token("bob-id", "bob-jti")
    profiles = {
        alice: {"id": "alice-id", "username": "alice", "role": "VIEWER", "is_active": True},
        bob: {"id": "bob-id", "username": "bob", "role": "QA_LEAD", "is_active": True},
    }
    forwarded: list[str] = []

    def bearer(request: Request) -> str:
        value = request.headers.get("authorization", "")
        return value.removeprefix("Bearer ")

    async def me(request: Request) -> JSONResponse:
        profile = profiles.get(bearer(request))
        return JSONResponse(profile or {"detail": "invalid"}, status_code=200 if profile else 401)

    async def projects(request: Request) -> JSONResponse:
        token = bearer(request)
        if token not in profiles:
            return JSONResponse({"detail": "invalid"}, status_code=401)
        forwarded.append(token)
        return JSONResponse(
            [
                {
                    "id": profiles[token]["id"],
                    "name": profiles[token]["username"],
                    "slug": profiles[token]["username"],
                    "created_at": "2026-09-05T00:00:00Z",
                }
            ]
        )

    backend = Starlette(
        routes=[
            Route("/api/v1/auth/me", me),
            Route("/api/v1/projects", projects),
        ]
    )

    async def exercise() -> None:
        backend_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=backend),
            base_url="http://backend",
        )
        monkeypatch.setattr(client, "_client", backend_http)
        monkeypatch.setattr(client, "_auth_client", backend_http)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        app = mcp_server.mcp.sse_app()
        runtime = uvicorn.Server(
            uvicorn.Config(app, log_level="error", lifespan="on")
        )
        serve = asyncio.create_task(runtime.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if runtime.started:
                    break
                await asyncio.sleep(0.01)
            assert runtime.started

            alice_session_ids: list[str] = []
            async with sse_client(
                f"http://127.0.0.1:{port}/sse",
                headers={"Authorization": f"Bearer {alice}"},
                on_session_created=alice_session_ids.append,
            ) as alice_streams, sse_client(
                f"http://127.0.0.1:{port}/sse",
                headers={"Authorization": f"Bearer {bob}"},
            ) as bob_streams:
                async with ClientSession(*alice_streams) as alice_session, ClientSession(
                    *bob_streams
                ) as bob_session:
                    await asyncio.gather(alice_session.initialize(), bob_session.initialize())
                    alice_result, bob_result = await asyncio.gather(
                        alice_session.call_tool("list_projects"),
                        bob_session.call_tool("list_projects"),
                    )
                    assert alice_result.isError is not True
                    assert bob_result.isError is not True

                    # Even a valid Bob token cannot submit into Alice's session.
                    assert alice_session_ids
                    async with httpx.AsyncClient() as raw_http:
                        takeover = await raw_http.post(
                            f"http://127.0.0.1:{port}/messages/",
                            params={"session_id": alice_session_ids[0]},
                            headers={"Authorization": f"Bearer {bob}"},
                            json={"jsonrpc": "2.0", "method": "ping", "id": 99},
                        )
                    assert takeover.status_code == 404

            assert Counter(forwarded) == Counter((alice, bob))
        finally:
            runtime.should_exit = True
            await serve
            await backend_http.aclose()
            client._client = None
            client._auth_client = None

    asyncio.run(exercise())


def test_real_stdio_process_keeps_configured_login_compatibility():
    """Transport auth must not gate the local single-client stdio workflow."""
    import uvicorn
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    forwarded: list[str] = []

    async def login(request: Request) -> JSONResponse:
        form = parse_qs((await request.body()).decode("utf-8"))
        if form.get("username") != ["stdio-user"] or form.get("password") != ["stdio-pass"]:
            return JSONResponse({"detail": "invalid"}, status_code=401)
        return JSONResponse({"access_token": "stdio-access-token"})

    async def projects(request: Request) -> JSONResponse:
        forwarded.append(request.headers.get("authorization", ""))
        return JSONResponse([])

    backend = Starlette(
        routes=[
            Route("/api/v1/auth/login", login, methods=["POST"]),
            Route("/api/v1/projects", projects),
        ]
    )

    async def exercise() -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        runtime = uvicorn.Server(uvicorn.Config(backend, log_level="error", lifespan="off"))
        serve = asyncio.create_task(runtime.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if runtime.started:
                    break
                await asyncio.sleep(0.01)
            assert runtime.started

            env = os.environ.copy()
            env.update(
                {
                    "TESTLOOKUP_API_URL": f"http://127.0.0.1:{port}",
                    "TESTLOOKUP_USERNAME": "stdio-user",
                    "TESTLOOKUP_PASSWORD": "stdio-pass",
                }
            )
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(MCP_DIR / "server.py")],
                cwd=MCP_DIR,
                env=env,
            )
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result = await session.call_tool("list_projects")
                    assert result.isError is not True
            assert forwarded == ["Bearer stdio-access-token"]
        finally:
            runtime.should_exit = True
            await serve

    asyncio.run(exercise())


def test_network_deployments_do_not_inject_shared_backend_credentials():
    paths = [
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "docker-compose.release.yml",
        REPO_ROOT / "k8s" / "base" / "mcp-deployment.yaml",
        REPO_ROOT / "k8s" / "base" / "secrets.yaml",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "MCP_USERNAME" not in source, path
        assert "MCP_PASSWORD" not in source, path
        assert "TESTLOOKUP_USERNAME" not in source, path
        assert "TESTLOOKUP_PASSWORD" not in source, path


def test_configured_external_host_is_allowed_and_unknown_host_is_rejected(monkeypatch):
    import server
    from mcp.server.auth.provider import AccessToken

    verified = AccessToken(
        token="caller-token",
        client_id="caller-jti",
        subject="caller-id",
        expires_at=2_000_000_000,
        scopes=[],
    )
    monkeypatch.setattr(
        server.mcp._token_verifier,
        "verify_token",
        AsyncMock(return_value=verified),
    )
    allowed = server.mcp.settings.transport_security.allowed_hosts
    monkeypatch.setattr(
        server.mcp.settings.transport_security,
        "allowed_hosts",
        [*allowed, "mcp.example.test"],
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=server.mcp.sse_app())
        headers = {"Authorization": "Bearer caller-token"}
        async with httpx.AsyncClient(transport=transport, base_url="https://ignored") as http:
            accepted = await http.post(
                "/messages/?session_id=00000000-0000-0000-0000-000000000000",
                headers={**headers, "Host": "mcp.example.test"},
                json={},
            )
            rejected = await http.post(
                "/messages/?session_id=00000000-0000-0000-0000-000000000000",
                headers={**headers, "Host": "attacker.invalid"},
                json={},
            )
        assert accepted.status_code == 404
        assert rejected.status_code == 421

    asyncio.run(exercise())


def test_packaged_network_endpoints_use_safe_exposure_defaults():
    """Compose stays host-local and OpenShift routes cover both SSE endpoints."""
    for name in ("docker-compose.yml", "docker-compose.release.yml"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert '"127.0.0.1:8002:8002"' in source, name

    for overlay in ("openshift", "openshift-artifactory"):
        path = REPO_ROOT / "k8s" / "overlays" / overlay / "route.yaml"
        source = path.read_text(encoding="utf-8")
        mcp_route = next(
            document
            for document in source.split("\n---")
            if "name: testlookup-mcp" in document
        )
        assert "path: /sse" not in mcp_route, path
        assert "insecureEdgeTerminationPolicy: Redirect" in mcp_route, path

    ingress = (REPO_ROOT / "k8s" / "base" / "ingress.yaml").read_text(
        encoding="utf-8"
    )
    assert "name: testlookup-mcp-ingress" in ingress
    assert "name: testlookup-mcp" in ingress
    assert "path: /" in ingress

    homelab = (REPO_ROOT / "k8s" / "overlays" / "homelab" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    assert "name: testlookup-mcp-ingress" in homelab
    assert "$patch: delete" in homelab
    assert 'value: "127.0.0.1:*,localhost:*,[::1]:*"' in homelab

    hpa = (REPO_ROOT / "k8s" / "base" / "hpa.yaml").read_text(encoding="utf-8")
    mcp_hpa = hpa.split("name: testlookup-mcp-hpa", maxsplit=1)[1]
    assert "maxReplicas: 1" in mcp_hpa

    deployment = (REPO_ROOT / "k8s" / "base" / "mcp-deployment.yaml").read_text(
        encoding="utf-8"
    )
    assert "type: Recreate" in deployment
    assert "path: /health/ready" in deployment
    assert "timeoutSeconds: 6" in deployment
    assert 'haproxy.router.openshift.io/timeout: "1h"' in (
        REPO_ROOT / "k8s" / "overlays" / "openshift" / "route.yaml"
    ).read_text(encoding="utf-8")


def test_mcp_sdk_is_pinned_to_principal_binding_release():
    requirements = (MCP_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert "mcp==1.29.1" in requirements
