"""``testlookup health`` must surface *which* dependency is down.

``GET /health/ready`` answers ``200`` with ``status: "ready"`` when the critical
dependencies (PostgreSQL, MongoDB, Redis) are up, and ``503`` with
``status: "not_ready"`` plus a per-dependency ``checks`` object when one is down.

Before this fix the command routed that ``503`` through ``client.request``'s
default ``raise_for_status``, so ``map_http_error(503)`` collapsed the whole body
to ``"Server error (503)."`` — throwing away the one thing the operator needs at
exactly the moment they need it: the name of the failing service. The command now
requests with ``raise_for_status=False``, renders the ``checks`` body, and exits
non-zero so it still doubles as a CI preflight.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.errors import CLIError, EXIT_ERROR


# ── fakes ────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = ""
        self._body = body

    def json(self):
        if self._body is _RAISE:
            raise ValueError("not json")
        return self._body


_RAISE = object()


class _StatusClient:
    """httpx.AsyncClient stand-in that returns a fixed status + body."""

    def __init__(self, status_code, body, **_kw):
        self._status = status_code
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def request(self, *_a, **_kw):
        return _Resp(self._status, self._body)


def _isolate_config(monkeypatch, tmp_path):
    from testlookup_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "PROFILES_FILE", tmp_path / "profiles.json", raising=False)
    monkeypatch.delenv("TESTLOOKUP_URL", raising=False)


_NOT_READY_BODY = {
    "status": "not_ready",
    "checks": {
        "postgres": {"status": "error", "detail": "connection refused"},
        "mongo": {"status": "ok"},
        "redis": {"status": "ok"},
    },
    "timestamp": "2026-08-27T00:00:00Z",
}

_READY_BODY = {
    "status": "ready",
    "checks": {
        "postgres": {"status": "ok"},
        "mongo": {"status": "ok"},
        "redis": {"status": "ok"},
    },
    "timestamp": "2026-08-27T00:00:00Z",
}


# ── client.request(raise_for_status=False) ───────────────────────────────

def test_request_returns_503_body_when_not_raising(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(503, _NOT_READY_BODY, **kw))
    data = asyncio.run(
        client.request("GET", "/health/ready", raise_for_status=False)
    )
    # The whole body survives — not collapsed to a "Server error (503)." string.
    assert data["status"] == "not_ready"
    assert data["checks"]["postgres"]["status"] == "error"


def test_request_still_raises_by_default_on_503(monkeypatch, tmp_path):
    # The default path is unchanged: a 503 still maps to a CLIError.
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(503, _NOT_READY_BODY, **kw))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/health/ready"))
    assert ei.value.exit_code == EXIT_ERROR
    assert "503" in str(ei.value)


def test_request_non_json_error_body_still_maps_cleanly(monkeypatch, tmp_path):
    # raise_for_status=False must not crash on a non-JSON error body (a proxy's
    # HTML 502) — it maps to a clean CLIError instead of a raw ValueError.
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(502, _RAISE, **kw))
    with pytest.raises(CLIError) as ei:
        asyncio.run(client.request("GET", "/health/ready", raise_for_status=False))
    assert ei.value.exit_code == EXIT_ERROR
    assert "Server error (502)" in str(ei.value)


# ── the health command end-to-end ────────────────────────────────────────

def test_health_degraded_surfaces_failing_dependency(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(503, _NOT_READY_BODY, **kw))
    result = CliRunner().invoke(app, ["health", "--output", "json"])
    # Non-zero exit — a down dependency is a failure a preflight must catch.
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_ready"
    # The operator can see exactly which service is down.
    assert payload["checks"]["postgres"]["status"] == "error"


def test_health_ready_exits_zero(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _StatusClient(200, _READY_BODY, **kw))
    result = CliRunner().invoke(app, ["health", "--output", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "ready"


def test_health_unreachable_server_exits_one(monkeypatch, tmp_path):
    # A fully-unreachable server still raises a mapped CLIError and exits 1.
    _isolate_config(monkeypatch, tmp_path)

    class _Raising(_StatusClient):
        async def request(self, *_a, **_kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Raising(0, None, **kw))
    result = CliRunner().invoke(app, ["health"])
    # A mapped CLIError → exit 1 (the actionable message text is covered by
    # test_client_connection_errors.py; it is printed to stderr here).
    assert result.exit_code == 1


def test_health_registered_on_app():
    result = CliRunner().invoke(app, ["--help"])
    assert "health" in result.stdout
