"""W23d AGT-14: the opt-in HTTP exchange capture (``http_capture.py``) and the reporter's use of it.

* Every case of ``sdk-vectors/http_exchange.json`` (the cross-language specification the server's
  ``http_exchange_redaction.py`` also runs) goes through the SDK port, and redacting the expected
  output again changes nothing.
* ``requests`` and ``httpx`` (sync + async) are captured through mock transports; the SDK's own client
  is not; a streamed response is not read; the caps hold; capture is OFF by default (nothing wrapped).
* The pytest plugin posts each test's exchanges ONCE with the test name its result used.
Secret samples are real-looking and non-hex.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import http_capture as hc
import testlookup_reporter as tr
from sdk_wire import FAKE_JWT, PytestConfig, PytestParser, PytestReport

#: The cross-language specification (G-A): every case runs through the SDK port below.
VECTORS_PATH = Path(__file__).resolve().parents[2] / "sdk-vectors" / "http_exchange.json"

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ3MjNkLXNhbXBsZSJ9.c2lnbmF0dXJlLXNhbXBsZS12YWx1ZS0x"
PAT = "ghp_" + "w23dSamplePatValueQxZkLmNoPqRsTuVwXy"[:36]
API = "https://api.example.test"


def _products(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/blob"):
        return httpx.Response(200, content=b"\x89PNG-w23d-binary", headers={"Content-Type": "image/png"})
    if request.url.path.startswith("/big"):
        return httpx.Response(200, text="w" * 70_000, headers={"Content-Type": "text/plain"})
    return httpx.Response(
        200,
        json={"id": 7, "name": "lamp", "price": 12.5, "note": f"token {PAT}", "password": "w23dSamplePass"},
        headers={"Set-Cookie": "sid=w23dsid; HttpOnly", "X-Request-Id": "req-w23d-1"},
    )


@pytest.fixture
def capture():
    cap = hc.HttpCapture()
    cap.install()
    yield cap
    cap.uninstall()


def _sync_client() -> httpx.Client:
    return httpx.Client(base_url=API, transport=httpx.MockTransport(_products))


# ── The shared vectors (sdk-vectors/http_exchange.json) through the SDK port ──


def _expand(value: Any) -> Any:
    """The vector file's generators: $repeat / $join / $list / $json (see its "generators")."""
    if isinstance(value, dict):
        keys = set(value)
        if keys == {"$repeat", "times"}:
            return str(_expand(value["$repeat"])) * int(value["times"])
        if keys == {"$join"}:
            return "".join(str(_expand(part)) for part in value["$join"])
        if keys == {"$list", "count"}:
            return [_expand(value["$list"]) for _ in range(int(value["count"]))]
        if keys == {"$json"}:
            return json.dumps(_expand(value["$json"]), ensure_ascii=False, separators=(",", ":"))
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


_VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
_CASES = [dict(case, input=_expand(case["input"]), expect=_expand(case["expect"])) for case in _VECTORS["cases"]]


def test_the_vector_file_is_the_one_this_port_implements():
    vocab = _VECTORS["vocabulary"]
    for name in ("EXCHANGES_PER_TEST_MAX", "BODY_WIRE_MAX_BYTES", "BODY_STORED_MAX_BYTES", "TRUNCATION_MARKER",
                 "HEADERS_MAX", "HEADER_NAME_MAX", "HEADER_VALUE_MAX", "URL_MAX", "ENDPOINT_MAX", "METHOD_RE",
                 "SCHEMA_KEYS_MAX", "FIELD_NAME_RE", "REDACTED", "UNNAMED_KEYS", "JSON_DEPTH_MAX", "ERROR_MAX",
                 "URL_PARTS_RE"):
        assert getattr(hc, name) == vocab[name], name
    for name in ("HEADER_DENYLIST", "HEADER_SECRET_SUFFIXES", "QUERY_DENYLIST", "ENDPOINT_ID_SEGMENT_RES",
                 "TOKEN_SHAPES", "SCHEMA_STATES"):
        assert list(getattr(hc, name)) == list(vocab[name]), name
    assert hc._JSON_PAIR.pattern == vocab["JSON_PAIR_RE"] and hc._FORM_PAIR.pattern == vocab["FORM_PAIR_RE"]
    assert len(_CASES) >= 30


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["name"])
def test_every_vector_case_through_the_sdk_port(case):
    assert hc.redact_exchange(case["input"]) == case["expect"]
    if case["expect"] is not None:
        # Idempotence: redacting the stored form again changes nothing.
        assert hc.redact_exchange(case["expect"]) == case["expect"]


@pytest.mark.parametrize(
    "case",
    [c for c in _CASES if c["expect"] is not None and all(
        len(str((c["input"].get(side) or {}).get("body") or "").encode("utf-8")) <= hc.BODY_WIRE_MAX_BYTES
        for side in ("request", "response"))],
    ids=lambda c: c["name"],
)
def test_what_the_sdk_sends_stores_exactly_the_expected_exchange(case):
    """The SDK skips only the store cut: the server's pass over what it sends equals the server's pass over the
    raw exchange (bodies within the wire cap). One known difference: when the server store-cuts a body the SDK
    already redacted, the marker's ``{omitted}`` counts from what it received, so it is short by the bytes the
    SDK's redaction removed -- compared without that number; ``body_bytes`` (the size before any cut) is exact."""
    wired = hc.wire_exchange(case["input"])
    assert wired is not None
    stored, expect = hc.redact_exchange(wired), copy.deepcopy(case["expect"])
    marker = re.compile(r"\[truncated: \d+ bytes not stored\]\Z")
    for side in ("request", "response"):
        for doc in (stored, expect):
            if doc[side]["body"]:
                doc[side] = dict(doc[side], body=marker.sub("[truncated]", doc[side]["body"]))
    assert stored == expect


# ── OFF by default ───────────────────────────────────────────────────────────


def test_capture_is_off_unless_asked():
    assert hc.capture_enabled(False, env={}, config_value=None) is False
    assert hc.capture_enabled(True, env={}) is True
    assert hc.capture_enabled(False, env={"TESTLOOKUP_CAPTURE_HTTP": "1"}) is True
    assert hc.capture_enabled(False, env={"TESTLOOKUP_CAPTURE_HTTP": "off"}, config_value="true") is False
    assert hc.capture_enabled(False, env={}, config_value="true") is True
    assert hc.capture_enabled(False, env={}, config_value=True) is True


def test_a_default_run_installs_no_wrapper(wire):
    parser = PytestParser()
    tr.pytest_addoption(parser)
    options = dict(parser.group.defaults)
    assert options["--testlookup-capture-http"] is False
    options.update({"--testlookup-url": "http://tl.test", "--testlookup-token": FAKE_JWT,
                    "--testlookup-project": "p-1"})
    config = PytestConfig(options)
    tr.pytest_configure(config)
    plugin = config.pluginmanager.plugins["testlookup_live"]
    assert plugin._capture is None
    plugin.pytest_sessionstart(None)
    try:
        for owner in (httpx.Client, httpx.AsyncClient):
            assert not hasattr(owner.send, "_testlookup_capture")
    finally:
        plugin.pytest_sessionfinish(None, 0)


# ── What is captured, and how it is redacted ─────────────────────────────────


def test_httpx_sync_is_captured_and_redacted_before_buffering(capture):
    capture.start("t1")
    with _sync_client() as client:
        response = client.get("/api/products/7?access_token=w23dSampleAccessToken123&page=2",
                              headers={"Authorization": f"Bearer {JWT}", "X-API-Key": "tlk_w23d_sample_key_v1"})
    assert response.json()["price"] == 12.5          # the test sees the real answer
    (exchange,) = capture.stop()
    assert exchange["method"] == "GET" and exchange["status"] == 200
    assert exchange["url"] == f"{API}/api/products/7?access_token=[REDACTED]&page=2"
    assert exchange["request"]["headers"]["authorization"] == "[REDACTED]"
    assert exchange["request"]["headers"]["x-api-key"] == "[REDACTED]"
    assert exchange["response"]["headers"]["set-cookie"] == "[REDACTED]"
    body = json.loads(exchange["response"]["body"])
    assert body["password"] == "[REDACTED]" and body["price"] == 12.5
    assert PAT not in exchange["response"]["body"]
    rendered = json.dumps(exchange)
    for raw in (JWT, "tlk_w23d_sample_key_v1", "w23dSampleAccessToken123", "w23dsid", "w23dSamplePass", PAT):
        assert raw not in rendered
    assert exchange["duration_ms"] >= 0 and exchange["started_at"].endswith("Z")


def test_httpx_async_is_captured(capture):
    async def go():
        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(_products)) as client:
            await client.post("/api/orders", json={"client_secret": "w23dClientSecretSample", "qty": 2})

    capture.start("t2")
    asyncio.run(go())
    (exchange,) = capture.stop()
    assert exchange["method"] == "POST"
    assert json.loads(exchange["request"]["body"]) == {"client_secret": "[REDACTED]", "qty": 2}


def test_requests_is_captured():
    requests = pytest.importorskip("requests")

    class _Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **_kwargs):
            response = requests.models.Response()
            response.status_code, response.url, response.request = 201, request.url, request
            response.headers["Content-Type"] = "application/json"
            response._content = b'{"id": 9, "api_key": "w23dRequestsKeySample"}'
            return response

        def close(self):
            pass

    cap = hc.HttpCapture()
    cap.install()
    try:
        session = requests.Session()
        session.mount("https://", _Adapter())
        cap.start("t3")
        answer = session.post(f"{API}/api/items", json={"name": "w23d"}, headers={"Cookie": "session=w23dCookieValue"})
        (exchange,) = cap.stop()
    finally:
        cap.uninstall()
    assert answer.json()["id"] == 9
    assert exchange["status"] == 201
    assert exchange["request"]["headers"]["Cookie"] == "[REDACTED]"
    assert json.loads(exchange["response"]["body"]) == {"id": 9, "api_key": "[REDACTED]"}


def test_the_sdks_own_client_is_never_captured():
    own = httpx.Client(base_url=API, transport=httpx.MockTransport(_products))
    cap = hc.HttpCapture(exclude=[own])
    cap.install()
    try:
        cap.start("t4")
        own.get("/api/products/1")
        with _sync_client() as other:
            other.get("/api/products/2")
        exchanges = cap.stop()
    finally:
        cap.uninstall()
        own.close()
    assert [e["url"] for e in exchanges] == [f"{API}/api/products/2"]


def test_a_streamed_response_is_not_read(capture):
    capture.start("t5")
    with _sync_client() as client:
        with client.stream("GET", "/api/products/3") as response:
            (exchange,) = capture.stop()
            assert exchange["response"]["body"] is None
            assert json.loads(response.read())["id"] == 7      # the test still owns the whole stream


def test_the_caps_hold(capture):
    capture.start("t6")
    with _sync_client() as client:
        for n in range(hc.EXCHANGES_PER_TEST_MAX + 5):
            client.get(f"/api/products/{n}")
    kept = capture.stop()
    assert len(kept) == hc.EXCHANGES_PER_TEST_MAX and capture.dropped == 5

    capture.start("t7")
    with _sync_client() as client:
        client.get("/big")
        client.get("/blob")
    big, blob = capture.stop()
    assert big["response"]["body_truncated"] is True
    assert big["response"]["body_bytes"] == 70_000
    assert len(big["response"]["body"].encode("utf-8")) <= hc.BODY_WIRE_MAX_BYTES
    assert blob["response"]["body"] is None                            # a non-text body is not captured
    assert blob["response"]["headers"]["content-type"] == "image/png"  # its headers are


def test_a_transport_error_is_recorded_and_still_raised(capture):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    capture.start("t8")
    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(httpx.ConnectError):
            client.get(f"{API}/api/down")
    (exchange,) = capture.stop()
    assert exchange["error"] == "ConnectError" and "status" not in exchange


def test_nothing_is_recorded_outside_a_test(capture):
    with _sync_client() as client:
        client.get("/api/products/1")
    capture.start("t9")
    assert capture.stop() == []


def test_uninstall_restores_the_originals():
    before = (httpx.Client.send, httpx.AsyncClient.send)
    cap = hc.HttpCapture()
    cap.install()
    assert httpx.Client.send is not before[0]
    cap.uninstall()
    assert (httpx.Client.send, httpx.AsyncClient.send) == before


# ── The pytest plugin: one post per test, the result's own test name ────────


class _Item:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


def _plugin(wire, answer: int = 202):
    original = wire.handle
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/stream/http-exchanges":
            wire.requests.append(request)
            posts.append(json.loads(request.content))
            return httpx.Response(answer, json={"stored": 1, "truncated": 0, "dropped": 0, "test_case_id": "tc-1"})
        return original(request)

    wire.handle = handle
    parser = PytestParser()
    tr.pytest_addoption(parser)
    options = dict(parser.group.defaults)
    options.update({"--testlookup-url": "http://tl.test", "--testlookup-token": FAKE_JWT,
                    "--testlookup-project": "p-1", "--testlookup-capture-http": True})
    config = PytestConfig(options)
    tr.pytest_configure(config)
    return config.pluginmanager.plugins["testlookup_live"], posts


def _run_one_test(plugin, nodeid: str, failed: bool = False) -> None:
    plugin.pytest_runtest_setup(_Item(nodeid))
    plugin.pytest_runtest_logreport(PytestReport(nodeid, when="setup"))
    with _sync_client() as client:
        client.get("/api/products/7", headers={"Authorization": f"Bearer {JWT}"})
    plugin.pytest_runtest_logreport(PytestReport(nodeid, when="call", failed=failed))
    plugin.pytest_runtest_logreport(PytestReport(nodeid, when="teardown"))


def test_the_plugin_posts_each_tests_exchanges_once_with_its_result_name(wire):
    plugin, posts = _plugin(wire)
    plugin.pytest_sessionstart(None)
    try:
        _run_one_test(plugin, "tests/test_shop.py::test_price", failed=True)
    finally:
        plugin.pytest_sessionfinish(None, 0)

    (post,) = posts
    events = [e for body in wire.bodies("POST", "/api/v1/stream/events/batch") for e in body["events"]]
    (result,) = [e for e in events if e.get("event_type") == "test_result"]
    assert post["test_name"] == result["test_name"] == "test_price"
    assert "class_name" not in post and "class_name" not in result
    assert (post["session_id"], post["run_id"]) == ("s-1", "r-1")
    # Only the test's own request: none of the reporter's session / batch traffic.
    assert [e["url"] for e in post["exchanges"]] == [f"{API}/api/products/7"]
    assert post["exchanges"][0]["request"]["headers"]["authorization"] == "[REDACTED]"
    assert JWT not in json.dumps(post)
    assert plugin._live.http_exchange_stats == {"http_exchanges_sent": 1, "http_exchanges_failed": 0}
    assert not hasattr(httpx.Client.send, "_testlookup_capture"), "uninstalled at session end"


def test_a_refused_post_is_counted_and_warned_once_never_raised(wire, caplog):
    plugin, posts = _plugin(wire, answer=503)
    plugin.pytest_sessionstart(None)
    try:
        with caplog.at_level("WARNING", logger="testlookup_reporter"):
            _run_one_test(plugin, "tests/test_shop.py::test_a")
            _run_one_test(plugin, "tests/test_shop.py::test_b")
    finally:
        plugin.pytest_sessionfinish(None, 0)
    assert len(posts) == 2
    assert plugin._live.http_exchange_stats["http_exchanges_failed"] == 2
    warnings = [r for r in caplog.records if "HTTP exchanges" in r.getMessage()]
    assert len(warnings) == 1
