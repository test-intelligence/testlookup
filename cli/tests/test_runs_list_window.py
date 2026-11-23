"""``runs list`` sends its window and names it (D-121).

It sent no ``days``, so it silently used the API's 30 days, could not ask for
all time, and its title said only "Test Runs".
"""
from __future__ import annotations

import json
import re

import pytest
from testlookup_cli import client
from testlookup_cli.app import app
from typer.testing import CliRunner

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def api(monkeypatch):
    calls = []

    async def _request(method, path, *, params=None, **_kw):
        calls.append((method, path, dict(params or {})))
        return {"items": [{"id": "r1", "build_number": "1201", "status": "PASSED"}], "total": 54, "page": 1, "pages": 3}

    monkeypatch.setattr(client, "request", _request)
    return calls


def _run(*args):
    return CliRunner().invoke(app, ["runs", "list", *args], env={"COLUMNS": "200"})


def test_the_default_window_is_sent_and_named(api):
    result = _run()
    assert result.exit_code == 0, result.output
    assert api[0][2]["days"] == 30
    assert "Test Runs (54 total, last 30 days)" in _plain(result.output)


def test_days_0_is_sent_as_all_time(api):
    result = _run("--days", "0")
    assert result.exit_code == 0, result.output
    assert api[0][2]["days"] == 0
    assert "Test Runs (54 total, all time)" in _plain(result.output)


def test_json_output_is_the_items_unchanged(api):
    result = _run("--days", "7", "-o", "json")
    assert result.exit_code == 0, result.output
    assert api[0][2]["days"] == 7
    assert json.loads(result.stdout)[0]["id"] == "r1"


def test_a_window_past_the_api_cap_is_a_usage_error(api):
    result = _run("--days", "400")
    assert result.exit_code == 2
    assert api == []
