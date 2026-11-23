"""A 200 answer that is not JSON must be a clear ``CLIError``, never a traceback (D-117).

A slightly wrong URL often reaches a web server that answers 200 with an HTML
page: the web app's ``index.html`` (its nginx serves it for any unknown path)
or a proxy's sign-in page. ``client.request`` ended with a bare
``resp.json()``, so ``JSONDecodeError`` escaped every command, and
``testlookup doctor`` (the command meant for exactly this case) crashed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands import doctor
from testlookup_cli.errors import CLIError
from typer.testing import CliRunner

_PAGE = "<!doctype html><html><head><title>TestLookup</title></head><body></body></html>"


def _html_transport(status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, text=_PAGE, headers={"content-type": "text/html"})
    )


@pytest.fixture
def html_server(monkeypatch):
    """Every ``httpx.AsyncClient`` the client opens answers with the web app's page."""
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = _html_transport()
        return real(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)
    profile = {"name": "default", "url": "https://tl.example", "auth_type": "api_key", "api_key": "k"}
    monkeypatch.setattr(client, "get_profile", lambda _n=None: dict(profile))
    monkeypatch.setattr(doctor.config, "get_profile", lambda _n=None: dict(profile))


def _assert_not_json_error(exc: CLIError) -> None:
    message = str(exc)
    assert "not JSON" in message
    assert "text/html" in message
    assert "<html" not in message


def test_request_maps_an_html_200_to_a_cli_error(html_server):
    with pytest.raises(CLIError) as exc:
        asyncio.run(client.request("GET", "/health/version"))
    _assert_not_json_error(exc.value)


def test_upload_file_maps_an_html_200_to_a_cli_error(html_server, tmp_path: Path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"PK")
    with pytest.raises(CLIError) as exc:
        asyncio.run(client.upload_file("/api/v1/project-imports", archive, {}))
    _assert_not_json_error(exc.value)


def test_upload_part_maps_an_html_200_to_a_cli_error(html_server):
    with pytest.raises(CLIError) as exc:
        asyncio.run(client.upload_part("job", 1, b"x", "0" * 64))
    _assert_not_json_error(exc.value)


def test_login_maps_an_html_200_to_a_cli_error(html_server):
    with pytest.raises(CLIError) as exc:
        asyncio.run(client.login("https://tl.example", "u", "p"))
    _assert_not_json_error(exc.value)


def test_doctor_fails_the_server_check_without_a_traceback(html_server):
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    # A traceback leaves its exception on the result; a handled failure does not.
    assert not isinstance(result.exception, ValueError), result.exception
    assert "Traceback" not in result.output
    assert "server" in result.output and "not JSON" in result.output


def test_doctor_refuses_json_that_is_not_a_version(monkeypatch):
    """JSON from some other service (a list, a string) is not a version either."""
    monkeypatch.setattr(
        doctor.config, "get_profile",
        lambda _n=None: {"name": "default", "url": "https://tl.example", "api_key": "k"},
    )

    async def _fake(method, path, **_kw):
        return ["not", "a", "version"]

    monkeypatch.setattr(doctor.client, "request", _fake)
    results = asyncio.run(doctor._gather_checks(None))
    server = next(r for r in results if r["name"] == "server")
    assert server["status"] == "fail"
    assert "not a TestLookup version" in server["detail"]
