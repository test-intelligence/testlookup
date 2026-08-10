"""``testlookup auth login`` could not succeed against any server.

Two independent defects on the CLI's primary authentication path, both
reproduced against the live deployment.

**1. The request body is the wrong encoding.** ``client.login`` posts
``json={...}``; ``POST /api/v1/auth/login`` takes
``OAuth2PasswordRequestForm = Depends()``, which is form-encoded only. Measured
side by side against the running server::

    json= -> 422 {"detail":[{"loc":["body","username"],"msg":"Field required"}]}
    data= -> 200 {"access_token": "eyJ..."}

The 422 is then reported as::

    ✗ Login failed — check username and password

with **correct** credentials. A protocol mismatch is blamed on the user, who
would rotate passwords chasing a bug in the client.

**2. ``--url`` ignores ``TESTLOOKUP_URL``.** It is declared as
``typer.Option("http://localhost:8000")``, so with the environment variable set
— and honoured by every other command, via ``config.get_config()`` — login
still dials localhost and fails with ``All connection attempts failed``. The
CLI therefore appears broken before the encoding bug is even reached.

Together: out of the box the command cannot work, and when pointed at the right
host it misattributes its own failure.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from testlookup_cli import client
from testlookup_cli.commands import auth


class _Resp:
    status_code = 200

    def __init__(self):
        self.captured: dict = {}

    def json(self):
        return {"access_token": "tok", "refresh_token": "r"}


class _Client:
    """Records how the request was encoded."""

    def __init__(self, **_kw):
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        _Client.last = self  # type: ignore[attr-defined]
        return _Resp()


def test_login_posts_form_encoded_not_json(monkeypatch):
    """The endpoint is OAuth2PasswordRequestForm — json= 422s."""
    holder: dict = {}

    def _factory(**kw):
        c = _Client(**kw)
        holder["client"] = c
        return c

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)
    asyncio.run(client.login("http://server", "admin", "pw"))

    call = holder["client"].calls[0]
    assert "data" in call, (
        "login posts json= to an OAuth2PasswordRequestForm endpoint — the "
        "server 422s and the CLI reports it as 'check username and password'"
    )
    assert call["data"] == {"username": "admin", "password": "pw"}
    assert "json" not in call, "the body must not also be sent as JSON"


def test_login_targets_the_expected_path(monkeypatch):
    """Guards the test above from passing on a renamed endpoint."""
    holder: dict = {}
    monkeypatch.setattr(
        client.httpx, "AsyncClient", lambda **kw: holder.setdefault("c", _Client(**kw))
    )
    asyncio.run(client.login("http://server/", "admin", "pw"))
    assert holder["c"].calls[0]["url"] == "http://server/api/v1/auth/login"


def test_url_option_defaults_from_the_environment():
    """Every other command honours TESTLOOKUP_URL; login hardcoded localhost.

    Asserted on the declared default rather than by running the command, so the
    failure message names the actual defect instead of a connection error.
    """
    default = inspect.signature(auth.login).parameters["url"].default
    # Typer wraps the default in an OptionInfo; the literal lives on .default.
    literal = getattr(default, "default", default)
    assert literal != "http://localhost:8000", (
        "auth login hardcodes http://localhost:8000 and ignores TESTLOOKUP_URL, "
        "so the command fails with 'All connection attempts failed' on any "
        "server the rest of the CLI reaches perfectly well"
    )


def test_env_url_is_actually_used(monkeypatch, tmp_path):
    """The resolved default must follow the environment at call time.

    ``get_profile`` prefers a *saved* profile and only falls back to the
    environment, so the config dir is pointed at an empty temp dir to exercise
    the fallback deterministically.
    """
    from testlookup_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "PROFILES_FILE", tmp_path / "profiles.json", raising=False)
    monkeypatch.setenv("TESTLOOKUP_URL", "http://example.test:9999")
    assert config.get_profile()["url"] == "http://example.test:9999"


@pytest.mark.parametrize("status", [401, 422])
def test_a_failed_login_still_raises(monkeypatch, status):
    """The fix must not turn real auth failures into silent successes."""

    class _Bad(_Client):
        async def post(self, url, **kwargs):
            r = _Resp()
            r.status_code = status
            return r

    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: _Bad(**kw))
    with pytest.raises(Exception):
        asyncio.run(client.login("http://server", "admin", "bad"))
