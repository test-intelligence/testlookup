"""``testlookup auth login | logout | whoami`` through the Typer commands (GAP-E02).

``cli/tests/test_auth_login_contract.py`` pins the login request's encoding at
the client level; no test ran the three commands, which decide what lands in
``profiles.json`` (API keys and refresh tokens) and what is printed. The
config directory is pointed at ``tmp_path`` and the server is an
``httpx.MockTransport``, so nothing leaves the process.

* login resolves the URL from ``TESTLOOKUP_URL``, saves both tokens and makes the profile active;
* a refused login exits 3 and writes nothing;
* logout ends the session on the server (``POST /api/v1/auth/logout``, best effort), removes the stored
  tokens AND a stored API key, keeps the profile's URL, says what it removed, and writes nothing for a
  profile that exists only through environment variables;
* ``--password-stdin`` reads the password from stdin; ``--password`` still works and warns;
* a plain ``http://`` URL to a host that is not this machine earns one warning;
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
NEW_ACCESS = "ci-access-token-2-test-only"
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
    """A fake server: ``POST /api/v1/auth/login`` accepts one username/password
    pair; ``POST /api/v1/auth/logout`` answers ``state["logout"]`` (a status
    code, or an exception to raise as a transport error) for the access token
    in ``state["live_access"]`` and 401 for any other; ``POST
    /api/v1/auth/refresh`` trades ``REFRESH`` for ``NEW_ACCESS``."""
    state = {
        "requests": [],
        "accept": ("dana", "ci-password-test-only"),
        "logout": 204,
        "live_access": {ACCESS, NEW_ACCESS},
    }
    real_client = httpx.AsyncClient
    monkeypatch.setattr(client, "_plain_http_warned", set(), raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/api/v1/auth/logout":
            if isinstance(state["logout"], Exception):
                raise state["logout"]
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if bearer not in state["live_access"]:
                return httpx.Response(401, json={"detail": "Could not validate credentials"})
            if state["logout"] == 204:
                return httpx.Response(204)
            return httpx.Response(state["logout"], json={"detail": "x"})
        if request.url.path == "/api/v1/auth/refresh":
            if json.loads(request.content).get("refresh_token") == REFRESH:
                return httpx.Response(200, json={"access_token": NEW_ACCESS})
            return httpx.Response(401, json={"detail": "Invalid refresh token"})
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


def _run(*args, env=None, input=None):
    return CliRunner().invoke(app, ["auth", *args], env=env, input=input)


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


def test_logout_drops_both_tokens_and_keeps_the_url(cfg, server):
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


def test_logout_then_whoami_reports_not_authenticated(cfg, server):
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


# -- logout: the server session, the API key, and environment-only profiles --


def _jwt_profile(name="work", url=SERVER):
    config.save_profile(
        name,
        {"url": url, "auth_type": "jwt", "access_token": ACCESS, "refresh_token": REFRESH},
    )


def _paths(server) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in server["requests"]]


def test_logout_ends_the_session_on_the_server_with_the_access_token(cfg, server):
    _jwt_profile()
    config.set_active_profile("work")

    result = _run("logout")

    assert result.exit_code == 0, result.output
    assert _paths(server) == ["POST /api/v1/auth/logout"]
    request = server["requests"][0]
    assert str(request.url) == f"{SERVER}/api/v1/auth/logout"
    assert request.headers["authorization"] == f"Bearer {ACCESS}"
    assert _profiles()["work"] == {"url": SERVER, "auth_type": "jwt", "name": "work"}
    plain = _plain(result.output)
    assert "Logged out (profile: work)" in plain
    assert "access token" in plain and "refresh token" in plain
    assert "session was ended on the server" in plain
    assert ACCESS not in result.output and REFRESH not in result.output


def test_logout_with_an_expired_access_token_refreshes_once_to_end_the_session(cfg, server):
    """Otherwise the refresh token copied from profiles.json keeps working."""
    server["live_access"] = {NEW_ACCESS}
    _jwt_profile()

    result = _run("logout", "--profile", "work")

    assert result.exit_code == 0, result.output
    assert _paths(server) == [
        "POST /api/v1/auth/logout",
        "POST /api/v1/auth/refresh",
        "POST /api/v1/auth/logout",
    ]
    assert server["requests"][-1].headers["authorization"] == f"Bearer {NEW_ACCESS}"
    assert "session was ended on the server" in _plain(result.output)
    assert NEW_ACCESS not in config.PROFILES_FILE.read_text()


@pytest.mark.parametrize(
    "failure",
    [503, 401, httpx.ConnectError("connection refused")],
    ids=["503", "401", "unreachable"],
)
def test_a_server_that_cannot_end_the_session_still_gets_the_profile_cleared(
    cfg, server, failure
):
    server["logout"] = failure
    if failure == 401:
        server["live_access"] = set()
    _jwt_profile()

    result = _run("logout", "--profile", "work")

    assert result.exit_code == 0, result.output
    assert _profiles()["work"] == {"url": SERVER, "auth_type": "jwt", "name": "work"}
    plain = _plain(result.output)
    assert "Could not end the session on the server" in plain
    assert "session was ended on the server" not in plain
    assert "Logged out (profile: work)" in plain


def test_logout_removes_a_stored_api_key_and_says_so(cfg, server):
    config.save_profile("ci", {"url": SERVER, "auth_type": "api_key", "api_key": API_KEY})
    _jwt_profile("work")

    result = _run("logout", "--profile", "ci")

    assert result.exit_code == 0, result.output
    assert _profiles()["ci"] == {"url": SERVER, "auth_type": "api_key", "name": "ci"}
    assert API_KEY not in config.PROFILES_FILE.read_text()
    assert _profiles()["work"]["access_token"] == ACCESS, "another profile is untouched"
    assert _paths(server) == [], "an API key has no session to end"
    plain = _plain(result.output)
    assert "API key" in plain
    assert "still valid on the server" in plain, "removing a key locally does not revoke it"
    assert API_KEY not in result.output


def test_logout_writes_nothing_for_a_profile_that_only_exists_in_the_environment(
    cfg, server, monkeypatch
):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", API_KEY)
    monkeypatch.setenv("TESTLOOKUP_URL", SERVER)

    result = _run("logout")

    assert result.exit_code == 0, result.output
    assert not config.PROFILES_FILE.exists(), (
        "logout wrote the environment's API key to profiles.json"
    )
    assert _paths(server) == []
    plain = _plain(result.output)
    assert "No saved credentials for profile default" in plain
    assert "TESTLOOKUP_API_KEY" in plain
    assert API_KEY not in result.output


def test_logout_of_an_unsaved_profile_leaves_the_saved_ones_as_they_are(cfg, server, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", API_KEY)
    _jwt_profile("work")
    before = config.PROFILES_FILE.read_text()

    result = _run("logout", "--profile", "env")

    assert result.exit_code == 0, result.output
    assert config.PROFILES_FILE.read_text() == before
    assert "No saved credentials for profile env" in _plain(result.output)


def test_a_saved_profile_can_be_told_from_the_environment_fallback(cfg, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", API_KEY)
    _jwt_profile("work")

    assert config.get_saved_profile("work")["access_token"] == ACCESS
    assert config.get_saved_profile("env") is None
    assert config.get_profile("env")["api_key"] == API_KEY, "the fallback itself is unchanged"


def test_login_then_a_command_then_logout(cfg, server):
    """Positive case: the ordinary session still works end to end."""
    assert _run("login", "--url", SERVER, "-u", "dana", "--password-stdin",
                input="ci-password-test-only\n").exit_code == 0
    assert json.loads(_run("whoami", "-o", "json").stdout)["authenticated"] is True
    assert client._build_headers(config.get_profile()) == {"Authorization": f"Bearer {ACCESS}"}

    assert _run("logout").exit_code == 0

    assert json.loads(_run("whoami", "-o", "json").stdout)["authenticated"] is False
    assert _paths(server) == ["POST /api/v1/auth/login", "POST /api/v1/auth/logout"]


# -- login: where the password comes from --


def test_password_stdin_reads_the_password_from_stdin(cfg, server):
    result = _run(
        "login", "--url", SERVER, "-u", "dana", "--password-stdin",
        input="ci-password-test-only\r\n",
    )

    assert result.exit_code == 0, result.output
    assert _profiles()["default"]["access_token"] == ACCESS
    assert "command line" not in _plain(result.output), "no warning for stdin"
    assert "ci-password-test-only" not in result.output


def test_the_password_option_still_works_and_warns_once(cfg, server):
    result = _run("login", "--url", SERVER, "-u", "dana", "-p", "ci-password-test-only")

    assert result.exit_code == 0, result.output
    assert _profiles()["default"]["access_token"] == ACCESS
    plain = _plain(result.stderr)
    assert plain.count("--password puts the password on the command line") == 1
    assert "--password-stdin" in plain
    assert "ci-password-test-only" not in result.output


def test_password_and_password_stdin_together_are_refused(cfg, server):
    result = _run(
        "login", "--url", SERVER, "-u", "dana", "-p", "x-test-only", "--password-stdin",
        input="ci-password-test-only\n",
    )

    assert result.exit_code == 2, result.output
    assert "not both" in _plain(result.output)
    assert server["requests"] == []


def test_password_stdin_needs_the_username_option(cfg, server):
    """Stdin carries the password, so the username cannot be prompted for."""
    result = _run("login", "--url", SERVER, "--password-stdin", input="ci-password-test-only\n")

    assert result.exit_code == 2, result.output
    assert "--username" in _plain(result.output)
    assert server["requests"] == []


def test_an_empty_password_on_stdin_is_refused(cfg, server):
    result = _run("login", "--url", SERVER, "-u", "dana", "--password-stdin", input="")

    assert result.exit_code == 2, result.output
    assert "No password on stdin" in _plain(result.output)
    assert server["requests"] == []


def test_the_login_help_marks_the_password_option_as_discouraged(cfg):
    result = _run("login", "--help")

    plain = _plain(result.output)
    assert "--password-stdin" in plain
    assert "Discouraged" in plain


# -- plain HTTP --

LOOPBACK_URLS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.8.9.10",
    "http://[::1]:8000",
    "http://app.localhost:3000",
    "https://testlookup.example.test",
]
REMOTE_URLS = [
    "http://testlookup.example.test",
    "http://10.0.0.5:8000",
    "HTTP://TestLookup.example.test:8000/",
    "http://localhost.example.test",
]


@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_https_and_loopback_urls_are_not_called_plain_http(url):
    assert client.is_plain_http_to_another_host(url) is False


@pytest.mark.parametrize("url", REMOTE_URLS)
def test_http_to_another_host_is_plain_http(url):
    assert client.is_plain_http_to_another_host(url) is True


def test_login_over_plain_http_to_another_host_warns_once(cfg, server):
    result = _run(
        "login", "--url", SERVER, "-u", "dana", "--password-stdin",
        input="ci-password-test-only\n",
    )

    assert result.exit_code == 0, result.output
    plain = _plain(result.stderr)
    assert plain.count("plain HTTP") == 1
    assert "testlookup.example.test" in plain and "https://" in plain


def test_login_to_this_machine_does_not_warn(cfg, server):
    result = _run(
        "login", "--url", "http://localhost:8000", "-u", "dana", "--password-stdin",
        input="ci-password-test-only\n",
    )

    assert result.exit_code == 0, result.output
    assert "plain HTTP" not in _plain(result.output)


def test_a_command_sending_a_stored_credential_over_plain_http_warns_once(
    cfg, server, monkeypatch, capsys
):
    config.save_profile("ci", {"url": SERVER, "auth_type": "api_key", "api_key": API_KEY})
    profile = config.get_profile("ci")

    assert client._build_headers(profile) == {"X-API-Key": API_KEY}
    assert client._build_headers(profile) == {"X-API-Key": API_KEY}

    err = _plain(capsys.readouterr().err)
    assert err.count("plain HTTP") == 1
    assert API_KEY not in err


def test_no_credential_no_plain_http_warning(cfg, server, capsys):
    assert client._build_headers({"url": SERVER, "auth_type": "jwt"}) == {}

    assert "plain HTTP" not in capsys.readouterr().err
