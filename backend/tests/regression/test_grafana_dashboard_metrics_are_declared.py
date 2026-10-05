"""Phase D G2: the Grafana overview dashboard reads only metrics that exist.

``infra/monitoring/grafana/dashboards/testlookup-overview.json`` is hand-written
JSON, so nothing ties a panel's PromQL to ``app/core/metrics.py``. A typo, a
renamed metric, or a ``_bucket`` on a counter is not an error anywhere: the
panel renders "No data", which looks like a quiet system rather than a broken
instrument -- the same failure ``test_declared_metrics_are_emitted.py`` guards
from the other side (a declared metric nobody increments).

Three ratchets:

1. every ``testlookup_*`` series a panel expression names is one a metric
   declared in ``app.core.metrics`` actually exports -- with prometheus_client's
   suffixes: a Counter is declared WITHOUT ``_total`` and exported with it, a
   Histogram exports ``_bucket``/``_count``/``_sum`` and never its bare name;
2. panel ids are unique (Grafana links, alerts and provisioning key on them);
3. no two ``gridPos`` rectangles overlap (Grafana silently re-flows them, so
   the layout in the file is not the layout on screen).
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
    for panel in _panels(dashboard):
        for target in panel.get("targets", []):
            expr = target.get("expr")
            if expr:
                yield panel.get("id"), expr


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
