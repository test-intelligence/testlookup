"""W34 STRAT-11: ``testlookup cases`` reads the Test Management routes, read-only.

Pinned: the exact query each command sends (the export ONLY the Excel route's
filters); a project name resolves; table / json / yaml; a page with more says so;
``--all`` follows the pages in order and stops at the cap, saying so; a ``TC-``
short id resolves when exactly one case matches and exits 2 otherwise; the
mapped refusals keep their exit codes; export writes the bytes.
"""
from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands import cases
from testlookup_cli.errors import map_http_error

P1 = "11111111-1111-4111-8111-111111111111"
C1 = "1a2b3c4d-0000-4000-8000-000000000001"
C2 = "1a2b3c99-0000-4000-8000-000000000002"
C3 = "9f8e7d6c-0000-4000-8000-000000000003"
LIST = "/api/v1/test-management/cases"
EXPORT = "/api/v1/test-management/cases/export/excel"


def _case(cid, title, **kw):
    return {"id": cid, "title": title, "status": "approved", "priority": "high", "test_type": "functional",
            "suite_name": "checkout", "owner": "Dana", "is_automated": False, "version": 3, **kw}


CASES = [_case(C1, "Login works"), _case(C2, "Login fails"), _case(C3, "Logout")]


@pytest.fixture
def api(monkeypatch):
    state = {"calls": [], "error": None, "cases": list(CASES)}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        state["calls"].append((path, dict(params or {})))
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Payment Service"}]
        if state["error"]:
            raise map_http_error(state["error"], "refused")
        if path == LIST:
            size, page = params["size"], params["page"]
            rows = state["cases"]
            pages = max(1, -(-len(rows) // size))
            return {"items": rows[(page - 1) * size: page * size], "total": len(rows),
                    "page": page, "size": size, "pages": pages}
        if path == f"{LIST}/{C1}":
            return _case(C1, "Login works", steps=[{"step_number": 1, "action": "Open login", "expected_result": "Form shows"}],
                         preconditions="A user exists")
        if path == f"{LIST}/{C1}/history":
            return [{"version": 3, "created_at": "2026-11-01T10:00:00Z", "change_type": "update",
                     "changed_by_name": "Dana", "status": "approved", "changed_fields": ["title"], "change_summary": "Renamed"}]
        raise AssertionError(path)

    async def _download(path, *, profile_name=None, params=None, timeout=60.0):
        state["calls"].append((path, dict(params or {})))
        if state["error"]:
            raise map_http_error(state["error"], "refused")
        return b"PK\x03\x04xlsx-bytes"

    monkeypatch.setattr(client, "request", _request)
    monkeypatch.setattr(client, "download", _download)
    return state


def _run(*args):
    return CliRunner().invoke(app, list(args))


def _sent(api, path):
    return [params for p, params in api["calls"] if p == path]


def test_list_sends_the_project_and_the_route_defaults(api):
    result = _run("cases", "list", "--project", "Payment Service", "-o", "json")
    assert result.exit_code == 0, result.output
    assert _sent(api, LIST) == [{"project_id": P1, "page": 1, "size": 25}]
    body = json.loads(result.output)
    assert [c["id"] for c in body["items"]] == [C1, C2, C3] and body["total"] == 3 and body["next_page"] is None


def test_list_sends_every_filter_under_its_rest_name(api):
    result = _run("cases", "list", "-p", P1, "--status", "draft", "--status", "approved", "--type", "functional",
                  "--priority", "P1", "--suite", "checkout", "--search", "login", "--owner", "me",
                  "--automation", "automated", "--requirement", "REQ-7", "--include-archived",
                  "--page", "2", "--size", "10", "-o", "json")
    assert result.exit_code == 0, result.output
    assert _sent(api, LIST) == [{
        "project_id": P1, "page": 2, "size": 10, "status": ["draft", "approved"], "test_type": ["functional"],
        "priority": ["P1"], "suite_name": "checkout", "search": "login", "owner": "me",
        "automation": "automated", "requirement": "REQ-7", "include_archived": True,
    }]


def test_list_table_shows_the_short_id_and_says_there_is_more(api):
    result = _run("cases", "list", "-p", P1, "--size", "2")
    assert result.exit_code == 0, result.output
    assert "TC-1A2B3C" in result.output and "Login works" in result.output and "Logout" not in result.output
    assert "2 shown of 3, more cases" in result.output and "--page 2" in result.output


def test_list_yaml_is_a_document(api):
    result = _run("cases", "list", "-p", P1, "-o", "yaml")
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["items"][2]["title"] == "Logout"


def test_all_follows_the_pages_in_order_with_the_largest_page(api):
    result = _run("cases", "list", "-p", P1, "--all", "--size", "1", "-o", "json")
    assert result.exit_code == 0, result.output
    assert [c["id"] for c in json.loads(result.output)["items"]] == [C1, C2, C3]
    assert [p["page"] for p in _sent(api, LIST)] == [1, 2, 3]
    _run("cases", "list", "-p", P1, "--all", "-o", "json")
    assert _sent(api, LIST)[-1]["size"] == 200


def test_all_stops_at_the_cap_and_says_so(api, monkeypatch):
    monkeypatch.setattr(cases, "MAX_PAGES", 2)
    result = _run("cases", "list", "-p", P1, "--all", "--size", "1")
    assert result.exit_code == 0, result.output
    assert len(_sent(api, LIST)) == 2
    assert "2 shown of 3, stopped after 2 pages" in result.output and "--page 3" in result.output


def test_size_outside_the_route_range_exits_2(api):
    assert _run("cases", "list", "-p", P1, "--size", "201").exit_code == 2
    assert not _sent(api, LIST)


def test_an_unknown_format_exits_2_and_output_is_an_alias(api):
    for command in (("list", "-p", P1), ("get", C1), ("history", C1)):
        assert _run("cases", *command, "-o", "csv").exit_code == 2
    assert api["calls"] == []
    assert json.loads(_run("cases", "list", "-p", P1, "--output", "json").output)["total"] == 3


def test_get_prints_the_case_with_its_steps(api):
    result = _run("cases", "get", C1)
    assert result.exit_code == 0, result.output
    assert "TC-1A2B3C  Login works" in result.output and "1. Open login  -> Form shows" in result.output
    assert "Preconditions: A user exists" in result.output
    assert not _sent(api, LIST), "a full id needs no lookup"
    assert json.loads(_run("cases", "get", C1, "-o", "json").output)["steps"][0]["action"] == "Open login"


def test_a_short_id_resolves_when_exactly_one_case_matches(api):
    result = _run("cases", "get", "tc-1a2b3c4", "--project", "Payment Service", "-o", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == C1
    assert _sent(api, LIST) == [{"project_id": P1, "page": 1, "size": 200, "search": "tc-1a2b3c4", "include_archived": True}]
    assert _sent(api, f"{LIST}/{C1}") == [{}]


def test_a_short_id_matching_several_cases_exits_2_and_lists_them(api):
    result = _run("cases", "get", "TC-1A2B3C", "-p", P1)
    assert result.exit_code == 2, result.output
    assert "2 cases start with TC-1A2B3C" in result.output and C1 in result.output and C2 in result.output


def test_a_short_id_matching_nothing_exits_2(api):
    api["cases"] = [_case(C3, "Mentions TC-1A2B3C in its title")]
    result = _run("cases", "history", "TC-1A2B3C", "-p", P1)
    assert result.exit_code == 2, result.output
    assert "No case TC-1A2B3C in this project" in result.output


def test_a_short_id_without_a_project_or_a_non_id_exits_2(api):
    assert _run("cases", "get", "TC-1A2B3C").exit_code == 2
    assert _run("cases", "get", "login-works", "-p", P1).exit_code == 2
    assert not _sent(api, LIST)


def test_history_lists_the_versions(api):
    result = _run("cases", "history", C1)
    assert result.exit_code == 0, result.output
    assert "Renamed" in result.output and "Dana" in result.output
    assert json.loads(_run("cases", "history", C1, "-o", "json").output)[0]["version"] == 3


def test_export_writes_the_bytes_and_sends_only_the_excel_routes_filters(api, tmp_path):
    out = tmp_path / "cases.xlsx"
    result = _run("cases", "export", "-p", "Payment Service", "-o", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"PK\x03\x04xlsx-bytes"
    assert _sent(api, EXPORT) == [{"project_id": P1}]
    _run("cases", "export", "-p", P1, "-o", str(out), "--status", "approved", "--type", "functional",
         "--priority", "high", "--search", "login", "--owner", "me", "--automation", "not_automated",
         "--requirement", "REQ-7")
    assert _sent(api, EXPORT)[-1] == {
        "project_id": P1, "status": ["approved"], "test_type": ["functional"], "priority": ["high"],
        "search": "login", "owner": "me", "automation": "not_automated", "requirement": "REQ-7",
    }


def test_export_offers_no_suite():
    """The Excel route reads no suite_name: an offered --suite would be dropped silently."""
    result = _run("cases", "export", "-p", P1, "-o", "x.xlsx", "--suite", "checkout")
    assert result.exit_code == 2 and "--suite" in result.output


@pytest.mark.parametrize(("status", "code"), [(403, 4), (404, 5), (422, 2)])
@pytest.mark.parametrize("argv", [("list", "-p", P1), ("get", C1), ("history", C1), ("export", "-p", P1, "-o", "x.xlsx")])
def test_refusals_keep_their_exit_codes(api, tmp_path, monkeypatch, argv, status, code):
    monkeypatch.chdir(tmp_path)
    api["error"] = status
    result = _run("cases", *argv)
    assert result.exit_code == code, result.output
    assert not (tmp_path / "x.xlsx").exists()
