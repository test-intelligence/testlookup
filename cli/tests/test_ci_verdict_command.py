"""``testlookup ci-verdict`` end to end through the Typer command (GAP-E01).

The pure partition and exit-code logic is tested in the backend job
(``backend/tests/test_ci_verdict_logic.py``); nothing ran the command itself.
This is the CI gate that decides whether a build goes green, so each case
pins one part of its contract with a fake ``client.request`` routed by path:

* every page of failures is read, so a real failure on page 2 still exits 1;
* all failures quarantined exits 0 and ``-o json`` is the ``build_verdict`` document;
* an auth error or a 5xx exits 2 and says it is failing CLOSED;
* ``--fail-open`` turns that into exit 0 with the warning;
* a run still executing past ``--timeout-seconds`` exits 2 without a verdict;
* a run with no project and no ``--project`` exits 2.

Rich wraps long lines and Typer styles output when ``GITHUB_ACTIONS`` is set,
so messages are compared as plain text with whitespace collapsed.
"""

from __future__ import annotations

import asyncio
import json
import re
import types

import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands import ci_verdict as ci_verdict_mod
from testlookup_cli.errors import EXIT_AUTH, map_http_error
from testlookup_cli.verdict_logic import build_verdict, compute_fingerprint

RUN_ID = "run-0000-ci-verdict-test-only"
PROJECT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT = "22222222-2222-4222-8222-222222222222"

FLAKY_BY_FINGERPRINT = {
    "test_name": "test_flaky_login",
    "class_name": "tests.auth.LoginTest",
    "suite_name": "auth",
    "status": "FAILED",
}
FLAKY_BY_NAME = {
    "test_name": "test_flaky_cart",
    "class_name": "tests.cart.CartTest",
    "suite_name": "cart",
    "status": "BROKEN",
}
REAL_ON_PAGE_2 = {
    "test_name": "test_checkout_total",
    "class_name": "tests.checkout.TotalTest",
    "suite_name": "checkout",
    "status": "FAILED",
}

MANIFEST_ENTRIES = [
    {
        "fingerprint": compute_fingerprint("test_flaky_login", "tests.auth.LoginTest"),
        "test_name": "test_flaky_login",
        "suite_name": "auth",
    },
    # Matched by the (test_name, suite_name) fallback: the fingerprint is not this test's.
    {
        "fingerprint": "0000000000000000",
        "test_name": "test_flaky_cart",
        "suite_name": "cart",
    },
]

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def api(monkeypatch):
    """A fake TestLookup routed by path; ``pages`` maps a status to its pages of items."""
    state = {
        "run": {"id": RUN_ID, "status": "COMPLETED", "project_id": PROJECT_ID},
        "run_statuses": [],  # consumed one per run fetch before falling back to state["run"]
        "pages": {
            "FAILED": [[FLAKY_BY_FINGERPRINT], [REAL_ON_PAGE_2]],
            "BROKEN": [[FLAKY_BY_NAME]],
        },
        "entries": list(MANIFEST_ENTRIES),
        "raise_on": None,  # (path-suffix, exception)
        "calls": [],
    }

    async def _request(
        method, path, *, profile_name=None, params=None, json_body=None, **_kw
    ):
        state["calls"].append((method, path, dict(params or {})))
        if state["raise_on"] and path.endswith(state["raise_on"][0]):
            raise state["raise_on"][1]
        if path == f"/api/v1/runs/{RUN_ID}":
            run = dict(state["run"])
            if state["run_statuses"]:
                run["status"] = state["run_statuses"].pop(0)
            return run
        if path == f"/api/v1/runs/{RUN_ID}/tests":
            pages = state["pages"].get(params["status"], [[]])
            page = params["page"]
            items = pages[page - 1] if page <= len(pages) else []
            return {
                "items": items,
                "total": sum(len(p) for p in pages),
                "page": page,
                "size": params["size"],
                "pages": len(pages),
            }
        if path.endswith("/quarantine/manifest"):
            return {"entries": state["entries"], "project_id": path.split("/")[4]}
        raise AssertionError(f"unexpected request {method} {path}")

    monkeypatch.setattr(client, "request", _request)
    return state


def _run(*args):
    return CliRunner().invoke(app, ["ci-verdict", "--run", RUN_ID, *args])


def _test_pages(api, status):
    return [
        params["page"]
        for _m, path, params in api["calls"]
        if path.endswith("/tests") and params.get("status") == status
    ]


def test_a_real_failure_on_the_second_page_blocks_the_build(api):
    result = _run("-o", "json")

    assert result.exit_code == 1, result.output
    assert _test_pages(api, "FAILED") == [1, 2], (
        "ci-verdict must read every page of FAILED tests"
    )
    assert _test_pages(api, "BROKEN") == [1]
    verdict = json.loads(result.stdout)
    assert verdict["verdict"] == "fail"
    assert [f["test_name"] for f in verdict["real_failures"]] == ["test_checkout_total"]
    assert verdict["counts"] == {
        "real_failures": 1,
        "quarantined_failures": 2,
        "total_failures": 3,
    }


def test_the_table_names_the_real_failure_from_page_two(api):
    result = _run()

    assert result.exit_code == 1, result.output
    plain = _plain(result.output)
    assert "test_checkout_total" in plain
    assert "1 REAL failure(s) (2 quarantined suppressed)" in plain


def test_only_quarantined_failures_pass_with_the_build_verdict_document(api):
    api["pages"]["FAILED"] = [[FLAKY_BY_FINGERPRINT]]

    result = _run("-o", "json")

    assert result.exit_code == 0, result.output
    verdict = json.loads(result.stdout)
    expected_quarantined = [
        {**FLAKY_BY_FINGERPRINT, "matched_by": "fingerprint"},
        {**FLAKY_BY_NAME, "matched_by": "name"},
    ]
    assert verdict == build_verdict([], expected_quarantined, strict=False)
    assert verdict["verdict"] == "pass" and verdict["exit_code"] == 0
    assert verdict["counts"] == {
        "real_failures": 0,
        "quarantined_failures": 2,
        "total_failures": 2,
    }
    manifest_paths = [
        path for _m, path, _p in api["calls"] if path.endswith("/quarantine/manifest")
    ]
    assert manifest_paths == [f"/api/v1/projects/{PROJECT_ID}/quarantine/manifest"], (
        "the project must be inferred from the run when --project is omitted"
    )


def test_strict_blocks_on_quarantined_failures(api):
    api["pages"]["FAILED"] = [[FLAKY_BY_FINGERPRINT]]

    result = _run("--strict", "-o", "json")

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["counts"]["quarantined_failures"] == 2


ERRORS = {
    "auth error on the run": (f"/runs/{RUN_ID}", map_http_error(401, "token expired")),
    "5xx on the second page": ("/tests", map_http_error(503, "no available server")),
    "5xx on the manifest": ("/quarantine/manifest", map_http_error(502, "bad gateway")),
}


@pytest.mark.parametrize("where", list(ERRORS))
def test_an_api_error_fails_closed_with_exit_2(api, monkeypatch, where):
    suffix, exc = ERRORS[where]
    if suffix == "/tests":
        # Let page 1 answer, then refuse page 2: an error mid-pagination must not
        # leave a verdict computed from the pages already read.
        original = client.request

        async def _second_page_fails(method, path, *, params=None, **kw):
            if path.endswith("/tests") and params["page"] == 2:
                raise exc
            return await original(method, path, params=params, **kw)

        monkeypatch.setattr(client, "request", _second_page_fails)
    else:
        api["raise_on"] = (suffix, exc)

    result = _run("-o", "json")

    assert result.exit_code == 2, result.output
    err = _plain(result.stderr)
    assert "ci-verdict failed" in err and "failing CLOSED" in err
    assert result.stdout.strip() == "", (
        "no verdict document may be printed on an infrastructure error"
    )


def test_the_auth_error_keeps_its_cause_in_the_message(api):
    api["raise_on"] = (f"/runs/{RUN_ID}", map_http_error(401, "token expired"))
    assert map_http_error(401).exit_code == EXIT_AUTH

    result = _run()

    assert result.exit_code == 2, result.output
    assert "Authentication failed" in _plain(result.stderr)


@pytest.mark.parametrize("where", ["auth error on the run", "5xx on the manifest"])
def test_fail_open_turns_an_api_error_into_exit_0_with_a_warning(api, where):
    api["raise_on"] = ERRORS[where]

    result = _run("--fail-open")

    assert result.exit_code == 0, result.output
    err = _plain(result.stderr)
    assert "--fail-open is set, so NOT blocking this build" in err
    assert "Failures were NOT checked against the quarantine manifest" in err
    assert "failing CLOSED" not in err


def test_fail_open_does_not_hide_a_real_failure(api):
    result = _run("--fail-open")

    assert result.exit_code == 1, result.output


@pytest.fixture
def fake_clock(monkeypatch):
    """Drive ``_wait_for_completion``'s clock and poll sleep without waiting."""
    clock = {"now": 1000.0, "sleeps": []}

    async def _sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(
        ci_verdict_mod, "time", types.SimpleNamespace(monotonic=lambda: clock["now"])
    )
    monkeypatch.setattr(
        ci_verdict_mod, "asyncio", types.SimpleNamespace(run=asyncio.run, sleep=_sleep)
    )
    return clock


def test_a_run_still_executing_past_the_timeout_exits_2_without_a_verdict(
    api, fake_clock
):
    api["run"]["status"] = "IN_PROGRESS"

    result = _run("--timeout-seconds", "30", "-o", "json")

    assert result.exit_code == 2, result.output
    err = _plain(result.stderr)
    assert "still in progress after 30s" in err and "failing CLOSED" in err
    assert fake_clock["sleeps"] == [5.0] * 6
    assert _test_pages(api, "FAILED") == [], (
        "no verdict may be computed on a partial run"
    )
    assert result.stdout.strip() == ""


def test_a_run_that_finishes_within_the_timeout_is_judged(api, fake_clock):
    api["run_statuses"] = ["IN_PROGRESS", "RUNNING"]  # then COMPLETED

    result = _run("--timeout-seconds", "30", "-o", "json")

    assert result.exit_code == 1, result.output
    assert fake_clock["sleeps"] == [5.0, 5.0]
    assert json.loads(result.stdout)["counts"]["real_failures"] == 1


def test_a_run_without_a_project_and_no_project_option_exits_2(api):
    api["run"].pop("project_id")

    result = _run("-o", "json")

    assert result.exit_code == 2, result.output
    assert "pass --project explicitly" in _plain(result.stderr)
    assert _test_pages(api, "FAILED") == []


def test_an_explicit_project_is_the_one_whose_manifest_is_read(api):
    api["run"].pop("project_id")

    result = _run("--project", OTHER_PROJECT, "-o", "json")

    assert result.exit_code == 1, result.output
    manifest_paths = [
        path for _m, path, _p in api["calls"] if path.endswith("/quarantine/manifest")
    ]
    assert manifest_paths == [f"/api/v1/projects/{OTHER_PROJECT}/quarantine/manifest"]
