"""The pytest plugin must authenticate with an API key, and apply the
configured session defaults.

Two defects, both in the shipped ``client/examples/python/pytest_smoke``:

* **An API key could not authenticate.** The plugin handed every credential
  to ``TestLookupReporter`` as ``token=``, and the reporter sent a passed
  token as ``Authorization: Bearer``. The server reads API keys only from
  ``X-API-Key`` and decodes a Bearer value as a JWT, so a ``qai_`` key from
  ``testlookup.api.key`` / ``TESTLOOKUP_API_KEY`` was refused with 401 and
  ``pytest_sessionstart`` raised before the first test.
* **testlookup.suite and testlookup.release never applied.** The defaults
  were filled in by ``TestLookupReporter.session()``, and the plugin opens
  its session through ``_create_session`` instead.

These tests drive the plugin's real hooks against a fake server that
enforces the server's auth rules (``conftest.Wire``).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import testlookup_reporter as tr
from sdk_wire import (
    FAKE_JWT,
    FAKE_KEY,
    PytestConfig,
    PytestParser,
    run_pytest_plugin,
    write_properties,
)

def _headers_of(wire, method: str, path_prefix: str) -> dict[str, str]:
    request = wire.first(method, path_prefix)
    assert request is not None, f"no {method} {path_prefix}"
    return {k.lower(): v for k, v in request.headers.items()}


# ── API keys ──────────────────────────────────────────────────────────────────

def test_an_api_key_in_testlookup_properties_streams_the_run(wire):
    """The pytest_smoke example, as shipped: endpoint + api.key + project."""
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
    )

    plugin = run_pytest_plugin()

    create = _headers_of(wire, "POST", "/api/v1/stream/sessions")
    assert create.get("x-api-key") == FAKE_KEY
    assert "authorization" not in create, "a qai_ key sent as Bearer is a 401"
    assert _headers_of(wire, "DELETE", "/api/v1/stream/sessions/s-1").get("x-api-key") == FAKE_KEY
    assert plugin._live.stats == {"sent": 1, "failed": 0}


def test_testlookup_api_key_env_var_streams_the_run(wire, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_ENDPOINT", "http://testlookup.test")
    monkeypatch.setenv("TESTLOOKUP_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TESTLOOKUP_PROJECT", "EverGreen")

    run_pytest_plugin()

    assert wire.told()["auth"] == ("X-API-Key", FAKE_KEY)


def test_the_api_key_flag_streams_the_run(wire):
    run_pytest_plugin(url="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen")

    assert wire.told()["auth"] == ("X-API-Key", FAKE_KEY)


def test_an_api_key_passed_as_the_token_flag_is_sent_as_an_api_key(wire):
    """--testlookup-token was the only credential flag for years; keys given
    through it must keep working rather than 401."""
    run_pytest_plugin(url="http://testlookup.test", token=FAKE_KEY, project="EverGreen")

    assert wire.told()["auth"] == ("X-API-Key", FAKE_KEY)


def test_a_jwt_is_still_sent_as_bearer(wire):
    run_pytest_plugin(url="http://testlookup.test", token=FAKE_JWT, project="EverGreen")

    headers = _headers_of(wire, "POST", "/api/v1/stream/sessions")
    assert headers.get("authorization") == f"Bearer {FAKE_JWT}"
    assert "x-api-key" not in headers


def test_a_flag_credential_beats_the_properties_file(wire):
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
    )

    run_pytest_plugin(token=FAKE_JWT)

    assert wire.told()["auth"] == ("Bearer", FAKE_JWT)


def test_an_api_key_wins_over_a_token_in_the_same_config(wire):
    """Same rule as the Java and JS SDKs."""
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", token=FAKE_JWT, api_key=FAKE_KEY, project="EverGreen",
    )

    run_pytest_plugin()

    assert wire.told()["auth"] == ("X-API-Key", FAKE_KEY)


def test_reporter_api_key_argument_is_sent_as_x_api_key(wire):
    async def go() -> None:
        reporter = tr.TestLookupReporter(
            base_url="http://testlookup.test", api_key=FAKE_KEY, project_id="EverGreen",
        )
        try:
            async with reporter.session(build_number="b-1") as live:
                await live.record("test_login", "PASSED", 5)
        finally:
            await reporter.aclose()

    asyncio.run(go())

    assert wire.told()["auth"] == ("X-API-Key", FAKE_KEY)


def test_without_any_credential_the_plugin_stays_off(wire):
    parser = PytestParser()
    tr.pytest_addoption(parser)
    options = dict(parser.group.defaults)
    options.update({"--testlookup-url": "http://testlookup.test", "--testlookup-project": "p"})
    config = PytestConfig(options)

    tr.pytest_configure(config)

    assert config.pluginmanager.plugins == {}
    assert wire.requests == []


# ── Session defaults ──────────────────────────────────────────────────────────

def test_configured_suite_and_release_reach_the_pytest_session(wire):
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
        suite="Checkout", release="2026.10",
    )

    run_pytest_plugin()

    told = wire.told()
    assert told["suite_name"] == "Checkout"
    assert told["release_name"] == "2026.10"


def test_the_launch_label_is_the_suite_fallback_in_pytest_too(wire):
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
        launch="Smoke suite",
    )

    run_pytest_plugin()

    told = wire.told()
    assert told["launch_name"] == "Smoke suite"
    assert told["suite_name"] == "Smoke suite"


def test_configured_build_branch_and_commit_reach_the_pytest_session(wire):
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
        build="42", branch="main", commit="abc1234",
    )

    run_pytest_plugin()

    told = wire.told()
    assert (told["build_number"], told["branch"], told["commit_hash"]) == ("42", "main", "abc1234")


def test_flags_beat_configured_session_values(wire):
    write_properties(
        Path("testlookup.properties"),
        endpoint="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen",
        build="42", branch="main", launch="From file",
    )

    run_pytest_plugin(build="99", branch="feature/x", launch="From flag")

    told = wire.told()
    assert (told["build_number"], told["branch"], told["launch_name"]) == ("99", "feature/x", "From flag")


def test_without_a_configured_build_the_plugin_names_one(wire):
    run_pytest_plugin(url="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen")

    assert re.fullmatch(r"pytest-\d+", wire.told()["build_number"])


def test_the_plugin_reports_framework_pytest_unless_configured(wire):
    run_pytest_plugin(url="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen")

    assert wire.told()["framework"] == "pytest"


def test_testlookup_framework_overrides_the_plugin_default(wire, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_FRAMEWORK", "pytest-bdd")

    run_pytest_plugin(url="http://testlookup.test", api_key=FAKE_KEY, project="EverGreen")

    assert wire.told()["framework"] == "pytest-bdd"
