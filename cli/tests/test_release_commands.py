"""W18 DEV-01: ``testlookup release gate`` is a CI contract; the parity groups call the MCP tools' endpoints.

Frozen here (README: "Release gate exit codes"):
  GO -> 0 · CONDITIONAL_GO -> 0 (1 with --fail-on CONDITIONAL_GO) · NO_GO -> 1 ·
  NOT_EVALUATED or never evaluated (404) -> 3 · infrastructure error -> 2 (fail closed),
  0 with --fail-open.
"""
from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.errors import map_http_error

P1 = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"
RELEASES = {"items": [{"id": R1, "name": "Checkout 2026.10", "version": "2026.10", "status": "active",
                       "project_id": P1, "project_name": "Checkout Service", "gate": {"verdict": "GO"}}]}


def _gate(verdict):
    return {"release_id": R1, "verdict": verdict, "blocking_reasons": ["test_pay (checkout-e2e) is FAILED"] if verdict == "NO_GO" else [],
            "blocking_total": 1 if verdict == "NO_GO" else 0, "decided_at": "2026-10-26T10:00:00",
            "scorecard": {"pass_rate": 97.5, "evidence_count": 120, "run_count": 3}}


@pytest.fixture
def api(monkeypatch):
    state = {"gate": _gate("GO"), "calls": [], "fail": None}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        state["calls"].append((method, path, params, json_body))
        if state["fail"] and path.startswith("/api/v1/releases/"):
            raise state["fail"]
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Checkout Service"}]
        if path == "/api/v1/releases":
            return RELEASES
        if path == f"/api/v1/releases/{R1}/gate":
            if state["gate"] is None:
                raise map_http_error(404, "This release has not been evaluated yet")
            return state["gate"]
        if path == f"/api/v1/releases/{R1}/gate/evaluate":
            return {**_gate("NO_GO"), "recorded": True}
        if path == "/api/v1/quarantine":
            return [{"id": "q1", "test_name": "test_flaky", "status": "QUARANTINED", "flip_rate": 0.4, "expires_at": "2026-11-01"}]
        if path.startswith("/api/v1/quarantine/"):
            return {"id": "q1", "status": "RELEASED"}
        if path == f"/api/v1/releases/{R1}/compliance-packs":
            return [{"id": "pk1", "generated_at": "2026-10-25"}, {"id": "pk2", "generated_at": "2026-10-26"}]
        if path == f"/api/v1/runs/{R1}/investigations":
            return {"id": "inv1", "status": "queued"}
        raise AssertionError(path)

    async def _download(path, *, profile_name=None, params=None, timeout=60.0):
        state["calls"].append(("DOWNLOAD", path, None, None))
        return b"PK\x03\x04zip"

    monkeypatch.setattr(client, "request", _request)
    monkeypatch.setattr(client, "download", _download)
    return state


def _run(*args):
    return CliRunner().invoke(app, list(args))


@pytest.mark.parametrize("verdict,fail_on,code", [
    ("GO", "NO_GO", 0),
    ("CONDITIONAL_GO", "NO_GO", 0),
    ("CONDITIONAL_GO", "CONDITIONAL_GO", 1),
    ("NO_GO", "NO_GO", 1),
    ("NOT_EVALUATED", "NO_GO", 3),
])
def test_the_exit_code_per_verdict(api, verdict, fail_on, code):
    api["gate"] = _gate(verdict)
    result = _run("release", "gate", "Checkout 2026.10", "--project", "Checkout Service", "--fail-on", fail_on)
    assert result.exit_code == code, result.output
    assert f"Release gate: {verdict}" in result.output


def test_never_evaluated_is_3_and_says_how_to_evaluate(api):
    api["gate"] = None
    result = _run("release", "gate", R1)
    assert result.exit_code == 3 and "has not been evaluated yet" in result.output


def test_an_infrastructure_error_fails_closed_unless_fail_open(api):
    api["fail"] = httpx.ConnectError("refused")
    assert _run("release", "gate", R1).exit_code == 2
    result = _run("release", "gate", R1, "--fail-open")
    assert result.exit_code == 0 and "--fail-open" in result.output
    api["fail"] = map_http_error(403, "You do not have access to this release")
    assert _run("release", "gate", R1).exit_code == 2


def test_an_ambiguous_or_unknown_release_name_is_an_infra_error_not_a_pass(api):
    assert _run("release", "gate", "Checkout 2026.11").exit_code == 2


def test_evaluate_records_a_fresh_verdict(api):
    result = _run("release", "gate", R1, "--evaluate", "--json")
    assert result.exit_code == 1
    assert ("POST", f"/api/v1/releases/{R1}/gate/evaluate", None, None) in api["calls"]
    body = json.loads(result.output)
    assert body["release_id"] == R1 and body["gate"]["verdict"] == "NO_GO" and body["gate"]["recorded"] is True


def test_the_blockers_are_printed(api):
    api["gate"] = _gate("NO_GO")
    result = _run("release", "gate", R1)
    assert "blockers  1" in result.output and "test_pay (checkout-e2e) is FAILED" in result.output


def test_release_list_shows_the_gate(api):
    assert _run("release", "list", "--project", "Checkout Service").exit_code == 0
    result = _run("release", "list", "--project", "Checkout Service", "-o", "json")
    assert result.exit_code == 0 and json.loads(result.output)[0]["gate"] == {"verdict": "GO"}


def test_quarantine_list_and_release_need_a_reason(api):
    result = _run("quarantine", "list", "--project", "Checkout Service", "--live-only")
    assert result.exit_code == 0 and "test_flaky" in result.output
    q = [c for c in api["calls"] if c[1] == "/api/v1/quarantine"][0]
    assert q[2] == {"limit": 50, "live_only": True, "project_id": P1}
    assert _run("quarantine", "release", "q1").exit_code == 2  # --reason is required
    result = _run("quarantine", "release", "q1", "--reason", "fix merged in #123")
    assert result.exit_code == 0
    assert ("POST", "/api/v1/quarantine/q1/release", None, {"notes": "fix merged in #123"}) in api["calls"]


def test_defects_need_exactly_one_signature(api):
    assert _run("defects", "jira", "preview", "--project", P1).exit_code == 2
    assert _run("defects", "jira", "preview", "--project", P1, "--fingerprint", "a", "--cluster", "b").exit_code == 2


def test_compliance_download_saves_the_newest_pack(api, tmp_path):
    out = tmp_path / "pack.zip"
    result = _run("compliance-pack", "download", "Checkout 2026.10", "-o", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"PK\x03\x04zip"
    assert ("DOWNLOAD", "/api/v1/compliance-packs/pk2/download", None, None) in api["calls"]


def test_investigations_start(api):
    result = _run("investigations", "start", R1)
    assert result.exit_code == 0 and "inv1" in result.output
