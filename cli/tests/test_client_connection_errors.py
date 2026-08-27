"""An unreachable server must produce an actionable message, not a raw traceback.

The shared HTTP client raises ``httpx.ConnectError`` / timeout *before* any HTTP
response exists, so ``map_http_error`` (which needs a status code) never sees
them. Before this fix a fresh self-hoster whose server was not up yet — or who
mistyped the URL — got a bare ``[Errno 111] Connection refused`` (or an empty
message) at the highest-friction moment of adoption. Now every transport failure
on the shared client (and the upload path) is mapped to a ``CLIError`` that says
what to check.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from testlookup_cli import client
from testlookup_cli.commands import upload
from testlookup_cli.errors import (
    CLIError,
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_TIMEOUT,
    map_connection_error,
)


# ── fakes ────────────────────────────────────────────────────────────────

class _RaisingClient:
    """httpx.AsyncClient stand-in whose every call raises a transport error."""

    def __init__(self, exc, **_kw):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def request(self, *_a, **_kw):
        raise self._exc

    async def get(self, *_a, **_kw):
        raise self._exc

    async def post(self, *_a, **_kw):
        raise self._exc


class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.text = ""
        # ``_RAISE`` models an error body that is not JSON (or not an object) —
        # ``_raise_for_status`` must fall back to an empty detail, not crash.
        self._body = {"detail": "nope"} if body is None else body

    def json(self):
        if self._body is _RAISE:
            raise ValueError("not json")
        return self._body


_RAISE = object()


class _StatusClient(_RaisingClient):
    """Returns a real HTTP response so the status-code path is exercised.

    Both ``request`` and ``get`` (the download path) return the same response so
    a single fake drives either client method.
    """

    def __init__(self, status_code, body=None, **_kw):
        self._status = status_code
        self._body = body

    async def request(self, *_a, **_kw):
        return _Resp(self._status, self._body)

    async def get(self, *_a, **_kw):
        return _Resp(self._status, self._body)


def _isolate_config(monkeypatch, tmp_path):
    """Point config at an empty dir so base_url is the deterministic default."""
    from testlookup_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "PROFILES_FILE", tmp_path / "profiles.json", raising=False)
    monkeypatch.delenv("TESTLOOKUP_URL", raising=False)


def _patch_raising(monkeypatch, exc):
    # client.httpx, upload's local `import httpx`, and the real httpx module are
    # all the same object, so patching httpx.AsyncClient covers every call site.
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _RaisingClient(exc, **kw))


# ── map_connection_error unit ───────────────────────────────────────────

def test_map_connect_error_is_actionable():
    err = map_connection_error(httpx.ConnectError("refused"), "http://localhost:8000")
    assert isinstance(err, CLIError)
    assert err.exit_code == EXIT_ERROR
    msg = str(err)
    assert "Cannot reach the TestLookup server at http://localhost:8000" in msg
    assert "Is it running?" in msg
    # The bare transport text must not be the whole story.
    assert "refused" not in msg


def test_map_timeout_keeps_timeout_exit_code():
    err = map_connection_error(httpx.ConnectTimeout("slow"), "http://localhost:8000")
    assert err.exit_code == EXIT_TIMEOUT
    assert "timed out" in str(err)


def test_map_connection_error_without_base_url_omits_where():
    err = map_connection_error(httpx.ConnectError("x"))
    assert " at " not in str(err)


# ── client.request / download integration ────────────────────────────────

def test_request_maps_connect_error(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _patch_raising(monkeypatch, httpx.ConnectError("Connection refused"))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/api/v1/projects"))
    assert ei.value.exit_code == EXIT_ERROR
    assert "Cannot reach the TestLookup server at http://localhost:8000" in str(ei.value)


def test_request_maps_timeout(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _patch_raising(monkeypatch, httpx.ReadTimeout("slow"))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/api/v1/projects"))
    assert ei.value.exit_code == EXIT_TIMEOUT


def test_download_maps_connect_error(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _patch_raising(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.download("/api/v1/reports/x.pdf"))
    assert "Cannot reach the TestLookup server" in str(ei.value)


def test_status_errors_still_flow_through_map_http_error(monkeypatch, tmp_path):
    """The new transport-error guard must not swallow real HTTP-status errors."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(404, **kw))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/api/v1/projects/missing"))
    assert ei.value.exit_code == EXIT_NOT_FOUND


# ── the server's ``detail`` reaches the user on both paths ─────────────────


def test_request_surfaces_server_detail(monkeypatch, tmp_path):
    """A status error carries the server's reason, not just the generic label."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _StatusClient(404, body={"detail": "Project not found"}, **kw),
    )
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/api/v1/projects/missing"))
    assert ei.value.exit_code == EXIT_NOT_FOUND
    assert "Project not found" in str(ei.value)


def test_download_surfaces_server_detail(monkeypatch, tmp_path):
    """``download`` dropped the body before this fix, so a failed PDF export said
    only 'Not found.' — the run id, access, or 'run not complete' reason was
    lost. It must now read the ``detail`` exactly as ``request`` does."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _StatusClient(404, body={"detail": "Run not found"}, **kw),
    )
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.download("/api/v1/reports/runs/missing/pdf"))
    assert ei.value.exit_code == EXIT_NOT_FOUND
    assert "Run not found" in str(ei.value)


def test_download_non_json_error_body_does_not_crash(monkeypatch, tmp_path):
    """An error response whose body is not JSON must still map to a clean
    CLIError with an empty detail — never a raw ``ValueError`` from ``json()``."""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _StatusClient(500, body=_RAISE, **kw),
    )
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.download("/api/v1/reports/runs/x/pdf"))
    assert ei.value.exit_code == EXIT_ERROR
    assert "Server error (500)" in str(ei.value)


# ── upload path ───────────────────────────────────────────────────────────

def test_upload_maps_connect_error(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _patch_raising(monkeypatch, httpx.ConnectError("refused"))
    f = tmp_path / "results.xml"
    f.write_text("<testsuite/>")
    with pytest.raises(CLIError) as ei:
        asyncio.run(
            upload._upload_file(path=f, project="p", build="b", commit_range=None)
        )
    assert "Cannot reach the TestLookup server" in str(ei.value)
