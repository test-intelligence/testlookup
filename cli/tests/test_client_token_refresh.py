"""The shared ``client.request`` refreshes an expired JWT once (GAP-E03).

``cli/README.md`` promises: "When the server answers 401, the CLI refreshes the
access token once with the refresh token and saves the new one." Only the
upload path's own retry was tested (``test_upload_retry_and_wait.py``); every
other command goes through ``client.request`` (``client.py:134-138``) and
``_try_refresh`` (``client.py:321-338``), which nothing ran.

Driven through a real command (``projects list``) against an
``httpx.MockTransport`` server, with the config directory in ``tmp_path``:

* 401 -> one ``POST /auth/refresh`` -> the retry carries the NEW bearer, and the command succeeds;
* the new tokens are saved under the profile that made the call, and no other;
* a refused refresh exits 3 after exactly one refresh call and saves nothing;
* an API-key profile (saved or env-only) never calls ``/auth/refresh``.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client, config
from testlookup_cli.app import app

SERVER = "http://testlookup.example.test"
OLD_ACCESS = "ci-old-access-token-test-only"
OLD_REFRESH = "ci-old-refresh-token-test-only"
NEW_ACCESS = "ci-new-access-token-test-only"
NEW_REFRESH = "ci-new-refresh-token-test-only"
API_KEY = "qai_your_api_key_here"
PROJECTS = [
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "name": "Payment Service",
        "description": "",
        "created_at": "2026-11-01T10:00:00Z",
    }
]


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
    """Accepts only NEW_ACCESS (or API_KEY when ``accept_key``); refresh swaps OLD for NEW."""
    state = {
        "log": [],
        "refresh_status": 200,
        "refresh_body": None,
        "accept_key": False,
    }
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        key = request.headers.get("x-api-key", "")
        state["log"].append(
            (request.method, request.url.path, auth or (f"key:{key}" if key else ""))
        )
        if request.url.path == "/api/v1/auth/refresh":
            body = json.loads(request.content)
            if (
                state["refresh_status"] != 200
                or body.get("refresh_token") != OLD_REFRESH
            ):
                return httpx.Response(401, json={"detail": "Invalid refresh token"})
            payload = state["refresh_body"] or {
                "access_token": NEW_ACCESS,
                "refresh_token": NEW_REFRESH,
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/api/v1/projects":
            if auth == f"Bearer {NEW_ACCESS}" or (
                state["accept_key"] and key == API_KEY
            ):
                return httpx.Response(200, json=PROJECTS)
            return httpx.Response(
                401, json={"detail": "Could not validate credentials"}
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)
    return state


def _jwt_profile(**extra):
    return {
        "url": SERVER,
        "auth_type": "jwt",
        "access_token": OLD_ACCESS,
        "refresh_token": OLD_REFRESH,
        **extra,
    }


def _projects_list(*args):
    return CliRunner().invoke(app, ["projects", "list", "-o", "json", *args])


def _profiles() -> dict:
    return json.loads(config.PROFILES_FILE.read_text())


def _calls(server, path):
    return [entry for entry in server["log"] if entry[1] == path]


def test_a_401_refreshes_once_and_retries_with_the_new_bearer(cfg, server):
    config.save_profile("work", _jwt_profile())
    config.set_active_profile("work")

    result = _projects_list()

    assert server["log"] == [
        ("GET", "/api/v1/projects", f"Bearer {OLD_ACCESS}"),
        ("POST", "/api/v1/auth/refresh", ""),
        ("GET", "/api/v1/projects", f"Bearer {NEW_ACCESS}"),
    ], "the retry after a refresh must carry the refreshed bearer"
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == PROJECTS


def test_the_new_tokens_are_saved_under_the_active_profile_only(cfg, server):
    config.save_profile(
        "default",
        {
            "url": "http://other.example.test",
            "auth_type": "jwt",
            "access_token": "ci-default-access-test-only",
            "refresh_token": "ci-default-refresh-test-only",
        },
    )
    config.save_profile("work", _jwt_profile())
    config.set_active_profile("work")

    result = _projects_list()

    assert result.exit_code == 0, result.output
    profiles = _profiles()
    assert profiles["work"]["access_token"] == NEW_ACCESS
    assert profiles["work"]["refresh_token"] == NEW_REFRESH
    assert profiles["work"]["url"] == SERVER and profiles["work"]["name"] == "work"
    assert profiles["default"]["access_token"] == "ci-default-access-test-only", (
        "another profile was overwritten"
    )
    assert profiles["default"]["refresh_token"] == "ci-default-refresh-test-only"
    assert set(profiles) == {"default", "work"}


def test_an_explicit_profile_option_is_the_profile_that_is_refreshed(cfg, server):
    config.save_profile(
        "default",
        {
            "url": SERVER,
            "auth_type": "jwt",
            "access_token": "ci-default-access-test-only",
        },
    )
    config.save_profile("work", _jwt_profile())
    config.set_active_profile("default")

    result = _projects_list("--profile", "work")

    assert result.exit_code == 0, result.output
    profiles = _profiles()
    assert profiles["work"]["access_token"] == NEW_ACCESS
    assert profiles["default"] == {
        "url": SERVER,
        "auth_type": "jwt",
        "access_token": "ci-default-access-test-only",
        "name": "default",
    }


def test_a_refresh_answer_without_a_refresh_token_keeps_the_old_one(cfg, server):
    config.save_profile("work", _jwt_profile())
    server["refresh_body"] = {"access_token": NEW_ACCESS}

    result = _projects_list("--profile", "work")

    assert result.exit_code == 0, result.output
    assert _profiles()["work"]["access_token"] == NEW_ACCESS
    assert _profiles()["work"]["refresh_token"] == OLD_REFRESH


def test_a_refused_refresh_exits_3_after_exactly_one_refresh(cfg, server):
    config.save_profile("work", _jwt_profile())
    before = config.PROFILES_FILE.read_text()
    server["refresh_status"] = 401

    result = _projects_list("--profile", "work")

    assert result.exit_code == 3, result.output
    assert len(_calls(server, "/api/v1/auth/refresh")) == 1
    assert _calls(server, "/api/v1/projects") == [
        ("GET", "/api/v1/projects", f"Bearer {OLD_ACCESS}")
    ]
    assert config.PROFILES_FILE.read_text() == before, (
        "a refused refresh must not rewrite the profile"
    )
    assert "Authentication failed" in " ".join(result.stderr.split())


def test_a_jwt_profile_without_a_refresh_token_does_not_refresh(cfg, server):
    config.save_profile(
        "work", {"url": SERVER, "auth_type": "jwt", "access_token": OLD_ACCESS}
    )

    result = _projects_list("--profile", "work")

    assert result.exit_code == 3, result.output
    assert _calls(server, "/api/v1/auth/refresh") == []


def test_a_saved_api_key_profile_never_refreshes(cfg, server):
    # A stray refresh token must not turn an API-key profile into a refreshing one.
    config.save_profile(
        "ci",
        {
            "url": SERVER,
            "auth_type": "api_key",
            "api_key": API_KEY,
            "refresh_token": OLD_REFRESH,
        },
    )

    result = _projects_list("--profile", "ci")

    assert result.exit_code == 3, result.output
    assert _calls(server, "/api/v1/auth/refresh") == []
    assert _calls(server, "/api/v1/projects") == [
        ("GET", "/api/v1/projects", f"key:{API_KEY}")
    ]


def test_an_env_only_api_key_is_sent_and_never_refreshes(cfg, server, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_URL", SERVER)
    monkeypatch.setenv("TESTLOOKUP_API_KEY", API_KEY)
    server["accept_key"] = True

    result = _projects_list()

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == PROJECTS
    assert server["log"] == [("GET", "/api/v1/projects", f"key:{API_KEY}")]
    assert not config.PROFILES_FILE.exists(), (
        "a read with an env key must not write profiles.json"
    )
