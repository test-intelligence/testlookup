"""Chart-data rendering for ``testlookup analytics`` (VIZ-211) — pure functions.

The body is ``GET /api/v1/analytics/chart-data``'s, unchanged: a C3 ``series``
chart plus the VIZ-204 ``meta`` envelope. Nothing here makes a request or
writes to a stream, so every format is testable byte for byte.

**CSV** mirrors the UI's per-chart export (``frontend/src/lib/viz/
chartExport.ts::buildChartCsv``): the scope as ``# Label,value`` comment lines,
then ``# Warning`` lines, then the table view's wide table with RAW values. A
gap (``y: null``, or a point the server marked ``measured: false``) is an
empty cell, never 0. Two differences, on purpose: lines end in ``\\n`` and
there is no BOM. A BOM exists for Excel's double-click; a terminal pipes this
into ``grep``, ``awk`` or a script, where a BOM is a stray character in the
first field and CRLF a stray ``\\r`` in the last.

**Cells** are written by a port of ``frontend/src/lib/viz/csv.ts``
(``csvCell``/``neutraliseCsvValue``), which explains the rules at length: every
name here comes from an ingested CI report, so a suite called ``=HYPERLINK(..)``
gets a leading ``'``, a plain number such as ``-5`` is left alone, and a field
holding ``" , ; TAB CR LF`` is quoted.

**Table** is one row per series with a sparkline. Rich has none, so it is drawn
from block glyphs, or an ASCII ramp on a console that cannot encode them.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from rich.table import Table
from rich.text import Text

ALL_PROJECTS = "All projects"
ALL_RELEASES = "All releases"
ALL_SUITES = "All suites"
SCOPE_UNAVAILABLE = "Scope unavailable"

#: Rates are percentages, 0-100 (``chart_data_service``: ``x 100``).
RATE_METRICS = frozenset({"pass_rate", "failure_rate"})
#: Durations are milliseconds.
DURATION_METRICS = frozenset({"duration_p50", "duration_p95", "duration_total"})

#: Eight levels, lowest first. A gap is a space in both.
SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
#: The ASCII ramp. Its lowest level is ``_``, not a space: a space is the gap,
#: and a 0% day must not read as a day with no data.
SPARK_ASCII = "_.:-=+*#"
SPARK_GAP = " "
#: The most points a sparkline draws; a longer series shows its latest ones.
SPARK_MAX_POINTS = 60

# ── CSV cells (port of frontend/src/lib/viz/csv.ts) ─────────────────────────

#: A string spreadsheets read as exactly the number it spells. ASCII digits
#: only, as in JavaScript (Python's ``\d`` would also match ``٣``).
_PLAIN_NUMBER = re.compile(r"-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_FORMULA_START = frozenset("=+-@")
_SEGMENT_BREAK = re.compile(r"[;\t\r\n]")
_MUST_QUOTE = re.compile(r'[",;\t\r\n]')


def _ignored_lead(ch: str, after_break: bool) -> bool:
    """What a spreadsheet may trim before it looks at a cell's first character."""
    if after_break and ch == '"':
        return True
    return ch.isspace() or unicodedata.category(ch) in ("Zs", "Cf")


def _starts_formula(segment: str, after_break: bool) -> bool:
    if _PLAIN_NUMBER.fullmatch(segment):
        return False
    folded = unicodedata.normalize("NFKC", segment)
    start = 0
    while start < len(folded) and _ignored_lead(folded[start], after_break):
        start += 1
    return start < len(folded) and folded[start] in _FORMULA_START


def raw_number(value: float) -> str:
    """A number as JavaScript's ``String(value)`` writes it, so this CSV and the
    UI's export agree byte for byte: ``88.0`` is ``88``, ``1e-7`` is ``1e-7``
    (Python says ``1e-07``) and ``0.00001`` stays positional.

    Both languages pick the shortest digits that round-trip; only the layout
    differs, and this is ECMA-262's Number::toString layout over Python's digits.
    """
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    parts = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in parts.digits)
    size = len(digits)
    point = parts.exponent + size  # where the decimal point falls in ``digits``
    if size <= point <= 21:
        return sign + digits + "0" * (point - size)
    if 0 < point <= 21:
        return sign + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * -point + digits
    exponent = point - 1
    mantissa = digits[0] + ("." + digits[1:] if size > 1 else "")
    return f"{sign}{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def neutralise_csv_value(value: Any) -> str:
    """``value`` made inert for a spreadsheet, unquoted. ``None`` is ``''``."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return raw_number(value)
    rest = str(value)
    out: list[str] = []
    after_break = False
    while True:
        found = _SEGMENT_BREAK.search(rest)
        segment = rest if found is None else rest[: found.start()]
        out.append(f"'{segment}" if _starts_formula(segment, after_break) else segment)
        if found is None:
            return "".join(out)
        out.append(rest[found.start()])
        rest = rest[found.start() + 1:]
        after_break = True


def csv_cell(value: Any) -> str:
    """One CSV field: neutralised, then quoted when it must be."""
    text = neutralise_csv_value(value)
    return '"' + text.replace('"', '""') + '"' if _MUST_QUOTE.search(text) else text


def csv_row(values: list[Any]) -> str:
    return ",".join(csv_cell(value) for value in values)


# ── Provenance ──────────────────────────────────────────────────────────────


def _day_of(value: str) -> str:
    return value[:10] if re.match(r"\d{4}-\d{2}-\d{2}", value or "") else value


def format_generated_at(iso: str) -> str:
    """``2026-09-23T14:05:09Z`` -> ``2026-09-23 14:05 UTC``; else as sent."""
    try:
        at = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def totals_line(meta: dict) -> Optional[str]:
    totals = meta.get("totals")
    if not isinstance(totals, dict):
        return None
    return (
        f"{totals.get('matched_runs', 0):,} of {totals.get('total_runs', 0):,} runs · "
        f"{totals.get('matched_executions', 0):,} of {totals.get('total_executions', 0):,} executions"
    )


def provenance_fields(meta: Any) -> list[tuple[str, str]]:
    """The scope as labelled fields, in the UI export's order.

    Built from ``meta.scope``/``totals``/``generated_at``: what the SERVER
    applied, never what was asked for. Without ``meta`` the export says so
    rather than inventing a scope.
    """
    scope = meta.get("scope") if isinstance(meta, dict) else None
    if not isinstance(scope, dict):
        return [("Scope", SCOPE_UNAVAILABLE)]
    projects = [str(p.get("name", "")) for p in scope.get("projects") or ()]
    releases = [str(r.get("name", "")) for r in scope.get("releases") or ()]
    suites = [str(s) for s in scope.get("suites") or ()]
    window = scope.get("window") or {}
    days = window.get("days")
    fields = [
        ("Project", ", ".join(projects) if projects else ALL_PROJECTS),
        ("Release", ", ".join(releases) if releases else ALL_RELEASES),
        ("Test Suite", ", ".join(suites) if suites else ALL_SUITES),
        ("Window", (
            f"{_day_of(str(window.get('from', '')))} – {_day_of(str(window.get('to', '')))} "
            f"UTC ({days} {'day' if days == 1 else 'days'})"
        )),
    ]
    totals = totals_line(meta)
    if totals:
        fields.append(("Totals", totals))
    if meta.get("generated_at"):
        fields.append(("Generated", format_generated_at(str(meta["generated_at"]))))
    return fields


def warnings(body: dict) -> list[str]:
    """What a reader must know that the numbers do not say."""
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    out: list[str] = []
    for item in meta.get("ignored_filters") or ():
        out.append(f"The {item.get('dimension')} filter was not applied: {item.get('reason')}")
    axes = meta.get("truncated_axes") or {}
    for axis in ("x", "series"):
        cut = axes.get(axis)
        if cut:
            noun = "axis" if axis == "x" else "series"
            out.append(
                f"Truncated: the {noun} shows {cut.get('kept')} of {cut.get('total')} "
                f"{cut.get('dimension')} values."
            )
    if meta.get("truncated") and not axes:
        out.append(f"Truncated: {meta.get('truncated_total')} values in total.")
    outside = meta.get("outside_window")
    if outside:
        out.append(
            f"{outside.get('buckets')} bucket(s) after the window ({outside.get('first')} to "
            f"{outside.get('last')}, {outside.get('executions')} executions) are not shown."
        )
    if meta and meta.get("measured") is False:
        out.append(f"Not measured: {meta.get('reason')}")
    return out


# ── The series as a wide table ─────────────────────────────────────────────


def _measured(point: dict) -> bool:
    return point.get("y") is not None and point.get("measured") is not False


def _axis(body: dict) -> list[str]:
    """Every x in axis order: as sent (the server zero-fills and orders it),
    chronological for a time axis."""
    xs: list[str] = []
    seen: set[str] = set()
    for series in body.get("series") or ():
        for point in series.get("points") or ():
            if point["x"] not in seen:
                seen.add(point["x"])
                xs.append(point["x"])
    return sorted(xs) if body.get("x_type") == "time" else xs


def wide_table(body: dict) -> tuple[list[str], list[list[Any]]]:
    """``(columns, rows)``: the first dimension, then one column per series."""
    series = body.get("series") or []
    dimensions = body.get("dimensions") or []
    labels = body.get("x_labels") or {}
    columns = [dimensions[0] if dimensions else "x", *[str(s.get("label", s.get("key"))) for s in series]]
    values = [
        {point["x"]: (point["y"] if _measured(point) else None) for point in s.get("points") or ()}
        for s in series
    ]
    rows = [[labels.get(x, x), *[by_x.get(x) for by_x in values]] for x in _axis(body)]
    return columns, rows


def chart_csv(body: dict, title: str) -> str:
    """The chart as CSV text: provenance, warnings, then the wide table."""
    lines = [f"# TestLookup chart export,{csv_row([title])}"]
    lines += [f"# {label},{csv_row([value])}" for label, value in provenance_fields(body.get("meta"))]
    lines += [f"# Warning,{csv_row([warning])}" for warning in warnings(body)]
    columns, rows = wide_table(body)
    lines.append(csv_row(columns))
    lines += [csv_row(row) for row in rows]
    return "\n".join(lines) + "\n"


# ── Table view ──────────────────────────────────────────────────────────────


def format_value(value: Optional[float], metric: str) -> str:
    if value is None:
        return "-"
    if metric in RATE_METRICS:
        return f"{value:.1f}%"
    if metric in DURATION_METRICS:
        return f"{value:,.0f} ms"
    return f"{round(value):,}"


def sparkline(values: list[Optional[float]], *, rate: bool, ramp: str = SPARK_BLOCKS) -> str:
    """One glyph per value, scaled 0-100 for a rate and 0..max otherwise."""
    present = [v for v in values if v is not None]
    top = 100.0 if rate else max(present, default=0.0)
    levels = len(ramp) - 1
    out = []
    for value in values:
        if value is None:
            out.append(SPARK_GAP)
            continue
        share = (value / top) if top > 0 else 0.0
        out.append(ramp[max(0, min(levels, round(share * levels)))])
    return "".join(out)


def chart_table(
    body: dict,
    metric: str,
    *,
    ramp: str = SPARK_BLOCKS,
    separator: str = " · ",
    clean: Callable[[str], str] = str,
) -> Table:
    """One row per series: its sparkline and a summary of its values.

    ``clean`` is applied to every ingested name (series labels, the scope
    caption) so a console that cannot encode a CJK suite name gets a ``?``
    instead of a UnicodeEncodeError after the request succeeded.
    """
    points = len(_axis(body))
    shown = min(points, SPARK_MAX_POINTS)
    trend = f"Trend, last {shown} of {points}" if points > shown else "Trend"
    bucket = (body.get("dimensions") or [""])[0]
    measured_label = {"day": "Measured days", "week": "Measured weeks"}.get(bucket, "Measured")
    columns, rows = wide_table(body)

    table = Table(
        title=Text(clean(f"{metric} by {', '.join(body.get('dimensions') or [])}")),
        caption=Text(clean(separator.join(
            f"{label}: {value}" for label, value in provenance_fields(body.get("meta"))
        ))),
        pad_edge=False,
    )
    table.add_column("Series", style="cyan", overflow="fold")
    table.add_column(trend, no_wrap=True)
    for name in ("First", "Last", "Min", "Max"):
        table.add_column(name, justify="right", no_wrap=True)
    table.add_column(measured_label, justify="right", no_wrap=True)
    table.add_column("n", justify="right", no_wrap=True)

    for index, series in enumerate(body.get("series") or []):
        values = [row[index + 1] for row in rows]
        present = [v for v in values if v is not None]
        sample = sum(int(p.get("n") or 0) for p in series.get("points") or ())
        table.add_row(
            Text(clean(columns[index + 1])),
            Text(sparkline(values[-SPARK_MAX_POINTS:], rate=metric in RATE_METRICS, ramp=ramp)),
            format_value(present[0] if present else None, metric),
            format_value(present[-1] if present else None, metric),
            format_value(min(present) if present else None, metric),
            format_value(max(present) if present else None, metric),
            f"{len(present)} of {len(values)}",
            f"{sample:,}",
        )
    return table
