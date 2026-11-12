"""Security gates judge the routed path, never one rebuilt from the Host header.

starlette builds ``request.url`` from the Host header and parses it again
(CVE-2026-48710, GHSA-86qp-5c8j-p5mr; starlette 0.52.x here), so a Host
header holding ``?`` or ``/`` changes ``request.url.path`` while the router
still runs the real path. Before this fix three gates read ``request.url.path``:

* the login rate limiter (``app/main.py``): ``Host: x?`` made the path ``""``,
  the lookup found no limiter, and password guessing went unlimited. nginx
  forwards ``Host $host`` and accepts ``?``, so a default install was exposed;
* the demo read-only gate (``app/core/demo_mode.py``): ``Host:
  x/api/v1/auth/logout?`` made a write to any route look like an allowlisted
  sign-out;
* the forced password change (``app/core/deps.py``): the same trick let a
  temporary-password session call routes other than the password change.

``app.core.request_path.routed_path`` reads ``scope["path"]``; the last test
refuses ``request.url.path`` anywhere in ``backend/app``.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from app.core import demo_mode
from app.core.config import settings
from app.core.request_path import routed_path

pytestmark = pytest.mark.regression

BACKEND_APP = Path(__file__).resolve().parents[2] / "app"
LOGIN = "/api/v1/auth/login"

#: Host headers that make starlette 0.52's ``request.url.path`` lie.
SPOOFING_HOSTS = ("x?", "x/api/v1/auth/logout?", "testserver/api/v1/auth/logout#")


def _request(path: str, host: str, method: str = "POST") -> Request:
    return Request({
        "type": "http", "method": method, "path": path, "root_path": "",
        "query_string": b"", "headers": [(b"host", host.encode("latin-1"))],
        "scheme": "http", "server": ("testserver", 80), "client": ("10.0.0.1", 1234),
    })


def _client() -> httpx.AsyncClient:
    import app.main as main

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver")


def test_the_spoof_still_works_on_the_starlette_in_use():
    """Guards the guard: if starlette stops lying, this file can be retired."""
    assert _request(LOGIN, "x?").url.path == ""
    assert _request(LOGIN, "x/api/v1/auth/logout?").url.path == "/api/v1/auth/logout"


@pytest.mark.parametrize("host", ("testserver", *SPOOFING_HOSTS))
def test_routed_path_is_the_scope_path_whatever_the_host(host):
    assert routed_path(_request(LOGIN, host)) == LOGIN


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ("testserver", *SPOOFING_HOSTS))
async def test_the_login_rate_limiter_applies_whatever_the_host(monkeypatch, host):
    import app.main as main

    calls: list[str] = []

    async def _exhausted(request):
        calls.append(routed_path(request))
        raise RateLimitExceeded(SimpleNamespace(error_message="too many sign-ins", limit="1/minute"))

    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(main, "_AUTH_LIMITERS", {LOGIN: (_exhausted, "too many sign-ins")})
    async with _client() as client:
        response = await client.post(
            LOGIN, data={"username": "x", "password": "y"}, headers={"host": host}
        )
    assert response.status_code == 429, f"Host {host!r} skipped the login rate limiter"
    assert calls == [LOGIN]


@pytest.mark.asyncio
@pytest.mark.parametrize("host", SPOOFING_HOSTS)
async def test_the_demo_gate_refuses_a_write_whatever_the_host(monkeypatch, host):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    async with _client() as client:
        response = await client.post("/api/v1/projects", json={}, headers={"host": host})
    assert response.status_code == 403, f"Host {host!r} slipped a write past demo mode"
    assert response.json() == {"detail": demo_mode.DEMO_READ_ONLY}


@pytest.mark.asyncio
@pytest.mark.parametrize("host", SPOOFING_HOSTS)
async def test_a_temporary_password_session_is_held_whatever_the_host(host):
    from fastapi import HTTPException

    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind, get_current_active_user

    user = _bind_credential_kind(
        SimpleNamespace(is_active=True, must_change_password=True, role="QA_ENGINEER"),
        CREDENTIAL_KIND_JWT,
    )
    with pytest.raises(HTTPException) as refused:
        await get_current_active_user(_request("/api/v1/projects", host, "GET"), user)
    assert refused.value.detail["code"] == "password_change_required"


def test_no_backend_code_reads_request_url_path():
    """Every decision, rate-limit key and metric label goes through routed_path."""
    offenders = []
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if path.name == "request_path.py":
            continue  # its docstring names the attribute it replaces
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b(request|req|websocket)\.url\.path\b", line):
                offenders.append(f"{path.relative_to(BACKEND_APP.parent)}:{number}")
    assert not offenders, (
        "request.url.path is rebuilt from the Host header (CVE-2026-48710); "
        "use app.core.request_path.routed_path instead: " + ", ".join(offenders)
    )
