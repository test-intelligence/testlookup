"""AIH-07: ``testlookup ai-history`` and ``testlookup compare-ai``.

``ai-history <run>`` reads ``GET /api/v1/runs/{run_id}/ai-history`` (and, with
``--diff A..B``, ``GET /api/v1/runs/{run_id}/ai-history/diff``); ``compare-ai
<run>`` reads ``GET /api/v1/runs/{run_id}/ai-compare`` -- the routes the Analysis
tab's version picker / previous-run card and MCP's ``get_ai_history`` /
``diff_ai_versions`` / ``compare_ai_with_previous`` read. What is pinned here:

* the request: exactly one call (two for ``--diff``: the list resolves the
  version numbers to ids), the REST parameter names, nothing sent for an unused
  option, no diff request for an unknown version number;
* the views (golden, byte for byte): one row per version (``v3  2026-10-14
  10:00  llm llama3.1  NO_GO risk 72  failing 7  [fallback]``) and the compare
  headline the story names (``vs #1233 (checkout · main): verdict GO → NO_GO ·
  new root causes 1 · resolved 1 · 3 tests changed category``), then the
  narrative label -- ``deterministic summary`` offline, never passed off as AI;
* ``--json`` is the REST body verbatim (the review notice goes to stderr);
* the exit codes: the stable HTTP mapping, a usage error is 2 with no request,
  ``--fail-on-verdict-regression`` is 8 only on a worse verdict and 0 (with a
  warning) when it cannot judge -- the verdict never changes the exit code
  without the flag.

``UPDATE_GOLDEN=1`` rewrites the golden files; read the diff before committing.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from testlookup_cli import ai_history_render, client
from testlookup_cli.app import app
from testlookup_cli.commands.ai_history import parse_diff_spec
from testlookup_cli.errors import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    EXIT_PERMISSION,
    EXIT_REGRESSION,
    EXIT_TIMEOUT,
    EXIT_VALIDATION,
    EXIT_VERDICT_REGRESSION,
    CLIError,
    map_http_error,
)
from typer.testing import CliRunner

CLI_DIR = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
RUN = "33333333-3333-4333-8333-333333333333"
BASE = "44444444-4444-4444-8444-444444444444"
PROJECT = "11111111-1111-4111-8111-111111111111"
V1 = "aaaaaaaa-0000-4000-8000-000000000001"
V2 = "aaaaaaaa-0000-4000-8000-000000000002"
V3 = "aaaaaaaa-0000-4000-8000-000000000003"
LIST_PATH = f"/api/v1/runs/{RUN}/ai-history"
DIFF_PATH = f"/api/v1/runs/{RUN}/ai-history/diff"
COMPARE_PATH = f"/api/v1/runs/{RUN}/ai-compare"
REVIEW = {"state": "pending_review", "message": "AI content not yet reviewed by a person."}
DISCLAIMER = "AI-generated: verify before acting."


def _envelope() -> dict:
    return {"requires_human_review": True, "review": dict(REVIEW), "ai_disclaimer": DISCLAIMER,
            "ai_disclaimer_version": "1"}


def _version(n: int, version_id: str, *, mode, model, verdict, risk, failing, generated, fallback=False,
             deterministic=False, trigger="pipeline", latest=False, kind="analysis_digest", headline=None) -> dict:
    """``AIVersionSummary`` (W03 contract section 6.2)."""
    return {
        "version_id": version_id, "kind": kind, "version": n, "trigger": trigger,
        "pipeline_run_id": None if trigger == "backfill" else f"bbbbbbbb-0000-4000-8000-00000000000{n}",
        "generated_at": generated, "created_at": generated,
        "analysis_mode": mode, "llm_provider": "ollama" if model else None, "llm_model": model,
        "fallback_used": fallback, "deterministic": deterministic,
        "verdict": verdict, "risk_score": risk, "failing_tests": failing,
        "categories": {"PRODUCT_BUG": failing} if failing else None, "cluster_count": 2,
        "headline": headline, "content_sha256": f"{n}" * 64,
        "decision_report_ref": {"report_id": f"r{n}", "report_version": n} if verdict else None,
        "summary_version": n, "review": dict(REVIEW), "is_latest": latest,
    }


def _list_body() -> dict:
    """``AIHistoryListResponse``: three digest versions, newest first."""
    return {
        "run_id": RUN, "kind": "analysis_digest", "total": 3, "limit": 20, "truncated": False,
        "latest_version": 3, "live": {"available": True},
        "versions": [
            _version(3, V3, mode="llm", model="llama3.1", verdict="NO_GO", risk=72, failing=7,
                     generated="2026-10-14T10:00:00+00:00", fallback=True, latest=True),
            _version(2, V2, mode="llm", model="llama3.1", verdict="CONDITIONAL_GO", risk=55, failing=6,
                     generated="2026-10-14T11:30:00+02:00"),
            _version(1, V1, mode="rules", model=None, verdict=None, risk=None, failing=8,
                     generated="2026-10-13T08:05:00Z", deterministic=True, trigger="backfill"),
        ],
        **_envelope(),
    }


def _diff(mode: str) -> dict:
    """``AIDigestDiff`` (W03 contract section 3.1)."""
    return {
        "schema_version": 1, "mode": mode, "identical": False, "has_changes": True,
        "provenance": {
            "changed": True, "engine": {"from": "rules", "to": "llm"}, "model": {"from": None, "to": "llama3.1"},
            "fallback_used": None, "deterministic": {"from": True, "to": False},
            "mode_counts": {"from": {"rules": 8}, "to": {"llm": 7}}, "engine_only": False,
            "note": ("Baseline analysed by rules; current by llm. Category changes may reflect the engine, not "
                     "the product.") if mode == "cross_run"
            else "The analysis engine changed between these versions (rules -> llm).",
        },
        "verdict": {"changed": True, "from": "GO", "to": "NO_GO", "direction": "worse",
                    "from_risk": 35, "to_risk": 72, "risk_delta": 37},
        "categories": {"changed": [{"category": "PRODUCT_BUG", "from": 2, "to": 5, "delta": 3},
                                   {"category": "INFRASTRUCTURE", "from": 3, "to": 1, "delta": -2}],
                       "from_total": 8, "to_total": 7},
        "clusters": {
            "added": [{"signature": "timeout payment-gateway", "label": "Timeout in checkout", "size": 4,
                       "member_count": 4}],
            "removed": [{"signature": "db pool", "label": "DB pool exhausted", "size": 6, "member_count": 6}],
            "matched": [{"signature_from": "s", "signature_to": "s", "label_from": "Flaky login",
                         "label_to": "Flaky login", "size_from": 3, "size_to": 5, "delta": 2,
                         "matched_by": "signature", "jaccard": None, "resized": True}],
            "counts": {"added": 1, "removed": 1, "matched": 1, "resized": 1},
        },
        "tests": {
            "added": [{"test_fingerprint": "fp9", "test_id": "t9", "test_name": "checkout::test_new",
                       "suite_name": "checkout", "category": "PRODUCT_BUG", "confidence": 80}],
            "removed": [],
            "changed": [
                {"test_fingerprint": "fp1", "test_id": "t1", "from_test_id": "f1", "test_name": "checkout::test_pay",
                 "suite_name": "checkout", "kinds": ["category", "confidence"], "material": True,
                 "category": {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"},
                 "confidence": {"from": 40, "to": 85, "delta": 45}, "flaky": None,
                 "root_cause_changed": True, "root_cause_excerpt": {"from": "a", "to": "b"}},
                {"test_fingerprint": "fp2", "test_id": "t2", "from_test_id": "f2",
                 "test_name": "[bold red]checkout::test_refund[/]", "suite_name": "checkout",
                 "kinds": ["category"], "material": True,
                 "category": {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"}, "confidence": None, "flaky": None,
                 "root_cause_changed": False, "root_cause_excerpt": None},
                {"test_fingerprint": "fp3", "test_id": "t3", "from_test_id": "f3", "test_name": "cart::test_add",
                 "suite_name": "cart", "kinds": ["category"], "material": True,
                 "category": {"from": None, "to": "TEST_DATA"}, "confidence": None, "flaky": None,
                 "root_cause_changed": False, "root_cause_excerpt": None},
                {"test_fingerprint": "fp4", "test_id": "t4", "from_test_id": "f4", "test_name": "cart::test_rm",
                 "suite_name": "cart", "kinds": ["root_cause"], "material": False, "category": None,
                 "confidence": None, "flaky": None, "root_cause_changed": True,
                 "root_cause_excerpt": {"from": "x", "to": "y"}},
            ],
            "persisting_unchanged": 5,
            "counts": {"added": 1, "removed": 0, "changed": 4, "material_changed": 3},
            "truncated": False,
        },
        "actions": {"added": ["Fix the gateway timeout"], "removed": []},
    }


def _ref(ref: str, version, *, mode, model, source="stored") -> dict:
    return {"ref": ref, "source": source, "version": version, "kind": "analysis_digest",
            "generated_at": "2026-10-14T10:00:00+00:00", "pipeline_run_id": None, "content_sha256": None,
            "analysis_mode": mode, "llm_model": model, "run_id": RUN, "build_number": "1234"}


def _diff_body() -> dict:
    """``AIDigestDiffResponse`` (``from``/``to`` are the JSON keys)."""
    return {"run_id": RUN, "from": _ref(V1, 1, mode="rules", model=None),
            "to": _ref(V3, 3, mode="llm", model="llama3.1"), "diff": _diff("versions"), **_envelope()}


def _compare_body() -> dict:
    """``AICompareResponse`` (W03 contract section 6.2), the deterministic narrative."""
    side = {"verdict": "GO", "risk_score": 35, "composite_risk": None, "analysed_tests": 8,
            "categories": {"INFRASTRUCTURE": 3}, "avg_confidence": 60.0, "top_clusters": [], "engine": "rules",
            "llm_provider": None, "llm_model": None, "deterministic": True, "fallback_used": False,
            "decision_report_version": None, "generated_at": None}
    return {
        "run_id": RUN, "project_id": PROJECT,
        "baseline": {"run_id": BASE, "build_number": "b-1233", "run_seq": 1233, "branch": "main",
                     "primary_suite_name": "checkout", "status": "PASSED",
                     "created_at": "2026-10-13T10:00:00+00:00", "finished_at": "2026-10-13T10:10:00+00:00",
                     "selection": "default",
                     "selection_reason": "Same suite and branch, newest completed run before this one.",
                     "branch_mismatch": False},
        "reason": None, "message": None,
        "current_ref": _ref("live", None, mode="llm", model="llama3.1", source="live"),
        "baseline_ref": _ref("live", None, mode="rules", model=None, source="live"),
        "sides": {"baseline": side, "current": {**side, "verdict": "NO_GO", "engine": "llm"}},
        "diff": _diff("cross_run"),
        "narrative": {
            "status": "deterministic", "source": "deterministic", "label": "deterministic summary",
            "fallback_used": True,
            "executive_summary": "The AI verdict went from GO to NO_GO: 1 new root cause, 1 resolved.",
            "ai_changes": [
                "Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.",
                "Verdict GO -> NO_GO (risk 35 -> 72)",
                "New root cause: Timeout in checkout (4 tests)",
                "Resolved: DB pool exhausted (6 tests)",
                "checkout::test_pay: INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)",
            ],
            "risk_level": None, "prompt_version": "run_compare_v2", "model_name": None, "generated_at": None,
            "report_status": "absent",
        },
        **_envelope(),
    }


def _no_baseline_body() -> dict:
    body = _compare_body()
    body.update({"baseline": None, "reason": "no_previous_run",
                 "message": 'No earlier completed run of "checkout" on branch "main" found.',
                 "current_ref": None, "baseline_ref": None, "sides": None, "diff": None, "narrative": None})
    return body


@pytest.fixture
def api(monkeypatch):
    """``client.request`` replaced: answers by path, records every call."""
    calls: list[dict] = []
    state: dict = {"bodies": {LIST_PATH: _list_body(), DIFF_PATH: _diff_body(), COMPARE_PATH: _compare_body()},
                   "error": None}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        calls.append({"method": method, "path": path, "params": params})
        if state["error"] is not None:
            raise state["error"]
        return copy.deepcopy(state["bodies"][path])

    monkeypatch.setattr(client, "request", _request)
    return calls, state


def _run(*args, env=None):
    return CliRunner().invoke(app, list(args), env=env)


def _golden(name: str, actual: bytes) -> bytes:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(actual)
    return path.read_bytes()


def _trimmed(stdout: str) -> bytes:
    return "".join(line.rstrip() + "\n" for line in stdout.splitlines()).encode("utf-8")


# ── ai-history: the request ─────────────────────────────────────────────────


def test_ai_history_asks_for_the_digest_versions_in_one_request(api):
    calls, _ = api
    result = _run("ai-history", RUN)
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": LIST_PATH, "params": {"kind": "analysis_digest", "limit": 20}}]


@pytest.mark.parametrize(("argv", "params"), [
    (("--kind", "summary", "-n", "5"), {"kind": "run_summary", "limit": 5}),
    (("--kind", "digest", "--limit", "100"), {"kind": "analysis_digest", "limit": 100}),
    (("--kind", "run_summary"), {"kind": "run_summary", "limit": 20}),
])
def test_ai_history_sends_the_rest_kind_and_limit(api, argv, params):
    calls, _ = api
    assert _run("ai-history", RUN, *argv).exit_code == 0
    assert calls[0]["params"] == params


def test_intelligence_history_is_the_same_command(api):
    calls, _ = api
    result = _run("intelligence", "history", RUN, "--json")
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": LIST_PATH, "params": {"kind": "analysis_digest", "limit": 20}}]
    assert json.loads(result.stdout) == _list_body()


def test_ai_history_json_is_the_rest_body_and_the_review_goes_to_stderr(api):
    for argv in (("--json",), ("-o", "json")):
        result = _run("ai-history", RUN, *argv)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == _list_body()
        assert "Review state: pending_review" in result.stderr


@pytest.mark.parametrize("argv", [
    ("--kind", "everything"), ("-n", "0"), ("-n", "101"), ("-o", "csv"),
    ("--diff", "3..1x"), ("--diff", "latest..1"), ("--diff", "a..b"), ("--diff", "0..2"), ("--diff", "1-3"),
    ("--diff", "1..3", "--kind", "summary"),
])
def test_a_usage_error_exits_2_without_a_request(api, argv):
    calls, _ = api
    result = _run("ai-history", RUN, *argv)
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert calls == []


# ── ai-history: the view ────────────────────────────────────────────────────


def test_one_row_per_version_names_the_engine_the_verdict_and_the_failing_tests(api):
    result = _run("ai-history", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert any(line.startswith("v3  2026-10-14 10:00  llm llama3.1  NO_GO risk 72") and
               "failing 7  [fallback] [latest]" in line for line in lines), result.stdout
    v2 = next(line for line in lines if line.startswith("v2"))
    assert "2026-10-14 09:30" in v2, "times are printed in UTC"
    v1 = next(line for line in lines if line.startswith("v1"))
    assert "rules" in v1 and "[deterministic] [backfilled]" in v1
    assert " - " in v1 and "risk 0" not in v1, "no verdict is '-', never a made-up risk"


def test_ai_history_table_matches_the_golden(api):
    result = _run("ai-history", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    actual = _trimmed(result.stdout)
    assert actual == _golden("ai_history_table.txt", actual)


def test_an_empty_history_says_so(api):
    _, state = api
    state["bodies"][LIST_PATH] = {**_list_body(), "total": 0, "latest_version": None, "versions": []}
    result = _run("ai-history", RUN)
    assert result.exit_code == 0, result.output
    assert "No AI versions stored for this run yet." in result.stdout
    assert "has a current AI analysis" in result.stdout


def test_a_cut_list_says_so(api):
    _, state = api
    state["bodies"][LIST_PATH] = {**_list_body(), "total": 30, "truncated": True}
    result = _run("ai-history", RUN)
    assert "3 of 30 versions shown" in result.stdout


# ── ai-history --diff ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("spec", "to"), [("1..3", V3), ("v1..v2", V2), ("1..latest", "latest"),
                                          ("v1..LIVE", "live")])
def test_diff_resolves_the_numbers_through_the_list_then_diffs_once(api, spec, to):
    calls, _ = api
    result = _run("ai-history", RUN, "--diff", spec)
    assert result.exit_code == 0, result.output
    assert calls == [
        {"method": "GET", "path": LIST_PATH, "params": {"kind": "analysis_digest", "limit": 100}},
        {"method": "GET", "path": DIFF_PATH, "params": {"from": V1, "to": to}},
    ]


@pytest.mark.parametrize("spec", ["9..latest", "1..9"])
def test_an_unknown_version_number_exits_2_without_a_diff_request(api, spec):
    calls, _ = api
    result = _run("ai-history", RUN, "--diff", spec)
    assert result.exit_code == EXIT_VALIDATION
    assert [c["path"] for c in calls] == [LIST_PATH], "no diff request for a version that does not exist"
    assert "v9 is not a stored AI version of this run (stored: v1, v2, v3)" in result.stderr


def test_the_diff_puts_provenance_first(api):
    result = _run("ai-history", RUN, "--diff", "1..3", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines[0] == "AI diff · run #1234 · v1 → v3"
    assert lines[1] == "Engines: rules → llm llama3.1"
    assert lines[2].startswith("Provenance changed: engine rules → llm · model - → llama3.1")
    assert lines[3] == "  The analysis engine changed between these versions (rules -> llm)."
    assert lines[4] == "Verdict GO → NO_GO (worse) · risk 35 → 72 (+37)"
    out = result.stdout
    assert "Categories: PRODUCT_BUG 2 → 5 (+3) · INFRASTRUCTURE 3 → 1 (-2)" in out
    assert "  ~ checkout::test_pay: INFRASTRUCTURE → PRODUCT_BUG, confidence 40% → 85%" in out
    assert "  ~ cart::test_add: - → TEST_DATA" in out
    assert "  ~ cart::test_rm: root cause reworded" in out
    assert "Tests: 1 added · 0 removed · 4 changed (3 material) · 5 unchanged" in out
    assert "Added clusters (1):" in out and "Resized clusters (1):" in out


def test_a_test_name_is_text_never_rich_markup(api):
    result = _run("ai-history", RUN, "--diff", "1..3", env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    assert "[bold red]checkout::test_refund[/]" in result.stdout


def test_diff_json_is_the_rest_body(api):
    result = _run("ai-history", RUN, "--diff", "1..3", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _diff_body()


def test_identical_versions_say_so(api):
    _, state = api
    body = _diff_body()
    body["diff"] = {"schema_version": 1, "mode": "versions", "identical": True, "has_changes": False}
    state["bodies"][DIFF_PATH] = body
    result = _run("ai-history", RUN, "--diff", "3..latest")
    assert "Identical: both sides have the same AI content." in result.stdout


def test_parse_diff_spec():
    assert parse_diff_spec("1..3") == (1, 3)
    assert parse_diff_spec(" v2 .. v5 ") == (2, 5)
    assert parse_diff_spec("1..Latest") == (1, "latest")
    assert parse_diff_spec("4..live") == (4, "live")
    for bad in ("", "1", "1..", "..2", "latest..2", "1...2", "v0..1", "1..v0", "1..x"):
        assert parse_diff_spec(bad) is None, bad


# ── compare-ai: the request ─────────────────────────────────────────────────


def test_compare_ai_sends_nothing_for_an_unused_option(api):
    calls, _ = api
    result = _run("compare-ai", RUN)
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": COMPARE_PATH, "params": {}}]


def test_compare_ai_sends_the_rest_parameter_names(api):
    calls, _ = api
    result = _run("compare-ai", RUN, "--baseline", BASE, "--suite", "checkout", "--json")
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": COMPARE_PATH,
                      "params": {"baseline_run_id": BASE, "suite_name": "checkout"}}]
    assert json.loads(result.stdout) == _compare_body()
    assert "Review state: pending_review" in result.stderr


@pytest.mark.parametrize("argv", [("--baseline", RUN), ("-o", "yaml")])
def test_compare_ai_usage_error_exits_2_without_a_request(api, argv):
    calls, _ = api
    result = _run("compare-ai", RUN, *argv)
    assert result.exit_code == EXIT_VALIDATION
    assert calls == []


# ── compare-ai: the view ────────────────────────────────────────────────────


def test_compare_ai_prints_the_storys_headline(api):
    result = _run("compare-ai", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == (
        "vs #1233 (checkout · main): verdict GO → NO_GO · new root causes 1 · resolved 1 · "
        "3 tests changed category"
    )


def test_compare_ai_table_matches_the_golden(api):
    result = _run("compare-ai", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    actual = _trimmed(result.stdout)
    assert actual == _golden("compare_ai_table.txt", actual)


def test_offline_the_summary_is_labelled_deterministic_never_ai(api):
    result = _run("compare-ai", RUN)
    second = result.stdout.splitlines()[1]
    assert second.startswith("deterministic summary: The AI verdict went from GO to NO_GO")
    assert "AI narrative" not in result.stdout
    assert "  - Verdict GO -> NO_GO (risk 35 -> 72)" in result.stdout


def test_an_llm_narrative_is_labelled_as_such(api):
    _, state = api
    body = _compare_body()
    body["narrative"].update({"status": "ready", "source": "llm", "label": "AI narrative", "fallback_used": False,
                              "model_name": "llama3.1", "report_status": "ready"})
    state["bodies"][COMPARE_PATH] = body
    result = _run("compare-ai", RUN)
    assert result.stdout.splitlines()[1].startswith("AI narrative: ")


def test_an_unchanged_verdict_says_unchanged(api):
    _, state = api
    body = _compare_body()
    body["diff"]["verdict"] = {"changed": False, "from": "GO", "to": "GO", "direction": "same",
                               "from_risk": 35, "to_risk": 30, "risk_delta": -5}
    state["bodies"][COMPARE_PATH] = body
    result = _run("compare-ai", RUN)
    assert result.stdout.splitlines()[0].startswith("vs #1233 (checkout · main): verdict GO (unchanged) · ")


def test_no_previous_run_says_why(api):
    _, state = api
    state["bodies"][COMPARE_PATH] = _no_baseline_body()
    result = _run("compare-ai", RUN)
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        'no previous run: No earlier completed run of "checkout" on branch "main" found.'
    ]


def test_a_baseline_without_analysis_is_not_no_change(api):
    _, state = api
    body = _compare_body()
    body.update({"reason": "baseline_not_analysed", "message": "#1233 has no AI analysis to compare.",
                 "diff": None, "narrative": None})
    state["bodies"][COMPARE_PATH] = body
    result = _run("compare-ai", RUN)
    assert result.stdout.splitlines()[0] == (
        "vs #1233 (checkout · main): not compared: #1233 has no AI analysis to compare."
    )
    assert "unchanged" not in result.stdout


# ── compare-ai --fail-on-verdict-regression ─────────────────────────────────


def test_a_worse_verdict_exits_8_with_the_flag(api):
    result = _run("compare-ai", RUN, "--fail-on-verdict-regression")
    assert result.exit_code == EXIT_VERDICT_REGRESSION == 8, result.output
    assert "got worse" in result.stderr
    assert result.stdout.startswith("vs #1233"), "the view is still printed"


def test_the_flag_also_gates_the_json_output(api):
    result = _run("compare-ai", RUN, "--json", "--fail-on-verdict-regression")
    assert result.exit_code == EXIT_VERDICT_REGRESSION
    assert json.loads(result.stdout)["diff"]["verdict"]["direction"] == "worse"


def test_without_the_flag_the_verdict_never_changes_the_exit_code(api):
    assert _run("compare-ai", RUN).exit_code == 0
    assert _run("compare-ai", RUN, "--json").exit_code == 0


@pytest.mark.parametrize("direction", ["better", "same"])
def test_a_verdict_that_did_not_get_worse_exits_0(api, direction):
    _, state = api
    body = _compare_body()
    body["diff"]["verdict"]["direction"] = direction
    state["bodies"][COMPARE_PATH] = body
    result = _run("compare-ai", RUN, "--fail-on-verdict-regression")
    assert result.exit_code == 0, result.output
    assert "could not be compared" not in result.stderr


@pytest.mark.parametrize("mutate", [
    lambda body: body.update(_no_baseline_body()),
    lambda body: body.update({"reason": "current_not_analysed", "message": "This run has no AI analysis.",
                              "diff": None, "narrative": None}),
    lambda body: body["diff"]["verdict"].update({"from": None, "direction": "unknown"}),
])
def test_an_unjudgeable_verdict_does_not_fail_ci_but_says_so(api, mutate):
    _, state = api
    body = _compare_body()
    mutate(body)
    state["bodies"][COMPARE_PATH] = body
    result = _run("compare-ai", RUN, "--fail-on-verdict-regression")
    assert result.exit_code == 0, result.output
    assert "could not be compared with a baseline" in result.stderr


def test_verdict_direction_reads_only_a_known_direction():
    assert ai_history_render.verdict_direction(_compare_body()) == "worse"
    assert ai_history_render.verdict_direction(_no_baseline_body()) is None
    assert ai_history_render.verdict_direction({"diff": {"verdict": {"direction": "unknown"}}}) is None
    assert ai_history_render.verdict_direction(None) is None


def test_the_verdict_code_is_not_any_other_code():
    assert EXIT_VERDICT_REGRESSION not in {
        0, 1, EXIT_VALIDATION, EXIT_AUTH, EXIT_PERMISSION, EXIT_NOT_FOUND, EXIT_TIMEOUT, EXIT_REGRESSION,
    }


# ── exit codes (the stable HTTP mapping) ────────────────────────────────────


@pytest.mark.parametrize("argv", [
    ("ai-history", RUN),
    ("ai-history", RUN, "--diff", "1..3"),
    ("intelligence", "history", RUN),
    ("compare-ai", RUN, "--fail-on-verdict-regression"),
])
@pytest.mark.parametrize(("status", "code"), [
    (404, EXIT_NOT_FOUND), (403, EXIT_PERMISSION), (401, EXIT_AUTH), (422, EXIT_VALIDATION), (503, 1),
])
def test_http_errors_keep_the_stable_exit_codes(api, argv, status, code):
    _, state = api
    state["error"] = map_http_error(status, "sentinel")
    result = _run(*argv)
    assert result.exit_code == code, result.output
    assert "sentinel" in result.stderr


def test_a_client_error_code_is_preserved(api):
    _, state = api
    state["error"] = CLIError("sentinel", EXIT_NOT_FOUND)
    result = _run("compare-ai", RUN, "--fail-on-verdict-regression")
    assert result.exit_code == EXIT_NOT_FOUND, "a 404 is never a verdict regression"


# ── the wire ────────────────────────────────────────────────────────────────


def test_the_wire_sends_the_rest_names_and_the_api_key(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _list_body() if request.url.path == LIST_PATH else _diff_body()
        return httpx.Response(200, json=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(client, "get_profile", lambda _name=None: {
        "url": "http://testserver", "auth_type": "api_key", "api_key": "tl_test_key",
    })
    result = _run("ai-history", RUN, "--diff", "v1..live", "-o", "json")
    assert result.exit_code == 0, result.output
    assert [r.url.path for r in seen] == [LIST_PATH, DIFF_PATH]
    assert dict(seen[1].url.params.multi_items()) == {"from": V1, "to": "live"}
    assert all(r.headers["x-api-key"] == "tl_test_key" for r in seen)


# ── a console that cannot encode the glyphs ─────────────────────────────────

_SNIPPET = """
import json, sys
from testlookup_cli import client
from testlookup_cli.app import app
body = json.loads(open(sys.argv[1], encoding="utf-8").read())
async def _request(*args, **kwargs):
    return body
client.request = _request
app(sys.argv[2:])
"""


def _console(tmp_path, encoding: str, body: dict, *argv: str) -> subprocess.CompletedProcess:
    body_file = tmp_path / "body.json"
    body_file.write_text(json.dumps(body), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET, str(body_file), *argv],
        capture_output=True, cwd=str(CLI_DIR), check=False,
        env={**os.environ, "PYTHONIOENCODING": encoding, "COLUMNS": "120"},
    )


def test_a_cp1252_console_gets_an_ascii_arrow_not_a_crash(tmp_path):
    body = _compare_body()
    body["baseline"]["primary_suite_name"] = "決済"
    proc = _console(tmp_path, "cp1252", body, "compare-ai", RUN)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err[-800:]
    assert "UnicodeEncodeError" not in err
    out = proc.stdout.decode("cp1252")
    assert out.splitlines()[0].startswith("vs #1233 (?? · main): verdict GO -> NO_GO · new root causes 1"), out


def test_an_ascii_console_gets_ascii_separators(tmp_path):
    proc = _console(tmp_path, "ascii", _compare_body(), "compare-ai", RUN)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
    assert proc.stdout.splitlines()[0] == (
        b"vs #1233 (checkout | main): verdict GO -> NO_GO | new root causes 1 | resolved 1 | "
        b"3 tests changed category"
    )
