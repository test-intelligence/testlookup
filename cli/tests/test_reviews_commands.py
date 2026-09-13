"""E8.4 (slice 4): the CLI surface of the human review gate.

* ``testlookup reviews list/accept/reject`` wrap the reviews API.
* Accept and reject are refused up front on an API-key profile: the server
  refuses API keys too, and the CLI should say why instead of relaying a 403.
* ``intelligence show`` prints the report's review state and AI disclaimer to
  stderr, so ``--output json`` stays pipeable. A payload without a review block
  prints ``unknown``, never nothing.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.commands import reviews as reviews_cmd

DISCLAIMER = "AI-generated content. Verify before acting."


@pytest.fixture
def api(monkeypatch):
    calls: list[dict] = []
    state = {"response": [], "auth_type": "jwt"}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        calls.append({"method": method, "path": path, "params": params, "json": json_body})
        return state["response"]

    def _profile(_name=None):
        if state["auth_type"] == "api_key":
            return {"auth_type": "api_key", "api_key": "tl_k"}
        return {"auth_type": "jwt", "access_token": "t"}

    monkeypatch.setattr(client, "request", _request)
    monkeypatch.setattr(reviews_cmd, "get_profile", _profile)
    return calls, state


def _run(*args):
    return CliRunner().invoke(app, list(args))


def _flat(text):
    return " ".join(text.split())


# ── reviews list ─────────────────────────────────────────────────────────────


def test_list_reads_the_open_queue_by_default(api):
    calls, state = api
    state["response"] = [{"id": "r1", "kind": "run_report", "state": "pending_review"}]
    result = _run("reviews", "list", "proj-1", "--output", "json")
    assert result.exit_code == 0, result.output
    assert calls == [{
        "method": "GET", "path": "/api/v1/projects/proj-1/reviews",
        "params": {"limit": 50, "state": "pending_review"}, "json": None,
    }]
    assert json.loads(result.stdout)[0]["id"] == "r1"


def test_list_all_sends_no_state_filter(api):
    calls, _ = api
    assert _run("reviews", "list", "proj-1", "--state", "all").exit_code == 0
    assert calls[0]["params"] == {"limit": 50}


def test_list_rejects_an_unknown_state_without_calling_the_api(api):
    calls, _ = api
    result = _run("reviews", "list", "proj-1", "--state", "approved")
    assert result.exit_code == 1 and calls == []


# ── accept / reject ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    ("reviews", "accept", "r1"),
    ("reviews", "reject", "r1", "--reason", "unsupported_claim"),
])
def test_an_api_key_profile_cannot_settle_a_review(api, args):
    calls, state = api
    state["auth_type"] = "api_key"
    result = _run(*args)
    assert result.exit_code == 1
    assert calls == [], "the CLI must refuse before sending anything"
    assert "API key" in _flat(result.stderr)


def test_accept_posts_the_notes_on_a_user_login(api):
    calls, state = api
    state["response"] = {"id": "r1", "state": "accepted"}
    result = _run("reviews", "accept", "r1", "--notes", "checked the logs")
    assert result.exit_code == 0, result.output
    assert calls == [{
        "method": "POST", "path": "/api/v1/reviews/r1/accept", "params": None,
        "json": {"notes": "checked the logs"},
    }]


def test_reject_posts_the_reason_code(api):
    calls, state = api
    state["response"] = {"id": "r1", "state": "rejected"}
    result = _run("reviews", "reject", "r1", "--reason", "stale_data")
    assert result.exit_code == 0, result.output
    assert calls[0]["path"] == "/api/v1/reviews/r1/reject"
    assert calls[0]["json"] == {"reason_code": "stale_data"}


def test_reject_refuses_an_unknown_reason_without_calling_the_api(api):
    calls, _ = api
    result = _run("reviews", "reject", "r1", "--reason", "looks_wrong")
    assert result.exit_code == 1 and calls == []


# ── intelligence show carries the review notice ──────────────────────────────


def test_intelligence_json_keeps_stdout_clean_and_puts_the_notice_on_stderr(api):
    _, state = api
    state["response"] = {
        "run": {"build_number": "42"},
        "review": {"state": "pending_review", "message": "Human review required before use."},
        "ai_disclaimer": DISCLAIMER,
    }
    result = _run("intelligence", "show", "run-1", "--output", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run"]["build_number"] == "42"
    err = _flat(result.stderr)
    assert "Review state: pending_review" in err
    assert DISCLAIMER in err


def test_intelligence_without_a_review_block_says_unknown(api):
    _, state = api
    state["response"] = {"run": {"build_number": "42"}}
    result = _run("intelligence", "show", "run-1")
    assert result.exit_code == 0, result.output
    assert "Review state: unknown" in _flat(result.stderr)
