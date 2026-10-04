"""VIZ-607 — the Summary Report's charts, drawn by the server for its exports.

The PDF is circulated detached from the app, so its charts are drawn here,
from the same payload the numbers come from, with ReportLab's own
``reportlab.graphics`` (no new dependency, fully offline). A client-uploaded
PNG was rejected: the server could not vouch for it.

Three charts, each ``None`` when it would have nothing to show:

* the status breakdown (a pie of passed / failed / broken / skipped, the
  counts and shares in its legend);
* pass rate by suite (horizontal bars, 0-100 %, at most ``MAX_BARS`` suites,
  the worst first);
* the top failing tests (horizontal bars of failures, at most ``MAX_BARS``).

Every label is plain text (``String`` takes no markup) passed through
``sanitize_for_report``; a long name is cut with "…" on the bar only, because
the full name is in the table that follows each chart.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.privacy_service import sanitize_for_report

MAX_BARS = 12
LABEL_MAX = 34

# The PDF's own palette (``summary_report_pdf``): status colours a reader of
# the printed page already knows from the tables.
STATUS_COLOURS = {
    "passed": "#059669",
    "failed": "#DC2626",
    "broken": "#D97706",
    "skipped": "#94A3B8",
}
STATUS_ORDER = ("passed", "failed", "broken", "skipped")
_BAR_COLOUR = "#1E40AF"


def _label(value: Any) -> str:
    text = sanitize_for_report(str(value or "")).strip() or "—"
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def status_breakdown(totals: dict) -> Optional[Any]:
    """A pie of the four statuses, or ``None`` when nothing ran."""
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    counts = [(status, int(totals.get(status) or 0)) for status in STATUS_ORDER]
    counts = [(status, n) for status, n in counts if n > 0]
    total = sum(n for _, n in counts)
    if total == 0:
        return None
    drawing = Drawing(480, 150)
    pie = Pie()
    pie.x, pie.y, pie.width, pie.height = 20, 10, 130, 130
    pie.data = [n for _, n in counts]
    pie.labels = None
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1
    for i, (status, _) in enumerate(counts):
        pie.slices[i].fillColor = colors.HexColor(STATUS_COLOURS[status])
    drawing.add(pie)
    legend = Legend()
    legend.x, legend.y = 190, 120
    legend.fontName, legend.fontSize = "Helvetica", 9
    legend.alignment = "right"
    legend.columnMaximum = len(STATUS_ORDER)  # one column, the statuses in order
    legend.colorNamePairs = [
        (colors.HexColor(STATUS_COLOURS[status]), f"{status.capitalize()}  {n:,}  ({n / total * 100:.1f}%)")
        for status, n in counts
    ]
    drawing.add(legend)
    return drawing


def _bars(labels: list[str], values: list[float], *, value_max: Optional[float], unit: str) -> Any:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    row = 16
    height = max(60, row * len(values) + 30)
    drawing = Drawing(480, height)
    chart = HorizontalBarChart()
    chart.x, chart.y = 180, 20
    chart.width, chart.height = 260, height - 30
    # ReportLab draws the first category at the bottom: reverse so the list
    # reads top-down in the order given.
    chart.data = [list(reversed(values))]
    chart.categoryAxis.categoryNames = list(reversed(labels))
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.valueAxis.valueMin = 0
    if value_max is not None:
        chart.valueAxis.valueMax = value_max
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 8
    # A callable, not a "%d" format: "%d" truncates (68.9 % read as "68%"),
    # and a "%" unit would have to be escaped. Labels round; bars stay exact.
    def label(value: float) -> str:
        return f"{value:.0f}{unit}"

    chart.valueAxis.labelTextFormat = label
    chart.bars[0].fillColor = colors.HexColor(_BAR_COLOUR)
    chart.bars[0].strokeColor = None
    chart.barLabelFormat = label
    chart.barLabels.fontName = "Helvetica"
    chart.barLabels.fontSize = 7
    chart.barLabels.boxAnchor = "w"
    chart.barLabels.dx = 3
    drawing.add(chart)
    return drawing


def suite_pass_rates(suites: list[dict]) -> Optional[Any]:
    """Pass rate by suite, the worst first; ``None`` when no suite was evaluated."""
    rated = [s for s in suites if s.get("pass_rate_pct") is not None]
    if not rated:
        return None
    rated.sort(key=lambda s: (float(s["pass_rate_pct"]), str(s.get("suite_name") or "")))
    shown = rated[:MAX_BARS]
    return _bars(
        [_label(s.get("suite_name")) for s in shown],
        [float(s["pass_rate_pct"]) for s in shown],
        value_max=100,
        unit="%",
    )


def top_failing(tests: list[dict]) -> Optional[Any]:
    """Failures of the top failing tests, most first; ``None`` when nothing failed."""
    failing = [t for t in tests if int(t.get("failures") or 0) > 0]
    if not failing:
        return None
    failing.sort(key=lambda t: (-int(t["failures"]), str(t.get("test_name") or "")))
    shown = failing[:MAX_BARS]
    return _bars(
        [_label(t.get("test_name")) for t in shown],
        [int(t["failures"]) for t in shown],
        value_max=None,
        unit="",
    )
