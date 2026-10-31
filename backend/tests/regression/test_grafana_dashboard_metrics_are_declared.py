"""Phase D G2: the Grafana overview dashboard reads only metrics that exist.

``infra/monitoring/grafana/dashboards/testlookup-overview.json`` is hand-written
JSON, so nothing ties a panel's PromQL to ``app/core/metrics.py``. A typo, a
renamed metric, or a ``_bucket`` on a counter is not an error anywhere: the
panel renders "No data", which looks like a quiet system rather than a broken
instrument -- the same failure ``test_declared_metrics_are_emitted.py`` guards
from the other side (a declared metric nobody increments).

Three ratchets:

1. every ``testlookup_*`` series a panel expression -- or a dashboard
   annotation's expression (W25b: the "Deploys" annotation) -- names is one a
   metric declared in ``app.core.metrics`` actually exports -- with
   prometheus_client's suffixes: a Counter is declared WITHOUT ``_total`` and
   exported with it, a Histogram exports ``_bucket``/``_count``/``_sum`` and
   never its bare name;
2. panel ids are unique (Grafana links, alerts and provisioning key on them);
3. no two ``gridPos`` rectangles overlap (Grafana silently re-flows them, so
   the layout in the file is not the layout on screen).

Two rows are pinned panel by panel (row 60, row 80 "Tier 0-2 jobs"): a row
that silently lost a panel would still pass the declared-series check.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("prometheus_client")

from prometheus_client.metrics import MetricWrapperBase  # noqa: E402

from app.core import metrics  # noqa: E402

pytestmark = pytest.mark.regression

_REL = Path("infra") / "monitoring" / "grafana" / "dashboards" / "testlookup-overview.json"

#: A series name in PromQL. The trailing lookahead keeps a longer identifier
#: from matching as its prefix; label VALUES like ``job="testlookup-backend"``
#: never match (a hyphen is not part of a name).
_SERIES_RE = re.compile(r"\b(testlookup_[a-z0-9_]+)\b")

#: What prometheus_client exports for each metric type, from the name it
#: stores (a Counter's stored name has ``_total`` stripped already).
_EXPORTED_SUFFIXES = {
    "counter": ("_total", "_created"),
    "gauge": ("",),
    "histogram": ("_bucket", "_count", "_sum", "_created"),
    "summary": ("", "_count", "_sum", "_created"),
    "info": ("_info",),
    "enum": ("",),
}


def _dashboard() -> dict:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / _REL
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    pytest.skip("Grafana dashboard JSON not available in this environment")
    raise AssertionError("unreachable")


def _panels(container: dict) -> Iterator[dict]:
    """Every panel, including those folded inside a collapsed row."""
    for panel in container.get("panels", []):
        yield panel
        yield from _panels(panel)


def _exprs(dashboard: dict) -> Iterator[tuple[Any, str]]:
    """``(panel id, expr)`` for every panel target, then
    ``("annotation:<name>", expr)`` for every annotation query (a string key,
    so the per-row tests' ``isinstance(pid, int)`` filters leave it out)."""
    for panel in _panels(dashboard):
        for target in panel.get("targets", []):
            expr = target.get("expr")
            if expr:
                yield panel.get("id"), expr
    for annotation in (dashboard.get("annotations") or {}).get("list", []):
        expr = annotation.get("expr")
        if expr:
            yield f"annotation:{annotation.get('name')}", expr


def exported_series(module: Any = metrics) -> dict[str, str]:
    """Every series name the declared metrics export -> the declared name."""
    out: dict[str, str] = {}
    for value in vars(module).values():
        if not isinstance(value, MetricWrapperBase):
            continue
        stored, kind = value._name, value._type
        for suffix in _EXPORTED_SUFFIXES.get(kind, ("",)):
            out[stored + suffix] = stored
    return out


def undeclared(exprs: list[tuple[Any, str]], exported: dict[str, str]) -> list[str]:
    return [
        f"panel {pid}: {name}"
        for pid, expr in exprs
        for name in _SERIES_RE.findall(expr)
        if name not in exported
    ]


def test_every_panel_metric_is_declared_in_app_core_metrics():
    exprs = list(_exprs(_dashboard()))
    assert exprs, "no panel expressions found -- the walker is broken"
    missing = undeclared(exprs, exported_series())
    assert not missing, (
        "Dashboard panels read series no metric in app/core/metrics.py exports "
        "(they would render 'No data' forever):\n  " + "\n  ".join(missing)
    )


def test_the_suffix_rules_reject_what_prometheus_would_not_export():
    """The check itself, on the cases that make it worth having: a counter
    read without ``_total``, a histogram read by its bare name, a ``_bucket``
    on a counter, and a name nobody declared."""
    exported = exported_series()
    assert exported["testlookup_analytics_rate_limited_total"] == "testlookup_analytics_rate_limited"
    bad = [
        (1, "rate(testlookup_analytics_rate_limited[5m])"),
        (2, "rate(testlookup_report_export_render_seconds[5m])"),
        (3, "rate(testlookup_report_export_jobs_bucket[5m])"),
        (4, "rate(testlookup_no_such_metric_total[5m])"),
    ]
    assert len(undeclared(bad, exported)) == len(bad)
    good = [
        (5, "rate(testlookup_report_export_render_seconds_count[5m])"),
        (6, 'sum(increase(testlookup_report_export_jobs_total{job="testlookup-worker"}[1h]))'),
    ]
    assert undeclared(good, exported) == []


def test_panel_ids_are_unique():
    ids = [panel.get("id") for panel in _panels(_dashboard())]
    assert None not in ids, "a panel without an id"
    dupes = sorted({pid for pid in ids if ids.count(pid) > 1})
    assert not dupes, f"duplicate panel ids: {dupes}"


def _overlaps(a: dict, b: dict) -> bool:
    return (
        a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
    )


def test_grid_rectangles_do_not_overlap_and_fit_the_grid():
    """Top-level panels only: panels folded inside a collapsed row take their
    positions when it is expanded (none of this dashboard's rows is collapsed)."""
    placed = [
        (panel["id"], panel["gridPos"])
        for panel in _dashboard().get("panels", [])
        if "gridPos" in panel
    ]
    for pid, pos in placed:
        assert pos["w"] > 0 and pos["h"] > 0 and pos["x"] >= 0 and pos["y"] >= 0, pid
        assert pos["x"] + pos["w"] <= 24, f"panel {pid} runs off the 24-column grid"
    clashes = [
        f"{a_id} and {b_id}"
        for i, (a_id, a) in enumerate(placed)
        for b_id, b in placed[i + 1:]
        if _overlaps(a, b)
    ]
    assert not clashes, f"overlapping panels: {clashes}"


def test_the_overlap_check_sees_an_overlap():
    a = {"x": 0, "y": 0, "w": 8, "h": 8}
    assert _overlaps(a, {"x": 7, "y": 7, "w": 2, "h": 2})
    assert not _overlaps(a, {"x": 8, "y": 0, "w": 8, "h": 8}), "touching is not overlapping"
    assert not _overlaps(a, {"x": 0, "y": 8, "w": 8, "h": 8})


def test_the_visualization_row_graphs_the_phase_d_metrics():
    """Row 60: the three G1 metrics each back a panel, next to the read
    layer's existing three -- a dashboard row that silently lost one would
    still pass the declared-metric check above."""
    exprs = dict(_exprs(_dashboard()))
    row = {pid: expr for pid, expr in exprs.items() if isinstance(pid, int) and 61 <= pid <= 69}
    assert sorted(row) == list(range(61, 70))
    joined = "\n".join(row.values())
    for name in (
        "testlookup_analytics_query_duration_seconds_bucket",
        "testlookup_analytics_query_duration_seconds_count",
        "testlookup_analytics_rate_limited_total",
        "testlookup_analytics_read_degraded_total",
        "testlookup_analytics_epoch_bump_failures_total",
        "testlookup_report_export_jobs_total",
        "testlookup_report_export_render_seconds_bucket",
    ):
        assert name in joined, f"no row-60 panel reads {name}"
    assert 'outcome="miss"' in row[61] and 'outcome="hit"' in row[62]


# ── Row 80 "Tier 0-2 jobs" (W25b HYG-09) ────────────────────────────────────

#: Every series the row charts. Each has a production ``.inc()`` cited in the
#: W25b contract §2.3 table; the label values the row filters on are driven
#: and read back by test_alerted_metrics_are_emitted.py.
_TIER_0_2_SERIES = frozenset({
    "testlookup_quarantine_proposals_total",
    "testlookup_quarantine_approvals_total",
    "testlookup_quarantine_expired_total",
    "testlookup_webhook_delivery_attempts_total",
    "testlookup_perf_baseline_refresh_runs_total",
    "testlookup_feature_flag_evaluations_total",
    "testlookup_retro_digest_dispatch_total",
    "testlookup_compliance_pack_generated_total",
    "testlookup_orphan_test_suites_total",
    "testlookup_celery_tasks_total",
    "testlookup_suite_parity_runs_total",
    "testlookup_ws_connections_total",
})

#: The Celery tasks behind the Tier 0-2 jobs (worker/celery_app.py beat entries
#: and worker/tasks.py); panel 88 charts their terminal failures.
_TIER_0_2_TASKS = frozenset({
    "app.worker.tasks.run_flaky_quarantine_maintenance",
    "app.worker.tasks.refresh_perf_baselines",
    "app.worker.tasks.dispatch_scheduled_digests",
    "app.worker.tasks.deliver_webhook",
    "app.worker.tasks.flag_orphan_test_suites",
    "app.worker.tasks.run_suite_membership_parity",
})

#: A PromQL label matcher: name, operator, quoted value.
_MATCHER_RE = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"')

_TIER_ROW_ID = 80
_TIER_PANEL_IDS = list(range(81, 91))


def test_the_tier_0_2_row_graphs_every_tier_0_2_job():
    """Row 80: expanded, its ten panels below it, the twelve series charted and
    nothing else, panel 88 filtering on exactly the six task names, no
    ``noValue`` (absence is "No data", never a zero), and no label filter
    beyond the two whose values a production line emits."""
    dashboard = _dashboard()
    top = {panel.get("id"): panel for panel in dashboard.get("panels", [])}

    row = top.get(_TIER_ROW_ID)
    assert row is not None, "row 80 is missing (or folded inside a collapsed row)"
    assert row["type"] == "row" and row["title"] == "Tier 0-2 jobs"
    assert row.get("collapsed") is False, "row 80 ships expanded (its panels stay top-level)"
    row_y = row["gridPos"]["y"]
    next_rows = [p["gridPos"]["y"] for p in top.values() if p.get("type") == "row" and p["gridPos"]["y"] > row_y]
    for pid in _TIER_PANEL_IDS:
        assert pid in top, f"panel {pid} is missing from the top level"
        y = top[pid]["gridPos"]["y"]
        assert y > row_y and all(y < ny for ny in next_rows), f"panel {pid} is not under row 80"
    for pid in [_TIER_ROW_ID, *_TIER_PANEL_IDS]:
        assert '"noValue"' not in json.dumps(top[pid]), f"panel {pid} sets noValue"

    exprs: dict[int, list[str]] = {}
    for pid, expr in _exprs(dashboard):
        if isinstance(pid, int) and pid in _TIER_PANEL_IDS:
            exprs.setdefault(pid, []).append(expr)
    assert sorted(exprs) == _TIER_PANEL_IDS, "a row-80 panel has no query"
    every = [expr for panel_exprs in exprs.values() for expr in panel_exprs]
    charted = {name for expr in every for name in _SERIES_RE.findall(expr)}
    assert charted == _TIER_0_2_SERIES, (
        f"missing {sorted(_TIER_0_2_SERIES - charted)}, unexpected {sorted(charted - _TIER_0_2_SERIES)}"
    )

    matchers = [(pid, m) for pid, panel_exprs in exprs.items() for e in panel_exprs for m in _MATCHER_RE.findall(e)]
    exact = {(name, op, value) for _, (name, op, value) in matchers if op != "=~"}
    assert exact == {("status", "=", "failure"), ("flag_key", "=", "perf_regression_detection")}, exact
    regex = [(pid, name, value) for pid, (name, op, value) in matchers if op == "=~"]
    assert [(pid, name) for pid, name, _ in regex] == [(88, "task_name")], regex
    alternatives = re.fullmatch(r"app\.worker\.tasks\.\(([a-z_|]+)\)", regex[0][2])
    assert alternatives, regex[0][2]
    named = {f"app.worker.tasks.{task}" for task in alternatives.group(1).split("|")}
    assert named == _TIER_0_2_TASKS, named
    assert 'status="failure"' in exprs[88][0]
    assert 'flag_key="perf_regression_detection"' in " ".join(exprs[84])


def test_the_deploys_annotation_reads_app_info():
    """``testlookup_app_info`` marks each new ``version`` on every panel; it is
    set by the API only (main.py), hence the backend job filter."""
    annotations = {a.get("name"): a for a in _dashboard()["annotations"]["list"]}
    deploys = annotations.get("Deploys")
    assert deploys is not None and deploys.get("enable") is True
    assert set(_SERIES_RE.findall(deploys["expr"])) == {"testlookup_app_info"}
    assert "unless" in deploys["expr"] and "offset" in deploys["expr"]
    assert ("annotation:Deploys", deploys["expr"]) in list(_exprs(_dashboard()))


def test_the_walker_reads_annotation_expressions():
    """Self-test: an undeclared series in an annotation is caught like a panel's."""
    fake = {
        "panels": [{"id": 1, "targets": [{"expr": "testlookup_app_info"}]}],
        "annotations": {"list": [{"name": "X", "expr": "count(testlookup_no_such_metric_total)"}]},
    }
    exprs = list(_exprs(fake))
    assert exprs == [(1, "testlookup_app_info"), ("annotation:X", "count(testlookup_no_such_metric_total)")]
    assert undeclared(exprs, exported_series()) == ["panel annotation:X: testlookup_no_such_metric_total"]
