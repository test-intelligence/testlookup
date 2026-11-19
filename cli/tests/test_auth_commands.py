"""``testlookup auth login | logout | whoami`` through the Typer commands (GAP-E02).

``cli/tests/test_auth_login_contract.py`` pins the login request's encoding at
the client level; no test ran the three commands, which decide what lands in
``profiles.json`` (API keys and refresh tokens) and what is printed. The
config directory is pointed at ``tmp_path`` and the server is an
``httpx.MockTransport``, so nothing leaves the process.

* login resolves the URL from ``TESTLOOKUP_URL``, saves both tokens and makes the profile active;
* a refused login exits 3 and writes nothing;
* logout drops both tokens and keeps the profile's URL (``cli/README.md``: "removes the stored tokens");
* whoami reports the state without ever printing a credential.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client, config
from testlookup_cli.app import app

SERVER = "http://testlookup.example.test"
ACCESS = "ci-access-token-test-only"
REFRESH = "ci-refresh-token-test-only"
API_KEY = "qai_your_api_key_here"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    home = tmp_path / "testlookup"
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "PROFILES_FILE", home / "profiles.json")
    monkeypatch.setattr(config, "ACTIVE_FILE", home / "active_profile")
    for name in ("TESTLOOKUP_PROFILE", "TESTLOOKUP_URL", "TESTLOOKUP_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def server(monkeypatch):
    """A fake ``POST /api/v1/auth/login`` that accepts one username/password pair."""
    state = {"requests": [], "accept": ("dana", "ci-password-test-only")}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path != "/api/v1/auth/login":
            return httpx.Response(404, json={"detail": "Not Found"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if (form.get("username"), form.get("password")) == state["accept"]:
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "token_type": "bearer",
                },
            )
        return httpx.Response(401, json={"detail": "Incorrect username or password"})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)
    return state


def _run(*args, env=None):
    return CliRunner().invoke(app, ["auth", *args], env=env)


def _profiles() -> dict:
    return json.loads(config.PROFILES_FILE.read_text())


def test_login_uses_testlookup_url_saves_both_tokens_and_activates_the_profile(
    cfg, server, monkeypatch
):
    monkeypatch.setenv("TESTLOOKUP_URL", SERVER)

    result = _run(
        "login", "-u", "dana", "-p", "ci-password-test-only", "--profile", "work"
    )

    assert result.exit_code == 0, result.output
    assert [str(r.url) for r in server["requests"]] == [f"{SERVER}/api/v1/auth/login"]
    assert _profiles() == {
        "work": {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "name": "work",
        }
    }
    assert config.ACTIVE_FILE.read_text().strip() == "work"
    assert config.get_active_profile_name() == "work"
    assert "ci-password-test-only" not in config.PROFILES_FILE.read_text()
    assert "Logged in as dana (profile: work)" in _plain(result.stdout)


def test_login_with_url_option_saves_under_the_active_default_profile(cfg, server):
    result = _run("login", "--url", SERVER, "-u", "dana", "-p", "ci-password-test-only")

    assert result.exit_code == 0, result.output
    assert _profiles()["default"]["url"] == SERVER
    assert _profiles()["default"]["access_token"] == ACCESS
    assert config.ACTIVE_FILE.read_text().strip() == "default"


def test_a_refused_login_exits_3_and_saves_nothing(cfg, server, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_URL", SERVER)

    result = _run(
        "login", "-u", "dana", "-p", "wrong-password-test-only", "--profile", "work"
    )

    assert result.exit_code == 3, result.output
    assert len(server["requests"]) == 1
    assert "Login failed" in _plain(result.stderr)
    assert not config.PROFILES_FILE.exists(), (
        "a refused login must not write profiles.json"
    )
    assert not config.ACTIVE_FILE.exists(), (
        "a refused login must not switch the active profile"
    )


def test_a_refused_login_leaves_an_existing_profile_untouched(cfg, server):
    config.save_profile(
        "work",
        {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
        },
    )
    config.set_active_profile("other")
    before = config.PROFILES_FILE.read_text()

    result = _run(
        "login", "-u", "dana", "-p", "wrong-password-test-only", "--profile", "work"
    )

    assert result.exit_code == 3, result.output
    assert config.PROFILES_FILE.read_text() == before
    assert config.ACTIVE_FILE.read_text().strip() == "other"


def test_logout_drops_both_tokens_and_keeps_the_url(cfg):
    config.save_profile(
        "work",
        {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
        },
    )
    config.save_profile(
        "other",
        {
            "url": "http://other.example.test",
            "auth_type": "api_key",
            "api_key": API_KEY,
        },
    )
    config.set_active_profile("work")

    result = _run("logout")

    assert result.exit_code == 0, result.output
    profiles = _profiles()
    assert "access_token" not in profiles["work"], "logout must drop the access token"
    assert "refresh_token" not in profiles["work"], "logout must drop the refresh token"
    assert profiles["work"] == {"url": SERVER, "auth_type": "jwt", "name": "work"}
    assert profiles["other"]["api_key"] == API_KEY, (
        "logout clears only the selected profile"
    )
    assert ACCESS not in config.PROFILES_FILE.read_text()
    assert REFRESH not in config.PROFILES_FILE.read_text()
    assert "Logged out (profile: work)" in _plain(result.stdout)


def test_logout_then_whoami_reports_not_authenticated(cfg):
    config.save_profile(
        "work",
        {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
        },
    )

    assert _run("logout", "--profile", "work").exit_code == 0
    result = _run("whoami", "--profile", "work", "-o", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "profile": "work",
        "url": SERVER,
        "auth_type": "jwt",
        "authenticated": False,
    }


@pytest.mark.parametrize("fmt", ["table", "json", "yaml"])
def test_whoami_never_prints_a_token(cfg, fmt):
    config.save_profile(
        "work",
        {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
        },
    )
    config.set_active_profile("work")

    result = _run("whoami", "-o", fmt)

    assert result.exit_code == 0, result.output
    assert ACCESS not in result.output and REFRESH not in result.output
    plain = _plain(result.output)
    assert "work" in plain and SERVER in plain
    if fmt == "json":
        assert json.loads(result.stdout)["authenticated"] is True


def test_whoami_never_prints_an_api_key(cfg):
    config.save_profile(
        "ci", {"url": SERVER, "auth_type": "api_key", "api_key": API_KEY}
    )

    result = _run("whoami", "--profile", "ci", "-o", "json")

    assert result.exit_code == 0, result.output
    assert API_KEY not in result.output
    assert json.loads(result.stdout) == {
        "profile": "ci",
        "url": SERVER,
        "auth_type": "api_key",
        "authenticated": True,
    }
