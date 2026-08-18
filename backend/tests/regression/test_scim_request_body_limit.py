import json

import pytest


def _scope(path="/api/v1/scim/v2/Users/user-id", method="PATCH", headers=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
        "server": ("example.test", 443),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }


async def _run(messages, *, scope=None, max_bytes=8):
    from app.middleware.scim_request_limit import SCIMRequestBodyLimitMiddleware

    called = False
    downstream_body = bytearray()
    sent = []
    queue = list(messages)

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def app(_scope, app_receive, app_send):
        nonlocal called
        called = True
        while True:
            message = await app_receive()
            if message["type"] != "http.request":
                break
            downstream_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await app_send({"type": "http.response.start", "status": 204, "headers": []})
        await app_send({"type": "http.response.body", "body": b""})

    middleware = SCIMRequestBodyLimitMiddleware(app, max_bytes=max_bytes)
    await middleware(scope or _scope(), receive, send)
    return called, bytes(downstream_body), sent, queue


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_without_reading_or_calling_app():
    called, body, sent, remaining = await _run(
        [{"type": "http.request", "body": b"never-read"}],
        scope=_scope(headers=[(b"content-length", b"9")]),
    )

    assert called is False
    assert body == b""
    assert len(remaining) == 1
    assert sent[0]["status"] == 413
    assert (b"content-type", b"application/scim+json") in sent[0]["headers"]
    payload = json.loads(sent[1]["body"])
    assert payload["status"] == "413"
    assert payload["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


@pytest.mark.asyncio
async def test_streamed_oversize_without_content_length_is_rejected():
    called, body, sent, _ = await _run(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": True},
            {"type": "http.request", "body": b"ignored", "more_body": False},
        ]
    )

    assert called is False
    assert body == b""
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_exact_limit_is_replayed_to_downstream_app():
    called, body, sent, _ = await _run(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )

    assert called is True
    assert body == b"12345678"
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        _scope(path="/api/v1/scim-tokens", method="POST"),
        _scope(path="/api/v1/scim/v2/Users", method="GET"),
        _scope(path="/api/v1/ingest/upload", method="POST"),
    ],
)
async def test_non_provisioning_or_bodyless_routes_are_not_limited(scope):
    called, body, sent, _ = await _run(
        [{"type": "http.request", "body": b"123456789", "more_body": False}],
        scope=scope,
    )

    assert called is True
    assert body == b"123456789"
    assert sent[0]["status"] == 204
