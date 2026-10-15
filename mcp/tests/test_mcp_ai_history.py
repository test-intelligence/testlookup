"""AIH-07: ``get_ai_history``, ``diff_ai_versions`` and ``compare_ai_with_previous``
over MCP.

"What changed in the AI analysis since the previous checkout run?" had no MCP
answer: nothing listed a run's AI versions or compared AI analysis across runs.
These three read the W03 routes the Analysis tab, the Run Compare page and the
CLI's ``ai-history`` / ``compare-ai`` read, and return compact markdown beside
the REST body. What is pinned here:

1. The contracts (name, description, input schema) against committed
   snapshots. ``UPDATE_SNAPSHOTS=1`` rewrites them.
2. The wire: the REST paths and parameter names, ``None`` never sent, the
   caller's own bearer forwarded; the paths are routes ``routers/ai_history.py``
   declares; a version number that is not stored is answered without a diff call.
3. Honesty: the offline summary is labelled "deterministic summary" (never
   passed off as AI); provenance comes first; "not analysed" is not "no change";
   a hostile name cannot break a table.
4. Errors: a structured result (404 for a run the caller cannot read, 503 when
   the store is down, a connection failure).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
REPO_ROOT = MCP_DIR.parent
sys.path.insert(0, str(MCP_DIR))

import ai_history_format  # noqa: E402
import client  # noqa: E402
from tools import intelligence  # noqa: E402

SNAPSHOTS = Path(__file__).parent / "snapshots"
BACKEND_ROUTER = REPO_ROOT / "backend" / "app" / "routers" / "ai_history.py"
TOOLS = ("get_ai_history", "diff_ai_versions", "compare_ai_with_previous")
RUN = "33333333-3333-4333-8333-333333333333"
BASE = "44444444-4444-4444-8444-444444444444"
PROJECT = "11111111-1111-4111-8111-111111111111"
V1 = "aaaaaaaa-0000-4000-8000-000000000001"
V2 = "aaaaaaaa-0000-4000-8000-000000000002"
V3 = "aaaaaaaa-0000-4000-8000-000000000003"
LIST_PATH = f"/api/v1/runs/{RUN}/ai-history"
DIFF_PATH = f"/api/v1/runs/{RUN}/ai-history/diff"
COMPARE_PATH = f"/api/v1/runs/{RUN}/ai-compare"
HOSTILE = "checkout|`rm -rf`\nsecond line"


def _envelope() -> dict:
    return {"requires_human_review": True,
            "review": {"state": "pending_review", "message": "AI content not yet reviewed."},
            "ai_disclaimer": "AI-generated: verify before acting.", "ai_disclaimer_version": "1"}


def _version(n, version_id, *, mode, model, verdict, risk, failing, fallback=False, deterministic=False,
             trigger="pipeline", latest=False) -> dict:
    return {
        "version_id": version_id, "kind": "analysis_digest", "version": n, "trigger": trigger,
        "pipeline_run_id": None, "generated_at": f"2026-10-1{n}T10:00:00+00:00",
        "created_at": f"2026-10-1{n}T10:00:00+00:00", "analysis_mode": mode,
        "llm_provider": "ollama" if model else None, "llm_model": model, "fallback_used": fallback,
        "deterministic": deterministic, "verdict": verdict, "risk_score": risk, "failing_tests": failing,
        "categories": None, "cluster_count": None, "headline": None, "content_sha256": f"{n}" * 64,
        "decision_report_ref": {"report_id": "r", "report_version": n} if verdict else None,
        "summary_version": None, "review": None, "is_latest": latest,
    }


def _list_body() -> dict:
    return {
        "run_id": RUN, "kind": "analysis_digest", "total": 3, "limit": 20, "truncated": False,
        "latest_version": 3, "live": {"available": True},
        "versions": [
            _version(3, V3, mode="llm", model="llama3.1", verdict="NO_GO", risk=72, failing=7, fallback=True,
                     latest=True),
            _version(2, V2, mode="llm", model="llama3.1", verdict="CONDITIONAL_GO", risk=55, failing=6),
            _version(1, V1, mode="rules", model=None, verdict=None, risk=None, failing=8, deterministic=True,
                     trigger="backfill"),
        ],
        **_envelope(),
    }


def _diff(mode: str) -> dict:
    return {
        "schema_version": 1, "mode": mode, "identical": False, "has_changes": True,
        "provenance": {"changed": True, "engine": {"from": "rules", "to": "llm"},
                       "model": {"from": None, "to": "llama3.1"}, "fallback_used": None, "deterministic": None,
                       "mode_counts": None, "engine_only": False,
                       "note": "Baseline analysed by rules; current by llm. Category changes may reflect the "
                               "engine, not the product."},
        "verdict": {"changed": True, "from": "GO", "to": "NO_GO", "direction": "worse", "from_risk": 35,
                    "to_risk": 72, "risk_delta": 37},
        "categories": {"changed": [{"category": "PRODUCT_BUG", "from": 2, "to": 5, "delta": 3}],
                       "from_total": 8, "to_total": 8},
        "clusters": {"added": [{"signature": "t", "label": "Timeout in checkout", "size": 4, "member_count": 4}],
                     "removed": [{"signature": "d", "label": "DB pool exhausted", "size": 6, "member_count": 6}],
                     "matched": [], "counts": {"added": 1, "removed": 1, "matched": 0, "resized": 0}},
        "tests": {
            "added": [], "removed": [],
            "changed": [
                {"test_fingerprint": "fp1", "test_id": "t1", "from_test_id": "f1", "test_name": "test_pay",
                 "suite_name": HOSTILE, "kinds": ["category", "confidence"], "material": True,
                 "category": {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"},
                 "confidence": {"from": 40, "to": 85, "delta": 45}, "flaky": None, "root_cause_changed": True,
                 "root_cause_excerpt": {"from": "a", "to": "b"}},
                {"test_fingerprint": "fp2", "test_id": "t2", "from_test_id": "f2", "test_name": "test_refund",
                 "suite_name": "checkout", "kinds": ["category"], "material": True,
                 "category": {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"}, "confidence": None, "flaky": None,
                 "root_cause_changed": False, "root_cause_excerpt": None},
                {"test_fingerprint": "fp3", "test_id": "t3", "from_test_id": "f3", "test_name": "test_add",
                 "suite_name": "cart", "kinds": ["category"], "material": True,
                 "category": {"from": None, "to": "TEST_DATA"}, "confidence": None, "flaky": None,
                 "root_cause_changed": False, "root_cause_excerpt": None},
            ],
            "persisting_unchanged": 5, "counts": {"added": 0, "removed": 0, "changed": 3, "material_changed": 3},
            "truncated": False,
        },
        "actions": {"added": [], "removed": []},
    }


def _ref(ref, version, *, mode, model, source="stored") -> dict:
    return {"ref": ref, "source": source, "version": version, "kind": "analysis_digest", "generated_at": None,
            "pipeline_run_id": None, "content_sha256": None, "analysis_mode": mode, "llm_model": model,
            "run_id": RUN, "build_number": "1234"}


def _diff_body() -> dict:
    return {"run_id": RUN, "from": _ref(V1, 1, mode="rules", model=None),
            "to": _ref(V3, 3, mode="llm", model="llama3.1"), "diff": _diff("versions"), **_envelope()}


def _compare_body() -> dict:
    return {
        "run_id": RUN, "project_id": PROJECT,
        "baseline": {"run_id": BASE, "build_number": "b-1233", "run_seq": 1233, "branch": "main",
                     "primary_suite_name": "checkout", "status": "PASSED", "created_at": None, "finished_at": None,
                     "selection": "default",
                     "selection_reason": "Same suite and branch, newest completed run before this one.",
                     "branch_mismatch": False},
        "reason": None, "message": None,
        "current_ref": _ref("live", None, mode="llm", model="llama3.1", source="live"),
        "baseline_ref": _ref("live", None, mode="rules", model=None, source="live"),
        "sides": {"baseline": None, "current": None},
        "diff": _diff("cross_run"),
        "narrative": {"status": "deterministic", "source": "deterministic", "label": "deterministic summary",
                      "fallback_used": True, "executive_summary": "The AI verdict went from GO to NO_GO.",
                      "ai_changes": ["Verdict GO -> NO_GO (risk 35 -> 72)",
                                     "New root cause: Timeout in checkout (4 tests)"],
                      "risk_level": None, "prompt_version": "run_compare_v2", "model_name": None,
                      "generated_at": None, "report_status": "absent"},
        **_envelope(),
    }


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools() -> dict:
    mcp = _FakeMCP()
    intelligence.register(mcp)
    return mcp.tools


def _call(name: str, **kwargs):
    return asyncio.run(_tools()[name](**kwargs))


@pytest.fixture
def backend(monkeypatch):
    """The real ``client.get`` over a MockTransport; answers by path, records every request."""
    seen: list[httpx.Request] = []
    state = {"status": 200, "bodies": {LIST_PATH: _list_body(), DIFF_PATH: _diff_body(),
                                       COMPARE_PATH: _compare_body()}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = state["bodies"].get(request.url.path, {"detail": "Not Found"})
        return httpx.Response(state["status"], json=copy.deepcopy(body))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    return seen, state


# ── 1. the contracts ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", TOOLS)
def test_the_tool_contract_matches_its_snapshot(name):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")
    intelligence.register(mcp)
    listed = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    actual = listed[name].model_dump(mode="json", exclude_none=True)
    snapshot = SNAPSHOTS / f"{name}.tool.json"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.write_bytes((json.dumps(actual, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    assert actual == json.loads(snapshot.read_text(encoding="utf-8")), (
        f"{name}'s published contract changed. If intended, rerun with UPDATE_SNAPSHOTS=1 and review the diff."
    )


def test_the_arguments_are_the_contracts():
    def schema(name):
        return json.loads((SNAPSHOTS / f"{name}.tool.json").read_text(encoding="utf-8"))["inputSchema"]

    assert set(schema("get_ai_history")["properties"]) == {"run_id", "limit"}
    assert schema("get_ai_history")["required"] == ["run_id"]
    assert set(schema("diff_ai_versions")["properties"]) == {"run_id", "from_version", "to_version"}
    assert schema("diff_ai_versions")["required"] == ["run_id", "from_version"]
    assert set(schema("compare_ai_with_previous")["properties"]) == {"run_id", "baseline_run_id"}
    assert schema("compare_ai_with_previous")["required"] == ["run_id"]


@pytest.mark.parametrize("name", TOOLS)
def test_the_description_says_how_to_read_ai_content(name):
    doc = " ".join((_tools()[name].__doc__ or "").split())
    assert "AI text" in doc
    assert "not a product change" in doc or "may reflect the engine, not the product" in doc


def test_compare_says_not_analysed_is_not_no_change():
    doc = " ".join((_tools()["compare_ai_with_previous"].__doc__ or "").split())
    assert "NOT COMPARED, which is not \"no change\"" in doc
    assert "deterministic summary" in doc


# ── 2. the wire ─────────────────────────────────────────────────────────────


def test_the_paths_are_routes_the_backend_serves():
    """The three tools call the W03 routes; a renamed route would 404 every call."""
    source = BACKEND_ROUTER.read_text(encoding="utf-8")
    assert 'prefix="/api/v1/runs"' in source
    for route in ('"/{run_id}/ai-history"', '"/{run_id}/ai-history/diff"', '"/{run_id}/ai-compare"'):
        assert route in source, route
    tools_src = (MCP_DIR / "tools" / "intelligence.py").read_text(encoding="utf-8")
    for path in ('f"/api/v1/runs/{run_id}/ai-history"', 'f"/api/v1/runs/{run_id}/ai-history/diff"',
                 'f"/api/v1/runs/{run_id}/ai-compare"'):
        assert path in tools_src, path


def test_get_ai_history_sends_the_digest_kind_and_the_limit(backend):
    seen, _ = backend
    result = _call("get_ai_history", run_id=RUN)
    assert result["ok"] is True
    assert seen[0].method == "GET" and seen[0].url.path == LIST_PATH
    assert dict(seen[0].url.params.multi_items()) == {"kind": "analysis_digest", "limit": "20"}
    assert seen[0].headers["authorization"] == "Bearer caller-token"


@pytest.mark.parametrize(("asked", "sent"), [(0, "1"), (50, "50"), (500, "100")])
def test_limit_is_clamped_to_the_routes_range(backend, asked, sent):
    seen, _ = backend
    _call("get_ai_history", run_id=RUN, limit=asked)
    assert seen[0].url.params["limit"] == sent


@pytest.mark.parametrize(("to_version", "to"), [(3, V3), ("v2", V2), ("latest", "latest"), ("LIVE", "live")])
def test_diff_resolves_the_numbers_then_diffs_once(backend, to_version, to):
    seen, _ = backend
    result = _call("diff_ai_versions", run_id=RUN, from_version=1, to_version=to_version)
    assert result["ok"] is True, result
    assert [r.url.path for r in seen] == [LIST_PATH, DIFF_PATH]
    assert dict(seen[0].url.params.multi_items()) == {"kind": "analysis_digest", "limit": "100"}
    assert dict(seen[1].url.params.multi_items()) == {"from": V1, "to": to}
    assert all(r.headers["authorization"] == "Bearer caller-token" for r in seen)
    assert result["diff"] == _diff_body()


@pytest.mark.parametrize(("frm", "to"), [(9, "latest"), (1, 9)])
def test_an_unknown_version_number_is_answered_without_a_diff_call(backend, frm, to):
    seen, _ = backend
    result = _call("diff_ai_versions", run_id=RUN, from_version=frm, to_version=to)
    assert result == {"ok": False, "error": "v9 is not a stored AI version of this run (stored: v1, v2, v3).",
                      "stored_versions": [1, 2, 3]}
    assert [r.url.path for r in seen] == [LIST_PATH]


@pytest.mark.parametrize(("frm", "to"), [(0, "latest"), (1, "newest"), (1, 0), (True, "latest")])
def test_a_malformed_version_makes_no_call(backend, frm, to):
    seen, _ = backend
    result = _call("diff_ai_versions", run_id=RUN, from_version=frm, to_version=to)
    assert result["ok"] is False and "version number" in result["error"]
    assert seen == []


def test_compare_never_sends_none(backend):
    seen, _ = backend
    result = _call("compare_ai_with_previous", run_id=RUN)
    assert result["ok"] is True
    assert seen[0].url.path == COMPARE_PATH
    assert dict(seen[0].url.params.multi_items()) == {}
    assert "None" not in str(seen[0].url)
    _call("compare_ai_with_previous", run_id=RUN, baseline_run_id=BASE)
    assert dict(seen[1].url.params.multi_items()) == {"baseline_run_id": BASE}


# ── 3. the answers ──────────────────────────────────────────────────────────


def test_history_answers_with_one_row_per_version_and_the_body(backend):
    result = _call("get_ai_history", run_id=RUN)
    summary = result["summary"]
    assert "| v3 | 2026-10-13 10:00 | llm | llama3.1 | NO_GO | 72 | 7 | fallback, latest, decision report v3 |" \
        in summary
    assert "| v1 | 2026-10-11 10:00 | rules | — | — | — | 8 | deterministic, backfilled |" in summary
    assert "3 stored version(s), latest v3" in summary
    assert "**review_state:** `pending_review`" in summary
    assert result["history"] == _list_body(), "the REST body verbatim"


def test_an_empty_history_says_so(backend):
    _, state = backend
    state["bodies"][LIST_PATH] = {**_list_body(), "total": 0, "latest_version": None, "versions": []}
    summary = _call("get_ai_history", run_id=RUN)["summary"]
    assert "No AI versions are stored for this run yet." in summary
    assert "has a current AI analysis" in summary


def test_the_diff_puts_provenance_first(backend):
    summary = _call("diff_ai_versions", run_id=RUN, from_version=1)["summary"]
    assert summary.startswith("## AI diff — run #1234 · v1 → v3\n**Engines:** rules → llm llama3.1")
    provenance = summary.index("### Provenance (read first)")
    assert provenance < summary.index("### Verdict") < summary.index("### Tests")
    assert "- engine: rules → llm" in summary
    assert "GO → NO_GO (worse); risk 35 → 72 (+37)" in summary
    assert "| test_pay | checkout¦'rm -rf' second line | category INFRASTRUCTURE → PRODUCT_BUG; " \
        "confidence 40 → 85 |" in summary


def test_a_hostile_name_cannot_break_the_table(backend):
    summary = _call("diff_ai_versions", run_id=RUN, from_version=1)["summary"]
    row = next(line for line in summary.splitlines() if line.startswith("| test_pay"))
    assert row.count("|") == 4, row
    assert "`" not in row


def test_compare_answers_with_the_storys_headline(backend):
    result = _call("compare_ai_with_previous", run_id=RUN)
    summary = result["summary"]
    assert ("**vs #1233 (checkout · main): verdict GO → NO_GO · new root causes 1 · resolved 1 · "
            "3 tests changed category**") in summary
    assert result["verdict_regressed"] is True
    assert result["compare"] == _compare_body()


def test_offline_the_summary_is_labelled_deterministic_never_ai(backend):
    summary = _call("compare_ai_with_previous", run_id=RUN)["summary"]
    assert "### deterministic summary" in summary
    assert "AI narrative" not in summary
    assert "- Verdict GO -> NO_GO (risk 35 -> 72)" in summary


def test_the_label_follows_the_source_not_the_servers_wording(backend):
    _, state = backend
    body = _compare_body()
    body["narrative"]["label"] = "AI narrative"  # a server bug must not make a deterministic text read as AI
    state["bodies"][COMPARE_PATH] = body
    assert "### deterministic summary" in _call("compare_ai_with_previous", run_id=RUN)["summary"]
    body["narrative"].update({"source": "llm", "status": "ready", "fallback_used": False})
    assert "### AI narrative" in _call("compare_ai_with_previous", run_id=RUN)["summary"]


def test_no_previous_run_says_why_and_cannot_judge_the_verdict(backend):
    _, state = backend
    body = _compare_body()
    body.update({"baseline": None, "reason": "no_previous_run",
                 "message": 'No earlier completed run of "checkout" on branch "main" found.',
                 "current_ref": None, "baseline_ref": None, "sides": None, "diff": None, "narrative": None})
    state["bodies"][COMPARE_PATH] = body
    result = _call("compare_ai_with_previous", run_id=RUN)
    assert result["ok"] is True and result["verdict_regressed"] is None
    assert '**No previous run: No earlier completed run of "checkout" on branch "main" found.**' in result["summary"]


def test_a_baseline_without_analysis_is_not_no_change(backend):
    _, state = backend
    body = _compare_body()
    body.update({"reason": "baseline_not_analysed", "message": "#1233 has no AI analysis to compare.",
                 "diff": None, "narrative": None})
    state["bodies"][COMPARE_PATH] = body
    result = _call("compare_ai_with_previous", run_id=RUN)
    assert result["verdict_regressed"] is None
    assert "**vs #1233 (checkout · main): not compared: #1233 has no AI analysis to compare.**" in result["summary"]
    assert '_Not compared: this is not the same as "no change"._' in result["summary"]


@pytest.mark.parametrize(("direction", "expected"), [("worse", True), ("better", False), ("same", False),
                                                     ("unknown", None), (None, None)])
def test_verdict_regressed_reads_only_a_known_direction(direction, expected):
    body = _compare_body()
    body["diff"]["verdict"]["direction"] = direction
    assert ai_history_format.verdict_regressed(body) is expected


def test_name_is_one_line_and_cut():
    assert ai_history_format.name(None) == "—"
    assert ai_history_format.name("a|b`c`\n d") == "a¦b'c' d"
    assert len(ai_history_format.name("x" * 200)) == 80


# ── 4. errors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "kwargs"), [
    ("get_ai_history", {"run_id": RUN}),
    ("diff_ai_versions", {"run_id": RUN, "from_version": 1}),
    ("compare_ai_with_previous", {"run_id": RUN}),
])
def test_an_unreadable_run_is_a_structured_404(backend, name, kwargs):
    _, state = backend
    state["status"] = 404
    state["bodies"] = {path: {"detail": "Test run not found"} for path in (LIST_PATH, DIFF_PATH, COMPARE_PATH)}
    result = _call(name, **kwargs)
    assert result == {"ok": False, "error": result["error"], "status_code": 404, "detail": "Test run not found"}


def test_a_store_outage_is_a_structured_503(backend):
    _, state = backend
    state["status"] = 503
    state["bodies"][LIST_PATH] = {"detail": "ai_history_unavailable"}
    result = _call("get_ai_history", run_id=RUN)
    assert (result["ok"], result["status_code"], result["detail"]) == (False, 503, "ai_history_unavailable")


def test_a_connection_failure_is_a_structured_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    for name, kwargs in (("get_ai_history", {"run_id": RUN}),
                         ("diff_ai_versions", {"run_id": RUN, "from_version": 1}),
                         ("compare_ai_with_previous", {"run_id": RUN})):
        result = _call(name, **kwargs)
        assert result["ok"] is False and "status_code" not in result, name
