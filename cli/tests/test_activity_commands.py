"""W18 DEV-04: ``testlookup activity`` reads the Activity tab's routes.

Pinned: a project name resolves; ``--since 7d`` becomes an ISO timestamp; a page
with more says so; ``--all`` follows the cursor in order; ``show`` prints the
before/after diff; 403 exits 4; export writes the file.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands.activity import since_to_iso
from testlookup_cli.errors import map_http_error

P1 = "11111111-1111-4111-8111-111111111111"
E = [{"id": f"e{i}", "occurred_at": f"2026-10-2{6 - i}T10:00:00Z", "event_type": "release.gate_evaluated",
      "actor": {"label": "Dana"}, "entity": {"label": "2026.10"}, "summary": f"event {i}"} for i in range(3)]


@pytest.fixture
def api(monkeypatch):
    state = {"calls": [], "deny": False}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        state["calls"].append((path, dict(params or {})))
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Payment Service"}]
        if state["deny"]:
            raise map_http_error(403, "You do not have access to this project")
        if path == f"/api/v1/projects/{P1}/activity":
            if (params or {}).get("cursor") == "c2":
                return {"items": E[2:], "next_cursor": None}
            return {"items": E[:2], "next_cursor": "c2"}
        if path == f"/api/v1/projects/{P1}/activity/e0":
            return {**E[0], "diff": {"before": {"verdict": "GO"}, "after": {"verdict": "NO_GO"}, "changed_fields": ["verdict"]}}
        raise AssertionError(path)

    async def _download(path, *, profile_name=None, params=None, timeout=60.0):
        state["calls"].append((path, dict(params or {})))
        return b"id,event\ne0,release.gate_evaluated\n"

    monkeypatch.setattr(client, "request", _request)
    monkeypatch.setattr(client, "download", _download)
    return state


def _run(*args):
    return CliRunner().invoke(app, list(args))


def test_since_is_relative_or_passed_through():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    assert since_to_iso("7d", now) == "2026-10-19T12:00:00+00:00"
    assert since_to_iso("24h", now) == "2026-10-25T12:00:00+00:00"
    assert since_to_iso("2026-10-01", now) == "2026-10-01"


def test_one_page_says_there_is_more(api):
    result = _run("activity", "list", "--project", "Payment Service", "--since", "7d", "-o", "json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert [e["id"] for e in body["items"]] == ["e0", "e1"] and body["next_cursor"] == "c2"
    params = [c for c in api["calls"] if c[0].endswith("/activity")][0][1]
    assert params["limit"] == 50 and params["since"]
    table = _run("activity", "list", "--project", "Payment Service")
    assert "2 shown, more events" in table.output and "--cursor c2" in table.output


def test_all_follows_the_cursor_in_order(api):
    result = _run("activity", "list", "--project", P1, "--all", "-o", "json")
    assert [e["id"] for e in json.loads(result.output)["items"]] == ["e0", "e1", "e2"]


def test_show_prints_the_diff(api):
    result = _run("activity", "show", "e0", "--project", "Payment Service")
    assert result.exit_code == 0 and "verdict: GO -> NO_GO" in result.output


def test_a_project_you_cannot_read_exits_4(api):
    api["deny"] = True
    assert _run("activity", "list", "--project", P1).exit_code == 4


def test_export_writes_the_file(api, tmp_path):
    out = tmp_path / "a.csv"
    result = _run("activity", "export", "--project", P1, "-o", str(out), "--since", "7d")
    assert result.exit_code == 0 and out.read_bytes().startswith(b"id,event")
    exp = [c for c in api["calls"] if c[0].endswith("/activity/export")][0][1]
    assert exp["format"] == "csv" and "since" in exp
