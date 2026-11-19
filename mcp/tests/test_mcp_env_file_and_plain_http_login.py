"""The MCP server never reads ``.env`` from the working directory, and warns before a plain-http login.

In stdio mode an MCP client starts the server with whatever working directory
it likes, often the workspace the user has open. The server used to load
``.env`` from there, so a repository with a crafted ``.env`` could set
``TESTLOOKUP_API_URL`` and receive the user's TestLookup username and password
on the first tool call.

The rules:

* Settings come from real environment variables and from ONE file: ``.env``
  next to ``server.py``, or, when ``TESTLOOKUP_ENV_FILE`` is set, only the file
  it names (a relative name is taken from the server's directory). The working
  directory is never consulted.
* A login that would send the password over plain ``http`` to a host other
  than this machine still works, and logs one clear warning per process.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
import config  # noqa: E402

DEFAULT_URL = "http://localhost:8000"
VARIABLES = ("TESTLOOKUP_API_URL", "TESTLOOKUP_USERNAME", "TESTLOOKUP_PASSWORD", "TESTLOOKUP_ENV_FILE")


def _env(directory: Path, url: str, name: str = ".env") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(f"TESTLOOKUP_API_URL={url}\n", encoding="utf-8")
    return target


@pytest.fixture
def places(tmp_path, monkeypatch):
    """An empty server directory and a separate working directory, with no TESTLOOKUP_* variables set."""
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    server_dir, workspace = tmp_path / "server", tmp_path / "workspace"
    server_dir.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(config, "SERVER_DIR", server_dir, raising=False)
    monkeypatch.chdir(workspace)
    return server_dir, workspace


def test_a_dot_env_in_the_working_directory_is_not_read(places):
    _, workspace = places
    _env(workspace, "http://attacker.example:9")

    assert config.Settings().api_url == DEFAULT_URL


def test_the_dot_env_next_to_the_server_is_read(places):
    server_dir, workspace = places
    _env(server_dir, "https://testlookup.example")
    _env(workspace, "http://attacker.example:9")

    assert config.Settings().api_url == "https://testlookup.example"


def test_the_named_env_file_is_the_only_file_read(places, tmp_path, monkeypatch):
    server_dir, workspace = places
    _env(server_dir, "https://beside-the-server.example")
    _env(workspace, "http://attacker.example:9")
    named = _env(tmp_path / "elsewhere", "https://named.example", name="mcp.env")
    monkeypatch.setenv("TESTLOOKUP_ENV_FILE", str(named))

    assert config.Settings().api_url == "https://named.example"

    named.unlink()
    assert config.Settings().api_url == DEFAULT_URL, "with the variable set, the file beside the server is not a fallback"


def test_a_relative_named_env_file_is_taken_from_the_server_directory(places, monkeypatch):
    server_dir, workspace = places
    _env(server_dir, "https://beside-the-server.example", name="team.env")
    _env(workspace, "http://attacker.example:9", name="team.env")
    monkeypatch.setenv("TESTLOOKUP_ENV_FILE", "team.env")

    assert config.env_file() == server_dir / "team.env"
    assert config.Settings().api_url == "https://beside-the-server.example"


def test_a_real_environment_variable_wins_over_the_file(places, monkeypatch):
    server_dir, _ = places
    _env(server_dir, "https://from-the-file.example")
    monkeypatch.setenv("TESTLOOKUP_API_URL", "https://from-the-environment.example")

    assert config.Settings().api_url == "https://from-the-environment.example"


def test_the_default_location_is_beside_server_py(monkeypatch):
    monkeypatch.delenv("TESTLOOKUP_ENV_FILE", raising=False)

    assert config.env_file() == MCP_DIR / ".env"
    assert (MCP_DIR / "server.py").is_file()


# ── Plain-http login ─────────────────────────────────────────────────────────


@pytest.fixture
def login(monkeypatch):
    """stdio mode with fake credentials; every login request is recorded."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "fake-access-token", "expires_in": 60})

    def use(api_url: str) -> list[httpx.Request]:
        monkeypatch.setattr(client.settings, "api_url", api_url)
        monkeypatch.setattr(
            client, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=api_url)
        )
        return seen

    monkeypatch.setattr(client.settings, "username", "fake-user")
    monkeypatch.setattr(client.settings, "password", "not-a-real-password")
    monkeypatch.setattr(client, "_request_access_token", lambda: None)
    monkeypatch.setattr(client, "_stdio_access_token", None)
    monkeypatch.setattr(client, "_plain_http_warned", False, raising=False)
    return use


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "plain http" in r.getMessage()]


def test_a_plain_http_login_to_another_host_warns_once_and_still_logs_in(login, caplog):
    seen = login("http://testlookup.internal.example:8000")

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(client.authenticate()) == "fake-access-token"
        assert asyncio.run(client.authenticate()) == "fake-access-token"

    assert len(seen) == 2, "the login is not refused"
    messages = _warnings(caplog)
    assert len(messages) == 1, "one warning per process"
    assert "testlookup.internal.example" in messages[0]
    assert "unencrypted" in messages[0] and "https://" in messages[0]
    assert "not-a-real-password" not in messages[0] and "fake-user" not in messages[0]


@pytest.mark.parametrize(
    "api_url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://127.8.9.10",
        "http://[::1]:8000",
        "http://app.localhost:3000",
        "https://testlookup.internal.example",
    ],
)
def test_a_loopback_or_https_login_does_not_warn(login, caplog, api_url):
    seen = login(api_url)

    with caplog.at_level(logging.WARNING):
        asyncio.run(client.authenticate())

    assert len(seen) == 1
    assert _warnings(caplog) == []


@pytest.mark.parametrize("api_url", ["http://localhost.attacker.example", "http://10.0.0.5:8000", "http://backend:8000"])
def test_a_host_that_only_looks_local_still_warns(login, caplog, api_url):
    login(api_url)

    with caplog.at_level(logging.WARNING):
        asyncio.run(client.authenticate())

    assert len(_warnings(caplog)) == 1


def test_the_login_tool_warns_too(login, caplog):
    from tools import auth

    class _FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    fake = _FakeMCP()
    auth.register(fake)
    seen = login("http://testlookup.internal.example:8000")

    with caplog.at_level(logging.WARNING):
        text = asyncio.run(fake.tools["login"]("fake-user", "not-a-real-password"))

    assert "Logged in as" in text and len(seen) == 1
    assert len(_warnings(caplog)) == 1


def test_no_credentials_means_no_warning_and_no_request(login, caplog, monkeypatch):
    seen = login("http://testlookup.internal.example:8000")
    monkeypatch.setattr(client.settings, "username", "")

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(client.authenticate()) is None

    assert seen == [] and _warnings(caplog) == []
