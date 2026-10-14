"""HON-02: the dashboard and readiness tools read the summary the API sends.

``GET /api/v1/metrics/summary`` answers each KPI as ``{"value", "trend",
"trend_direction"}`` (``metrics_service.get_dashboard_summary``). Both tools
read ``v.get("current", 0)``, a key that does not exist, so every KPI rendered
0, and ``check_release_readiness`` then applied its own literal thresholds
(85 % / 5 defects / 10 new failures / 20 flaky) to those zeros: every project
read "Pass rate is 0.0% (threshold: 85%)" as a blocker, whatever the server's
policy-driven verdict said.

The fixture ``snapshots/metrics_summary.policy_downgrade.json`` was produced by
the real ``get_dashboard_summary`` (payload assembly and ``classify_with_policy``
unmodified; only its DB reads stubbed): 96.4 % pass rate, 3 open defects, 7
flaky, 4 new failures, and a policy whose ``max_p0_defects: 0`` cap fired on 2
open critical defects, so the verdict is RED with downgrade ``p0_defects:2>0``.
``test_the_fixture_is_the_shape_the_service_builds`` keeps it honest against
the service source.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

SNAPSHOT = MCP_DIR / "tests" / "snapshots" / "metrics_summary.policy_downgrade.json"
METRICS_SERVICE = MCP_DIR.parent / "backend" / "app" / "services" / "metrics_service.py"
RELEASE_ID = "5e1ea5e0-0000-4000-8000-000000000001"


def _summary() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, answer):
    """Register the real tool bodies over a fake ``client.get``.

    ``answer`` is a payload, or a callable ``(path, params) -> payload`` that
    may raise.
    """
    import client
    from tools import metrics, release

    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        return answer(path, params) if callable(answer) else answer

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    metrics.register(mcp)
    release.register(mcp)
    return mcp.tools, calls


def _run(tools, name, *args, **kwargs) -> str:
    return asyncio.run(tools[name](*args, **kwargs))


# ── the fixture is the service's shape ──────────────────────────────────────


def _service_payload_keys() -> tuple[set[str], dict[str, set[str]]]:
    """Top-level keys of ``result_dict`` in ``get_dashboard_summary``, and the
    literal keys of each KPI card it builds."""
    tree = ast.parse(METRICS_SERVICE.read_text(encoding="utf-8"))
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_dashboard_summary"
    )
    literal = next(
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "result_dict" for t in node.targets)
        and isinstance(node.value, ast.Dict)
    )
    top: set[str] = set()
    cards: dict[str, set[str]] = {}
    for key, value in zip(literal.keys, literal.values):
        if not isinstance(key, ast.Constant):
            continue
        top.add(key.value)
        if isinstance(value, ast.Dict):
            cards[key.value] = {k.value for k in value.keys if isinstance(k, ast.Constant)}
    return top, cards


def test_the_fixture_is_the_shape_the_service_builds():
    if not METRICS_SERVICE.exists():  # pragma: no cover - MCP-only checkouts
        pytest.skip("backend not present in this checkout")
    from tools import metrics

    top, cards = _service_payload_keys()
    fixture = _summary()

    assert set(fixture) <= top, f"fixture keys the service never sends: {set(fixture) - top}"
    for name, card in fixture.items():
        if isinstance(card, dict):
            assert set(card) <= cards[name], f"{name}: fixture keys the service never sends: {set(card) - cards[name]}"

    # Every key the tools read is one the service writes.
    for name in metrics.SUMMARY_KPIS:
        assert {"value", "trend", "trend_direction"} <= cards[name], name
    assert {"release_readiness", "release_readiness_band", "release_readiness_downgrades"} <= top


def test_no_card_carries_a_current_key():
    """The bug's premise, stated against the real payload: a reader of
    ``current`` gets nothing, so every KPI it renders is 0."""
    for name, card in _summary().items():
        if isinstance(card, dict):
            assert "current" not in card and "previous" not in card, name


# ── get_dashboard_metrics ───────────────────────────────────────────────────


def test_dashboard_metrics_render_the_values_the_api_sends(monkeypatch):
    tools, calls = _tools(monkeypatch, _summary())
    text = _run(tools, "get_dashboard_metrics", "proj-1", days=7)

    assert calls == [("/api/v1/metrics/summary", {"project_id": "proj-1", "days": 7})]
    assert "| Avg Pass Rate (7d) | 96.4% |" in text
    assert "| Total Executions (7d) | 1680 |" in text
    assert "| Active Defects | 3 |" in text
    assert "| Flaky Test Count | 7 |" in text
    assert "| New Failures (24h) | 4 |" in text
    assert "| Avg Run Duration | 182s |" in text
    # The server's relative trend, rendered rather than re-derived.
    assert "▲5.9% vs previous 7 days" in text
    assert "▲16.7% vs previous 7 days" in text
    assert "▼4.2% vs previous 7 days" in text
    # The verdict and its band are the server's.
    assert "Release Readiness: **RED** (policy band: red)" in text
    assert "2 open critical (P0) defect(s); the policy allows 0 (`p0_defects:2>0`)" in text
    assert "per test execution" in text


def test_dashboard_metrics_say_pending_with_no_executions(monkeypatch):
    pending = _pending_summary()
    tools, _ = _tools(monkeypatch, pending)
    text = _run(tools, "get_dashboard_metrics", "proj-1")
    assert "Pending — no executions in window" in text
    assert "RED" not in text


def test_a_denied_project_is_not_rendered_as_zeros(monkeypatch):
    """``on_denied="empty"``: the route answers only the ``meta`` envelope."""
    tools, _ = _tools(monkeypatch, {"meta": {"schema_version": 2}})
    for name in ("get_dashboard_metrics", "check_release_readiness"):
        text = _run(tools, name, "proj-1")
        assert "no access to it, or no data" in text, name
        assert "0.0%" not in text, name


# ── check_release_readiness ─────────────────────────────────────────────────


def _pending_summary() -> dict:
    """What the service sends with no executions in the window."""
    pending = copy.deepcopy(_summary())
    pending["total_executions_7d"].update(value=0, trend=None, trend_direction="flat")
    pending["avg_pass_rate_7d"].update(value=0.0, trend=None, trend_direction="flat")
    pending.update(release_readiness=None, release_readiness_band=None, release_readiness_downgrades=[])
    return pending


def test_readiness_states_the_server_verdict_and_its_downgrades(monkeypatch):
    tools, _ = _tools(monkeypatch, _summary())
    text = _run(tools, "check_release_readiness", "proj-1")

    assert "Release Readiness: **RED** (policy band: red)" in text
    assert "| Avg Pass Rate | **96.4%** |" in text
    assert "### ❌ Blockers (policy hard caps that fired)" in text
    blockers = text.split("### ❌ Blockers (policy hard caps that fired)")[1].split("###")[0]
    assert [line for line in blockers.splitlines() if line.startswith("- ")] == [
        "- 2 open critical (P0) defect(s); the policy allows 0 (`p0_defects:2>0`)",
    ]
    assert "No-Go" in text
    # No local thresholds: the old text, and the old blockers drawn from them.
    assert "threshold" not in text.lower()
    assert "85%" not in text and "95%" not in text
    assert "open defects" not in text


@pytest.mark.parametrize("readiness, band, words", [
    ("GREEN", "yellow", "**Go**"),
    ("AMBER", "orange", "**Conditional Go**"),
    ("GREEN", None, "**Go**"),
])
def test_readiness_without_downgrades_lists_no_blockers(monkeypatch, readiness, band, words):
    summary = _summary()
    summary.update(release_readiness=readiness, release_readiness_band=band, release_readiness_downgrades=[])
    tools, _ = _tools(monkeypatch, summary)
    text = _run(tools, "check_release_readiness", "proj-1")

    assert f"Release Readiness: **{readiness}**" in text
    assert words in text
    assert "Blockers" not in text
    if band is None:
        assert "No release-gate policy is active" in text


def test_readiness_is_pending_not_red_with_no_executions(monkeypatch):
    tools, _ = _tools(monkeypatch, _pending_summary())
    text = _run(tools, "check_release_readiness", "proj-1")
    assert "Pending — no executions in window" in text
    assert "RED" not in text and "No-Go" not in text
    assert "Blockers" not in text


def test_the_tool_carries_no_literal_thresholds():
    src = (MCP_DIR / "tools" / "release.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(pass_rate|active_defects|new_failures|flaky_count)\s*[<>]=?\s*\d", src), (
        "release.py compares a KPI to a literal: the verdict is the server's policy, not a local rule"
    )
    assert "threshold:" not in src


# ── check_release_readiness(release_id=...) ─────────────────────────────────


def test_a_release_id_reads_the_stored_gate_decision(monkeypatch):
    gate = {
        "release_id": RELEASE_ID,
        "verdict": "NO_GO",
        "blocking_reasons": ["checkout::test_pay is FAILED", "CRITICAL defect open: Card declined"],
        "decided_at": "2026-10-12T09:30:00+00:00",
        "scorecard": {"denominator": 120, "evidence_count": 118, "run_count": 3, "measured": True},
        "from_snapshot": True,
    }
    tools, calls = _tools(monkeypatch, gate)
    text = _run(tools, "check_release_readiness", "proj-1", release_id=RELEASE_ID)

    assert calls == [(f"/api/v1/releases/{RELEASE_ID}/gate", None)]
    assert "Release Gate: **NO_GO**" in text
    assert "- checkout::test_pay is FAILED" in text
    assert "- CRITICAL defect open: Card declined" in text
    assert "| Tests with evidence | 118 of 120 |" in text
    assert "decided 2026-10-12T09:30:00+00:00" in text


def test_a_release_never_evaluated_says_so(monkeypatch):
    def _missing(path, params):
        request = httpx.Request("GET", f"http://api{path}")
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

    tools, _ = _tools(monkeypatch, _missing)
    text = _run(tools, "check_release_readiness", "proj-1", release_id=RELEASE_ID)
    assert "never evaluated" in text
    assert "NO_GO" not in text


def test_a_release_gate_error_other_than_404_is_not_swallowed(monkeypatch):
    def _forbidden(path, params):
        request = httpx.Request("GET", f"http://api{path}")
        raise httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))

    tools, _ = _tools(monkeypatch, _forbidden)
    with pytest.raises(httpx.HTTPStatusError):
        _run(tools, "check_release_readiness", "proj-1", release_id=RELEASE_ID)


def test_a_release_id_that_is_not_a_uuid_is_refused_before_any_call(monkeypatch):
    tools, calls = _tools(monkeypatch, {})
    text = _run(tools, "check_release_readiness", "proj-1", release_id="../../admin")
    assert "not a release UUID" in text
    assert calls == []
