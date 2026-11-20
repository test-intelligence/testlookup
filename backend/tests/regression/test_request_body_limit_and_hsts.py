"""Every request body is bounded by the backend, and https answers carry HSTS.

The rules:

* A request body over the route's limit is answered 413 with a message that
  states the limit, for every content type, whether the size is declared in
  ``Content-Length`` or only found while reading (chunked). The handler does
  not run. A body exactly at the limit is accepted.
* The routes that carry test results or an archive keep working above the
  default limit: their own checks (50 MiB file, 32 MiB part, 50,000 results)
  stay the authority, and the larger limit here is never below what the
  shipped nginx and ingress let through.
* WebSockets and streamed responses are not touched.
* ``Strict-Transport-Security`` is added to a response only when the request
  arrived over https, as decided by the proxy trust boundary
  (``X-Forwarded-Proto`` from a peer in ``TRUSTED_PROXY_IPS``); never for
  plain http, never for an untrusted peer's header, never twice.

Some cloud overlays route ``/api/`` from the load balancer straight to the
backend, so the frontend nginx's body limit and HSTS header never applied
there.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

pytest.importorskip("asyncpg")

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, StreamingResponse  # noqa: E402
from starlette.routing import Route, WebSocketRoute  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware.request_hardening import (  # noqa: E402
    HSTS_VALUE,
    LARGE_BODY_PATHS,
    FormBodyLimitMiddleware,
    RequestBodyLimitMiddleware,
    StrictTransportSecurityMiddleware,
    is_large_body_route,
)

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = settings.REQUEST_BODY_MAX_BYTES
UPLOAD = settings.REQUEST_BODY_UPLOAD_MAX_BYTES
MIB = 1024 * 1024
JSON = {"content-type": "application/json"}
PART_PATH = "/api/v1/project-imports/uploads/00000000-0000-0000-0000-000000000001/parts/1"


def _json_body(size: int) -> bytes:
    """A valid JSON object of exactly ``size`` bytes."""
    frame = b'{"pad":""}'
    return b'{"pad":"' + b"x" * (size - len(frame)) + b'"}'


def _client(target=app, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target, **kwargs), base_url="http://test"
    )


@pytest.fixture
def db_calls():
    """Override get_db; records whether any handler dependency ran."""
    state = {"opened": 0}

    class _Session:
        async def execute(self, *_a, **_kw):
            raise AssertionError("a handler reached the database")

    async def _db():
        state["opened"] += 1
        yield _Session()

    app.dependency_overrides[get_db] = _db
    yield state
    app.dependency_overrides.pop(get_db, None)


async def _raw(target, path: str, chunks: list[bytes], headers: list[tuple[bytes, bytes]],
               method: str = "POST") -> tuple[int, bytes, int]:
    """Drive ``target`` with a raw ASGI scope: no Content-Length unless given.

    Returns (status, body, number of receive() calls).
    """
    messages = [{"type": "http.request", "body": c, "more_body": True} for c in chunks]
    if messages:
        messages[-1]["more_body"] = False
    reads = {"n": 0}

    async def receive():
        reads["n"] += 1
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "client": ("203.0.113.9", 4000), "server": ("test", 80),
    }
    await target(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert sum(1 for m in sent if m["type"] == "http.response.start") == 1, sent
    return start["status"], body, reads["n"]


# ── Wiring ──────────────────────────────────────────────────────────────────


def test_the_limits_are_wired_from_the_settings_and_sit_innermost() -> None:
    assert DEFAULT == 10 * MIB
    assert UPLOAD == 100 * MIB
    classes = [m.cls for m in app.user_middleware]
    [entry] = [m for m in app.user_middleware if m.cls is RequestBodyLimitMiddleware]
    assert entry.kwargs == {"max_bytes": DEFAULT, "upload_max_bytes": UPLOAD}
    # user_middleware is outermost-first. Innermost, so the 413 passes through
    # CORS and Telemetry like the form limit's does.
    assert classes[-1] is RequestBodyLimitMiddleware
    assert classes.index(FormBodyLimitMiddleware) < classes.index(RequestBodyLimitMiddleware)


def test_hsts_is_registered_directly_inside_the_proxy_boundary() -> None:
    """Outside it, scope["scheme"] is still the proxy's own http; and anything
    registered between the two could answer without the header."""
    classes = [m.cls for m in app.user_middleware]
    position = classes.index(StrictTransportSecurityMiddleware)
    assert all(cls is ProxyHeadersMiddleware for cls in classes[:position]), classes[:position]


def test_every_large_body_path_is_a_real_route() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    assert LARGE_BODY_PATHS <= paths, LARGE_BODY_PATHS - paths
    assert "/api/v1/project-imports/uploads/{data_job_id}/parts/{part_number}" in paths
    assert is_large_body_route(PART_PATH)
    # Only the part PUT: the routes around it take small JSON.
    assert not is_large_body_route("/api/v1/project-imports/uploads")
    assert not is_large_body_route(PART_PATH.rsplit("/parts/", 1)[0] + "/complete")
    assert not is_large_body_route("/api/v1/ingest/uploads/abc")
    assert not is_large_body_route("/api/v1/auth/login")


def test_the_limits_leave_room_for_what_the_routes_and_front_doors_accept() -> None:
    """Nothing that is accepted today may be refused by this limit first."""
    from app.core.release_planning import MAX_PRD_BYTES
    from app.core.test_library_vocab import IMPORT_MAX_BYTES
    from app.routers.ingest import MAX_FILE_SIZE
    from app.services import project_export_vocab as export_vocab

    framing = 5 * MIB  # what nginx's 55m leaves around the 50 MiB file
    assert UPLOAD >= MAX_FILE_SIZE + framing
    assert UPLOAD >= export_vocab.IMPORT_UPLOAD_MAX_BYTES + framing
    assert UPLOAD >= export_vocab.IMPORT_PART_MAX_BYTES + MIB
    # Uploads that stay on the default limit.
    assert DEFAULT >= IMPORT_MAX_BYTES + MIB
    assert DEFAULT >= MAX_PRD_BYTES + MIB
    assert DEFAULT >= settings.FORM_URLENCODED_ACS_MAX_BYTES

    nginx = (ROOT / "frontend" / "nginx.conf.template").read_text(encoding="utf-8")
    [nginx_mib] = re.findall(r"^\s*client_max_body_size\s+(\d+)m;", nginx, flags=re.M)
    ingress = (ROOT / "k8s" / "base" / "ingress.yaml").read_text(encoding="utf-8")
    [ingress_mib] = re.findall(r'proxy-body-size:\s*"(\d+)m"', ingress)
    assert UPLOAD >= int(nginx_mib) * MIB
    assert UPLOAD >= int(ingress_mib) * MIB


# ── The real app: ordinary routes ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_json_body_one_byte_over_the_limit_is_refused_before_the_handler(db_calls) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/auth/register", content=_json_body(DEFAULT + 1), headers=JSON
        )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert str(DEFAULT) in detail and "10 MiB" in detail, detail
    assert db_calls["opened"] == 0, "the handler's dependencies ran"


@pytest.mark.asyncio
async def test_a_json_body_exactly_at_the_limit_is_accepted(db_calls) -> None:
    body = _json_body(DEFAULT)
    assert len(body) == DEFAULT
    async with _client() as client:
        response = await client.post("/api/v1/auth/register", content=body, headers=JSON)
    # Past the limit and into validation: the pad is not a registration.
    assert response.status_code == 422, response.status_code


@pytest.mark.asyncio
async def test_a_chunked_body_with_no_content_length_is_refused_while_it_is_read(db_calls) -> None:
    chunk = b"x" * MIB
    chunks = [b'{"pad":"'] + [chunk] * 40 + [b'"}']
    status, body, reads = await _raw(app, "/api/v1/auth/register", chunks, [(b"content-type", b"application/json")])
    assert status == 413
    assert str(DEFAULT).encode() in body
    assert db_calls["opened"] == 0, "the handler's dependencies ran"
    # Stopped at the limit, not after reading all 40 MiB.
    assert reads <= 13, reads


@pytest.mark.asyncio
async def test_the_limit_covers_other_content_types_and_methods(db_calls) -> None:
    async with _client() as client:
        multipart = await client.post(
            "/api/v1/test-management/cases/import",
            files={"file": ("cases.csv", b"x" * (DEFAULT + 1))},
        )
        text = await client.put(
            "/api/v1/auth/register", content=b"x" * (DEFAULT + 1), headers={"content-type": "text/plain"}
        )
    assert multipart.status_code == 413
    assert text.status_code == 413
    assert db_calls["opened"] == 0


@pytest.mark.asyncio
async def test_a_cross_origin_413_carries_cors_headers_and_is_seen_by_telemetry(db_calls) -> None:
    origin = settings.CORS_ORIGINS[0]
    async with _client() as client:
        response = await client.post(
            "/api/v1/auth/register", content=_json_body(DEFAULT + 1), headers={**JSON, "Origin": origin}
        )
    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("x-request-id"), "Telemetry did not see the 413"


# ── The real app: the routes that carry results or an archive ───────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/ingest", "/api/v1/stream/events/batch", "/api/v1/stream/ingest"])
async def test_a_json_batch_over_the_default_limit_still_reaches_the_route(db_calls, path) -> None:
    async with _client() as client:
        response = await client.post(path, content=_json_body(DEFAULT + MIB), headers=JSON)
    assert response.status_code != 413
    assert response.status_code in (401, 403, 422), response.status_code


@pytest.mark.asyncio
async def test_a_report_upload_over_the_default_limit_still_reaches_the_route(db_calls) -> None:
    report = b"<testsuite>" + b"x" * (DEFAULT + MIB) + b"</testsuite>"
    async with _client() as client:
        ingest = await client.post(
            "/api/v1/ingest/file", files={"file": ("report.xml", report)}, data={"build_number": "1"}
        )
        archive = await client.post("/api/v1/projects/import", files={"file": ("project.zip", report)})
        part = await client.put(PART_PATH, content=report)
    for response in (ingest, archive, part):
        assert response.status_code != 413, response.request.url
        assert response.status_code in (401, 403), response.status_code  # reached auth


@pytest.mark.asyncio
async def test_the_upload_routes_are_still_bounded(db_calls) -> None:
    """Declared sizes only: nothing of this size is really sent."""
    for path in ("/api/v1/ingest", "/api/v1/ingest/file", PART_PATH):
        headers = [(b"content-type", b"application/json"), (b"content-length", str(UPLOAD + 1).encode())]
        status, body, reads = await _raw(app, path, [b"{}"], headers)
        assert status == 413, path
        assert str(UPLOAD).encode() in body and b"100 MiB" in body, body
        assert reads == 0, "an oversize declared body was read"
    headers = [(b"content-type", b"application/json"), (b"content-length", str(UPLOAD).encode())]
    status, _, _ = await _raw(app, "/api/v1/ingest", [b"{}"], headers)
    assert status != 413


# ── The middleware itself, on a small app ───────────────────────────────────

LIMIT, BIG = 1000, 5000


def _small_app(max_bytes: int = LIMIT, upload_max_bytes: int = BIG):
    seen: dict = {"ran": 0, "bytes": None, "chunks": 0}

    async def whole(request: Request):
        body = await request.body()
        seen["ran"] += 1
        seen["bytes"] = len(body)
        return JSONResponse({"bytes": len(body)})

    async def streamed(request: Request):
        seen["ran"] += 1
        total = 0
        async for chunk in request.stream():
            seen["chunks"] += 1 if chunk else 0  # stream() ends with one empty chunk
            total += len(chunk)
        return JSONResponse({"bytes": total})

    async def swallowing(request: Request):
        """A handler that turns any read error into its own answer."""
        try:
            await request.body()
        except Exception:
            return JSONResponse({"detail": "could not read"}, status_code=400)
        return JSONResponse({})

    async def events(_request: Request):
        async def ticks():
            for n in range(3):
                yield f"data: {n}\n\n".encode()

        return StreamingResponse(ticks(), media_type="text/event-stream")

    async def socket(websocket):
        await websocket.accept()
        await websocket.send_text(await websocket.receive_text())
        await websocket.close()

    inner = Starlette(routes=[
        Route("/whole", whole, methods=["POST"]),
        Route("/api/v1/ingest", whole, methods=["POST"]),
        Route("/streamed", streamed, methods=["PUT"]),
        Route("/swallowing", swallowing, methods=["POST"]),
        Route("/events", events, methods=["GET", "POST"]),
        WebSocketRoute("/socket", socket),
    ])
    return RequestBodyLimitMiddleware(inner, max_bytes=max_bytes, upload_max_bytes=upload_max_bytes), seen


@pytest.mark.asyncio
async def test_small_app_at_the_limit_passes_and_one_byte_over_does_not() -> None:
    target, seen = _small_app()
    async with _client(target) as client:
        at = await client.post("/whole", content=b"x" * LIMIT)
        over = await client.post("/whole", content=b"x" * (LIMIT + 1))
    assert (at.status_code, at.json()) == (200, {"bytes": LIMIT})
    assert over.status_code == 413
    assert over.json() == {
        "detail": "Request body is larger than the limit of 1000 bytes for this route. Send a smaller request."
    }
    assert seen["ran"] == 1, "the handler ran for the refused body"


@pytest.mark.asyncio
async def test_small_app_chunked_bodies_are_counted_as_they_arrive() -> None:
    target, seen = _small_app()
    at_status, at_body, _ = await _raw(target, "/whole", [b"x" * 400, b"x" * 600], [])
    assert (at_status, at_body) == (200, b'{"bytes":1000}')
    seen["ran"] = 0
    status, body, reads = await _raw(target, "/whole", [b"x" * 400, b"x" * 600, b"x", b"x" * 400], [])
    assert status == 413 and b"1000 bytes" in body
    assert seen["ran"] == 0, "the handler ran"
    assert reads == 3, "reading went on after the limit was passed"


@pytest.mark.asyncio
async def test_small_app_a_streaming_handler_is_stopped_at_the_limit() -> None:
    target, seen = _small_app()
    status, body, _ = await _raw(target, "/streamed", [b"x" * 600, b"x" * 600, b"x" * 600], [], method="PUT")
    assert status == 413 and b"1000 bytes" in body
    assert seen["chunks"] == 1, "a chunk past the limit reached the handler"
    seen["chunks"] = 0
    status, body, _ = await _raw(target, "/streamed", [b"x" * 500, b"x" * 500], [], method="PUT")
    assert (status, body) == (200, b'{"bytes":1000}')
    assert seen["chunks"] == 2, "the body was buffered instead of streamed"


@pytest.mark.asyncio
async def test_small_app_the_refusal_wins_over_a_handler_that_swallows_the_error() -> None:
    target, _ = _small_app()
    status, body, _ = await _raw(target, "/swallowing", [b"x" * 600, b"x" * 600], [])
    assert status == 413 and b"1000 bytes" in body


@pytest.mark.asyncio
async def test_small_app_the_large_routes_get_the_upload_limit_and_zero_switches_a_limit_off() -> None:
    target, seen = _small_app()
    status, _, _ = await _raw(target, "/api/v1/ingest", [b"x" * 2500, b"x" * 2500], [])
    assert status == 200 and seen["bytes"] == BIG
    status, body, _ = await _raw(target, "/api/v1/ingest", [b"x" * 2500, b"x" * 2501], [])
    assert status == 413
    assert b"5000 bytes" in body and b"smaller batches" in body

    unlimited, seen = _small_app(max_bytes=0)
    status, _, _ = await _raw(unlimited, "/whole", [b"x" * 9000], [])
    assert status == 200 and seen["bytes"] == 9000


@pytest.mark.asyncio
async def test_small_app_streamed_responses_and_bodiless_requests_pass_through() -> None:
    target, _ = _small_app()
    async with _client(target) as client:
        got = await client.get("/events")
        posted = await client.post("/events", content=b"x" * LIMIT)
    for response in (got, posted):
        assert response.status_code == 200
        assert response.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_small_app_websockets_are_not_touched() -> None:
    target, _ = _small_app(max_bytes=4)
    with TestClient(target).websocket_connect("/socket") as socket:
        socket.send_text("x" * 5000)
        assert socket.receive_text() == "x" * 5000


# ── HSTS ────────────────────────────────────────────────────────────────────

TRUSTED_PROXY = "10.0.0.7"
FRONTEND_VALUE = "max-age=31536000"


def _hsts_app():
    async def plain(_request: Request):
        return JSONResponse({"ok": True})

    async def own(_request: Request):
        return JSONResponse({"ok": True}, headers={"Strict-Transport-Security": "max-age=60"})

    inner = Starlette(routes=[Route("/plain", plain), Route("/own", own)])
    # The order main.py builds: the proxy boundary outermost, HSTS inside it.
    return ProxyHeadersMiddleware(StrictTransportSecurityMiddleware(inner), trusted_hosts=TRUSTED_PROXY)


def test_the_value_is_the_one_the_frontend_nginx_sends() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf.template").read_text(encoding="utf-8")
    values = set(re.findall(r'add_header Strict-Transport-Security "([^"]+)"', nginx))
    assert values == {FRONTEND_VALUE}
    assert HSTS_VALUE.decode() == FRONTEND_VALUE


@pytest.mark.asyncio
async def test_forwarded_https_from_a_trusted_proxy_gets_the_header() -> None:
    async with _client(_hsts_app(), client=(TRUSTED_PROXY, 5000)) as client:
        response = await client.get("/plain", headers={"X-Forwarded-Proto": "https"})
    assert response.headers.get_list("strict-transport-security") == [FRONTEND_VALUE]


@pytest.mark.asyncio
async def test_plain_http_and_untrusted_peers_do_not_get_the_header() -> None:
    target = _hsts_app()
    async with _client(target, client=(TRUSTED_PROXY, 5000)) as trusted:
        forwarded_http = await trusted.get("/plain", headers={"X-Forwarded-Proto": "http"})
        no_header = await trusted.get("/plain")
    async with _client(target, client=("203.0.113.9", 5000)) as untrusted:
        spoofed = await untrusted.get("/plain", headers={"X-Forwarded-Proto": "https"})
    for response in (forwarded_http, no_header, spoofed):
        assert response.status_code == 200
        assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_a_header_the_response_already_has_is_not_duplicated() -> None:
    async with _client(_hsts_app(), client=(TRUSTED_PROXY, 5000)) as client:
        response = await client.get("/own", headers={"X-Forwarded-Proto": "https"})
    assert response.headers.get_list("strict-transport-security") == ["max-age=60"]


@pytest.mark.asyncio
async def test_the_real_app_sends_the_header_for_https_only(db_calls) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as secure:
        over_https = await secure.get("/")
        refused = await secure.post("/api/v1/auth/register", content=_json_body(DEFAULT + 1), headers=JSON)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as plain:
        over_http = await plain.get("/", headers={"X-Forwarded-Proto": "https"})
    assert over_https.headers.get_list("strict-transport-security") == [FRONTEND_VALUE]
    assert refused.status_code == 413
    assert refused.headers.get_list("strict-transport-security") == [FRONTEND_VALUE]
    # TRUSTED_PROXY_IPS is empty in tests: nobody's X-Forwarded-Proto counts.
    assert settings.TRUSTED_PROXY_IPS.strip() == ""
    assert "strict-transport-security" not in over_http.headers
