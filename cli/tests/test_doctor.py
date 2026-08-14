"""``testlookup doctor`` must give a fresh self-hoster a single, ordered verdict
on their local setup — and exit non-zero only when a check hard-fails.

The checks are pure orchestration over ``client.request`` and ``config``, so the
tests stub those: an unreachable server, a not-yet-authenticated profile, and a
rejected credential each have a distinct, actionable outcome, and the exit code
distinguishes "not configured yet" (warn, exit 0) from "broken" (fail, exit 1).
"""
from __future__ import annotations

import asyncio

from typer.testing import CliRunner

from testlookup_cli.app import app
from testlookup_cli.commands import doctor
from testlookup_cli.errors import CLIError, EXIT_AUTH, EXIT_ERROR


# ── fakes ────────────────────────────────────────────────────────────────

def _fake_profile(**over) -> dict:
    base = {
        "name": "default",
        "url": "http://localhost:8000",
        "auth_type": "api_key",
        "api_key": "k",
    }
    base.update(over)
    return base


def _patch_profile(monkeypatch, profile: dict) -> None:
    monkeypatch.setattr(doctor.config, "get_profile", lambda _n=None: profile)


def _patch_request(monkeypatch, handler) -> None:
    """Replace ``client.request`` with an async ``handler(method, path)``."""

    async def _fake(method, path, **_kw):
        return handler(method, path)

    monkeypatch.setattr(doctor.client, "request", _fake)


def _statuses(results: list[dict]) -> dict[str, str]:
    return {r["name"]: r["status"] for r in results}


# ── _gather_checks: the check ladder ──────────────────────────────────────

def test_no_url_fails_and_short_circuits(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile(url=""))
    results = asyncio.run(doctor._gather_checks(None))
    # Only the profile check runs — nothing to reach without a URL.
    assert [r["name"] for r in results] == ["profile"]
    assert results[0]["status"] == "fail"
    assert "TESTLOOKUP_URL" in results[0]["detail"]


def test_unreachable_server_fails_and_skips_auth(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile())

    def handler(method, path):
        raise CLIError("Cannot reach the TestLookup server", EXIT_ERROR)

    _patch_request(monkeypatch, handler)
    results = asyncio.run(doctor._gather_checks(None))
    st = _statuses(results)
    assert st == {"profile": "ok", "server": "fail", "auth": "skip"}


def test_reachable_but_no_credentials_warns(monkeypatch):
    # A profile with a URL but no api_key / access_token.
    _patch_profile(monkeypatch, _fake_profile(auth_type="jwt", api_key=None))

    def handler(method, path):
        assert path == "/health/version"
        return {"version": "1.2.3", "build": {"revision": "abc123"}}

    _patch_request(monkeypatch, handler)
    results = asyncio.run(doctor._gather_checks(None))
    st = _statuses(results)
    assert st == {"profile": "ok", "server": "ok", "auth": "warn"}
    server = next(r for r in results if r["name"] == "server")
    assert "1.2.3" in server["detail"] and "abc123" in server["detail"]


def test_all_green(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile())

    def handler(method, path):
        if path == "/health/version":
            return {"version": "9.9.9", "build": {"revision": "deadbee"}}
        assert path == "/api/v1/projects"
        return []

    _patch_request(monkeypatch, handler)
    results = asyncio.run(doctor._gather_checks(None))
    assert _statuses(results) == {"profile": "ok", "server": "ok", "auth": "ok"}


def test_rejected_credentials_fail_auth(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile())

    def handler(method, path):
        if path == "/health/version":
            return {"version": "1", "build": {}}
        raise CLIError("Authentication failed", EXIT_AUTH)

    _patch_request(monkeypatch, handler)
    results = asyncio.run(doctor._gather_checks(None))
    assert _statuses(results)["auth"] == "fail"


def test_non_auth_error_on_probe_is_a_warning(monkeypatch):
    # A transient server error on the authed probe must not be reported as a
    # credential failure — the credentials were never rejected.
    _patch_profile(monkeypatch, _fake_profile())

    def handler(method, path):
        if path == "/health/version":
            return {"version": "1", "build": {}}
        raise CLIError("Server error (503).", EXIT_ERROR)

    _patch_request(monkeypatch, handler)
    results = asyncio.run(doctor._gather_checks(None))
    assert _statuses(results)["auth"] == "warn"


# ── CLI exit codes: only hard-fail is non-zero ────────────────────────────

def test_cli_exit_zero_when_reachable_unauthenticated(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile(auth_type="jwt", api_key=None))
    _patch_request(
        monkeypatch,
        lambda m, p: {"version": "1", "build": {"revision": "r"}},
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_cli_exit_one_when_server_unreachable(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile())

    def handler(m, p):
        raise CLIError("Cannot reach the TestLookup server", EXIT_ERROR)

    _patch_request(monkeypatch, handler)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_cli_json_output_is_machine_readable(monkeypatch):
    _patch_profile(monkeypatch, _fake_profile())
    _patch_request(
        monkeypatch,
        lambda m, p: {"version": "1", "build": {"revision": "r"}} if p.endswith("version") else [],
    )
    result = CliRunner().invoke(app, ["doctor", "--output", "json"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.stdout)
    assert {c["name"] for c in payload["checks"]} == {"profile", "server", "auth"}


def test_doctor_registered_on_app():
    # The command must actually be wired into the CLI, not just importable.
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert "doctor" in result.stdout
