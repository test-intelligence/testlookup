"""A saved profile that hides ``TESTLOOKUP_API_KEY`` / ``TESTLOOKUP_URL`` says so (D-123).

The precedence stays as documented (a saved profile wins; the variables are the
fallback for a profile name that is not saved), but it used to be silent: on a
reused CI runner the job ran as whoever last ran ``auth login``. Now one warning
goes to stderr, once per command, so ``-o json`` on stdout stays parseable.
"""
from __future__ import annotations

import json
import re

import pytest
from testlookup_cli import client, config
from testlookup_cli.app import app
from typer.testing import CliRunner

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    home = tmp_path / "testlookup"
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "PROFILES_FILE", home / "profiles.json")
    monkeypatch.setattr(config, "ACTIVE_FILE", home / "active_profile")
    monkeypatch.setattr(config, "_env_ignored_warned", set(), raising=False)
    for name in ("TESTLOOKUP_PROFILE", "TESTLOOKUP_URL", "TESTLOOKUP_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.save_profile("default", {"url": "https://saved.example", "auth_type": "jwt", "access_token": "a"})
    return home


def test_a_saved_profile_with_the_env_key_set_warns_once_on_stderr(cfg, monkeypatch, capsys):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", "qai_env_key")

    first = config.get_profile()
    second = config.get_profile()

    assert first["url"] == second["url"] == "https://saved.example", "the precedence is unchanged"
    captured = capsys.readouterr()
    assert captured.out == ""
    err = _plain(captured.err)
    assert err.count("Using saved profile 'default'") == 1
    assert "TESTLOOKUP_API_KEY is set and ignored" in err
    assert "qai_env_key" not in captured.err


def test_both_variables_are_named(cfg, monkeypatch, capsys):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", "k")
    monkeypatch.setenv("TESTLOOKUP_URL", "https://env.example")
    config.get_profile()
    assert "TESTLOOKUP_API_KEY and TESTLOOKUP_URL are set and ignored" in _plain(capsys.readouterr().err)


def test_no_warning_without_the_variables_or_for_an_unsaved_name(cfg, monkeypatch, capsys):
    config.get_profile()
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("TESTLOOKUP_API_KEY", "k")
    profile = config.get_profile("env")
    assert profile["api_key"] == "k"
    assert capsys.readouterr().err == "", "the environment is in use: nothing to warn about"


def test_json_output_stays_parseable(cfg, monkeypatch):
    monkeypatch.setenv("TESTLOOKUP_API_KEY", "k")

    async def _request(method, path, **_kw):
        config.get_profile()  # what the real client does first
        return {"version": "1", "build": {"revision": "r"}} if path.endswith("version") else []

    monkeypatch.setattr(client, "request", _request)
    result = CliRunner().invoke(app, ["doctor", "-o", "json"])

    assert result.exit_code == 0, result.output
    assert {c["name"] for c in json.loads(result.stdout)["checks"]} == {"profile", "server", "auth"}
    assert "set and ignored" in _plain(result.stderr)
