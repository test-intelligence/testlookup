"""Execution-time rendering for ``testlookup runs timing`` and ``testlookup
analytics execution`` (EXEC-06) -- pure functions.

The bodies are the REST API's, unchanged:

* ``GET /api/v1/runs/{run_id}/timing`` (``RunTimingResponse``) for a run;
* ``GET /api/v1/analytics/execution-metrics`` (``ExecutionMetricsResponse``)
  for a project.

Nothing here makes a request or writes to a stream, so every view is testable
byte for byte (``cli/tests/golden``).

**Null is "not reported".** The API sends ``null`` for a figure the test report
did not carry (a JUnit file has no timestamps, so no wall clock) and a block
that could not be measured says why (``measured: false`` + ``reason``). This
module never turns either into ``0``: a missing figure is ``not reported`` in a
sentence and ``-`` in a table cell (the caption says so), and an unmeasured
block prints its reason. A measured ``0`` is ``0 ms``.

**Names are untrusted.** Test, suite, worker and branch names come from ingested
CI reports: every one goes into a :class:`rich.text.Text` (never parsed as Rich
markup, so ``[bold]`` or ``[/]`` in a test name is that text) and through
``clean`` (a console that cannot encode a CJK suite name gets ``?``).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from testlookup_cli import chart_render

NOT_REPORTED = "not reported"
#: A table cell for a figure the report did not carry.
CELL_MISSING = "-"
CAPTION_MISSING = f"{CELL_MISSING} = not reported by the test report"


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def format_ms(value: Any) -> Optional[str]:
    """``9000`` -> ``9.0 s``; ``None`` -> ``None`` (the caller says "not reported")."""
    ms = _num(value)
    if ms is None:
        return None
    if ms < 1000:
        return f"{ms:,.0f} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {rest} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def ms_cell(value: Any) -> str:
    return format_ms(value) or CELL_MISSING


def ms_phrase(value: Any) -> str:
    return format_ms(value) or NOT_REPORTED


def ratio_text(value: Any, times: str) -> Optional[str]:
    ratio = _num(value)
    return None if ratio is None else f"{ratio:.1f}{times}"


def pct_cell(value: Any) -> str:
    pct = _num(value)
    if pct is None:
        return CELL_MISSING
    return f"{pct:+.1f}%"


def _text(value: Any, clean: Callable[[str], str], fallback: str = CELL_MISSING) -> Text:
    if value is None or value == "":
        return Text(fallback)
    return Text(clean(str(value)))


def _block_reason(block: Any) -> Optional[str]:
    """``None`` for a measured block, else its reason (or a generic one)."""
    if not isinstance(block, dict):
        return "not available"
    if block.get("measured") is False:
        return str(block.get("reason") or NOT_REPORTED)
    return None


# ── testlookup runs timing ──────────────────────────────────────────────────


def kpi_line(figures: dict, *, separator: str = " · ", times: str = "×") -> str:
    """``Wall clock 9.0 s · Test time 17.4 s · Parallelism 1.9×``."""
    parallelism = ratio_text(figures.get("parallelism"), times)
    return separator.join((
        f"Wall clock {ms_phrase(figures.get('wall_clock_ms'))}",
        f"Test time {ms_phrase(figures.get('summed_ms'))}",
        f"Parallelism {parallelism or NOT_REPORTED}",
    ))


def phases_line(phases: Any, *, separator: str = " · ") -> str:
    reason = _block_reason(phases)
    if reason is not None:
        return f"Phases: {reason}"
    return separator.join((
        f"Setup {ms_phrase(phases.get('setup_ms'))}",
        f"Test body {ms_phrase(phases.get('call_ms'))}",
        f"Teardown {ms_phrase(phases.get('teardown_ms'))}",
    ))


def run_heading(body: dict, *, separator: str = " · ", clean: Callable[[str], str] = str) -> str:
    build = body.get("build_number")
    parts = [f"Run #{build}" if build else f"Run {str(body.get('run_id', ''))[:8]}"]
    if body.get("branch"):
        parts.append(str(body["branch"]))
    if body.get("status"):
        parts.append(str(body["status"]))
    return clean(separator.join(parts))


def suites_table(suites: Any, *, times: str = "×", clean: Callable[[str], str] = str) -> RenderableType:
    reason = _block_reason(suites)
    if reason is not None:
        return Text(f"Suites: {reason}")
    items = suites.get("items") or []
    if not items:
        return Text("Suites: none")
    total = suites.get("total") or len(items)
    title = f"Suites ({len(items)} of {total})" if suites.get("truncated") else "Suites"
    table = Table(title=Text(title), caption=Text(CAPTION_MISSING), pad_edge=False)
    table.add_column("Suite", style="cyan", overflow="fold")
    for name in ("Wall clock", "Test time", "Parallelism", "Tests", "Failed"):
        table.add_column(name, justify="right", no_wrap=True)
    for item in items:
        failed = int(item.get("failed") or 0) + int(item.get("broken") or 0)
        table.add_row(
            _text(item.get("suite_name"), clean),
            ms_cell(item.get("wall_ms")),
            ms_cell(item.get("summed_ms")),
            ratio_text(item.get("parallelism"), times) or CELL_MISSING,
            f"{int(item.get('test_count') or 0):,}",
            f"{failed:,}",
        )
    return table


def slowest_table(
    slowest: Any, *, limit: int, times: str = "×", clean: Callable[[str], str] = str,
) -> RenderableType:
    reason = _block_reason(slowest)
    if reason is not None:
        return Text(f"Slowest tests: {reason}")
    items = (slowest.get("items") or [])[:limit]
    if not items:
        return Text("Slowest tests: none")
    total = int(slowest.get("total") or len(items))
    table = Table(
        title=Text(f"Slowest {len(items)} of {total:,} tests"),
        caption=Text(CAPTION_MISSING),
        pad_edge=False,
    )
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Test", style="cyan", overflow="fold")
    table.add_column("Suite", overflow="fold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Baseline", justify="right", no_wrap=True)
    table.add_column("vs baseline", justify="right", no_wrap=True)
    for index, item in enumerate(items, 1):
        baseline = item.get("baseline") if isinstance(item.get("baseline"), dict) else None
        table.add_row(
            str(index),
            _text(item.get("test_name"), clean),
            _text(item.get("suite_name"), clean),
            _text(item.get("status"), clean),
            ms_cell(item.get("duration_ms")),
            ms_cell(baseline.get("mean_ms")) if baseline else CELL_MISSING,
            ratio_text(item.get("baseline_ratio"), times) or CELL_MISSING,
        )
    return table


def has_duration_findings(findings: Any) -> bool:
    """The run has at least one duration finding (a slower test or suite).

    What ``--fail-on-regression`` exits on. ``measured: false`` or no block at
    all is NOT a finding: slowness that could not be judged never fails CI.
    """
    if not isinstance(findings, dict) or findings.get("measured") is False:
        return False
    return bool(
        int(findings.get("spike_count") or 0)
        or int(findings.get("suite_regression_count") or 0)
        or findings.get("spikes")
        or findings.get("suites")
    )


def findings_view(findings: Any, *, times: str = "×", clean: Callable[[str], str] = str) -> list[RenderableType]:
    if not isinstance(findings, dict):
        return [Text("Duration regressions: not available for this run")]
    if findings.get("measured") is False:
        return [Text(f"Duration regressions: not measured ({findings.get('reason') or NOT_REPORTED})")]
    spikes = findings.get("spikes") or []
    suites = findings.get("suites") or []
    spike_count = int(findings.get("spike_count") or len(spikes))
    suite_count = int(findings.get("suite_regression_count") or len(suites))
    out: list[RenderableType] = [Text(
        f"Duration regressions: {spike_count} slower {'test' if spike_count == 1 else 'tests'}, "
        f"{suite_count} slower {'suite' if suite_count == 1 else 'suites'}"
    )]
    if spikes:
        table = Table(title=Text("Slower than their baseline"), pad_edge=False)
        table.add_column("Test", style="cyan", overflow="fold")
        table.add_column("Suite", overflow="fold")
        table.add_column("This run", justify="right", no_wrap=True)
        table.add_column("Baseline", justify="right", no_wrap=True)
        table.add_column("Ratio", justify="right", no_wrap=True)
        table.add_column("New", no_wrap=True)
        for spike in spikes:
            table.add_row(
                _text(spike.get("test_name") or spike.get("test_fingerprint"), clean),
                _text(spike.get("suite_name"), clean),
                ms_cell(spike.get("observed_ms")),
                ms_cell(spike.get("baseline_mean_ms")),
                ratio_text(spike.get("ratio"), times) or CELL_MISSING,
                "no (slow before)" if spike.get("was_slow_before") else "yes",
            )
        out.append(table)
    if suites:
        table = Table(title=Text("Suites slower than their history"), pad_edge=False)
        table.add_column("Suite", style="cyan", overflow="fold")
        table.add_column("Metric", no_wrap=True)
        table.add_column("Recent median", justify="right", no_wrap=True)
        table.add_column("Baseline median", justify="right", no_wrap=True)
        table.add_column("Ratio", justify="right", no_wrap=True)
        table.add_column("New", no_wrap=True)
        for suite in suites:
            table.add_row(
                _text(suite.get("suite_name") or suite.get("suite_key"), clean),
                _metric_label(suite.get("metric")),
                ms_cell(suite.get("recent_median_ms")),
                ms_cell(suite.get("baseline_median_ms")),
                ratio_text(suite.get("ratio"), times) or CELL_MISSING,
                "no (slow before)" if suite.get("was_slow_before") else "yes",
            )
        out.append(table)
    return out


def _metric_label(metric: Any) -> str:
    return {"wall": "wall clock", "summed": "test time"}.get(str(metric), str(metric or CELL_MISSING))


def run_timing_view(
    body: dict,
    *,
    slowest: int,
    separator: str = " · ",
    times: str = "×",
    clean: Callable[[str], str] = str,
) -> RenderableType:
    """The whole ``runs timing`` table view, as one renderable."""
    figures = body.get("run") if isinstance(body.get("run"), dict) else {}
    parts: list[RenderableType] = [
        Text(run_heading(body, separator=separator, clean=clean), style="bold"),
        Text(kpi_line(figures, separator=separator, times=times)),
        Text(phases_line(body.get("phases"), separator=separator)),
    ]
    if figures.get("timing_source") is None:
        parts.append(Text("Timing: not computed for this run yet (the backfill has not reached it)"))
    parts += [
        Text(""),
        suites_table(body.get("suites"), times=times, clean=clean),
        Text(""),
        slowest_table(body.get("slowest"), limit=slowest, times=times, clean=clean),
        Text(""),
        *findings_view(body.get("duration_findings"), times=times, clean=clean),
    ]
    return Group(*parts)


# ── testlookup analytics execution ──────────────────────────────────────────


def _pct(block: Any, key: str) -> Any:
    return block.get(key) if isinstance(block, dict) else None


def totals_line(totals: Any, *, separator: str = " · ", times: str = "×") -> str:
    totals = totals if isinstance(totals, dict) else {}
    runs = int(totals.get("runs") or 0)
    wall_runs = int(totals.get("wall_runs") or 0)
    wall = ms_phrase(totals.get("wall_clock_ms"))
    if totals.get("wall_clock_ms") is not None and wall_runs < runs:
        wall += f" ({wall_runs:,} of {runs:,} runs)"
    parallelism = ratio_text(totals.get("parallelism"), times)
    return separator.join((
        f"Runs {runs:,}",
        f"Wall clock {wall}",
        f"Test time {ms_phrase(totals.get('summed_ms'))}",
        f"Parallelism {parallelism or NOT_REPORTED}",
    ))


def execution_suites_table(
    suites: Any, *, separator: str = " · ", clean: Callable[[str], str] = str,
) -> RenderableType:
    items = (suites.get("items") or []) if isinstance(suites, dict) else []
    if not items:
        return Text("Suites: none ran in the window")
    total = int(suites.get("total") or len(items))
    title = f"Suites ({len(items)} of {total})" if suites.get("truncated") else "Suites"
    table = Table(
        title=Text(title),
        caption=Text(clean(
            f"{CAPTION_MISSING}{separator}change is against the previous window of the same length"
        )),
        pad_edge=False,
    )
    table.add_column("Suite", style="cyan", overflow="fold")
    table.add_column("Runs", justify="right", no_wrap=True)
    for name in ("Wall p50", "Wall p95", "Wall p50 change", "Test time p50", "Test time p95",
                 "Test time p50 change"):
        table.add_column(name, justify="right", no_wrap=True)
    for item in items:
        current = item.get("current") or {}
        wall, summed = current.get("wall"), current.get("summed")
        table.add_row(
            _text(item.get("suite_name") or item.get("suite_key"), clean),
            f"{int(current.get('runs') or 0):,}",
            ms_cell(_pct(wall, "p50_ms")),
            ms_cell(_pct(wall, "p95_ms")),
            pct_cell(item.get("wall_p50_change_pct")),
            ms_cell(_pct(summed, "p50_ms")),
            ms_cell(_pct(summed, "p95_ms")),
            pct_cell(item.get("summed_p50_change_pct")),
        )
    return table


def _shown_of(items: list, block: dict) -> str:
    """``3`` when the list is whole, ``3 of 5`` when ``--top`` cut it (``total``
    is the count before the cut), so a cut list never reads as the whole set."""
    total = int(block.get("total") or len(items))
    return f"{len(items)} of {total}" if total > len(items) else str(len(items))


def suite_regressions_view(block: Any, *, times: str = "×", clean: Callable[[str], str] = str) -> RenderableType:
    reason = _block_reason(block)
    if reason is not None:
        return Text(f"Suite regressions: not measured ({reason})")
    items = block.get("items") or []
    if not items:
        return Text(f"Suite regressions: none ({int(block.get('evaluated') or 0)} suite histories judged)")
    table = Table(
        title=Text(f"Suite regressions ({_shown_of(items, block)})"),
        caption=Text("median of the last 5 runs vs the 20 before, same branch"),
        pad_edge=False,
    )
    table.add_column("Suite", style="cyan", overflow="fold")
    table.add_column("Branch", overflow="fold")
    table.add_column("Metric", no_wrap=True)
    table.add_column("Recent median", justify="right", no_wrap=True)
    table.add_column("Baseline median", justify="right", no_wrap=True)
    table.add_column("Ratio", justify="right", no_wrap=True)
    table.add_column("New", no_wrap=True)
    for item in items:
        table.add_row(
            _text(item.get("suite_name") or item.get("suite_key"), clean),
            _text(item.get("branch"), clean),
            _metric_label(item.get("metric")),
            ms_cell(item.get("recent_median_ms")),
            ms_cell(item.get("baseline_median_ms")),
            ratio_text(item.get("ratio"), times) or CELL_MISSING,
            "no (slow before)" if item.get("was_slow_before") else "yes",
        )
    return table


def test_regressions_view(block: Any, *, times: str = "×", clean: Callable[[str], str] = str) -> RenderableType:
    reason = _block_reason(block)
    if reason is not None:
        return Text(f"Test regressions: not measured ({reason})")
    items = block.get("items") or []
    if not items:
        return Text(f"Test regressions: none ({int(block.get('considered') or 0)} baselined tests judged)")
    table = Table(
        title=Text(f"Test regressions ({_shown_of(items, block)})"),
        caption=Text("window median vs the test's duration baseline"),
        pad_edge=False,
    )
    table.add_column("Test", style="cyan", overflow="fold")
    table.add_column("Suite", overflow="fold")
    table.add_column("Window p50", justify="right", no_wrap=True)
    table.add_column("Baseline", justify="right", no_wrap=True)
    table.add_column("Ratio", justify="right", no_wrap=True)
    for item in items:
        table.add_row(
            _text(item.get("test_name") or item.get("test_fingerprint"), clean),
            _text(item.get("suite_name"), clean),
            ms_cell(item.get("window_p50_ms")),
            ms_cell(item.get("baseline_mean_ms")),
            ratio_text(item.get("ratio"), times) or CELL_MISSING,
        )
    return table


def slowest_tests_view(block: Any, *, clean: Callable[[str], str] = str) -> RenderableType:
    reason = _block_reason(block)
    if reason is not None:
        return Text(f"Slowest tests: {reason}")
    items = block.get("items") or []
    if not items:
        return Text("Slowest tests: none")
    noun = "test" if len(items) == 1 else "tests"
    table = Table(title=Text(f"Slowest {len(items)} {noun} (by p95)"), pad_edge=False)
    table.add_column("Test", style="cyan", overflow="fold")
    table.add_column("Suite", overflow="fold")
    table.add_column("Runs", justify="right", no_wrap=True)
    table.add_column("p50", justify="right", no_wrap=True)
    table.add_column("p95", justify="right", no_wrap=True)
    table.add_column("Max", justify="right", no_wrap=True)
    for item in items:
        table.add_row(
            _text(item.get("test_name") or item.get("test_fingerprint"), clean),
            _text(item.get("suite_name"), clean),
            f"{int(item.get('n') or 0):,}",
            ms_cell(item.get("p50_ms")),
            ms_cell(item.get("p95_ms")),
            ms_cell(item.get("max_ms")),
        )
    return table


def scope_caption(meta: Any, *, separator: str = " · ") -> str:
    """The scope the SERVER applied (``meta``), as ``analytics trends`` prints it."""
    return separator.join(f"{label}: {value}" for label, value in chart_render.provenance_fields(meta))


def execution_view(
    body: dict,
    *,
    separator: str = " · ",
    times: str = "×",
    clean: Callable[[str], str] = str,
) -> RenderableType:
    """The whole ``analytics execution`` table view, as one renderable."""
    days = body.get("days")
    heading = [f"Execution time, last {days} {'day' if days == 1 else 'days'}"]
    if body.get("branch"):
        heading.append(f"branch {body['branch']}")
    parts: list[RenderableType] = [Text(clean(separator.join(heading)), style="bold")]
    if body.get("measured") is False:
        parts.append(Text(f"Not measured: {body.get('reason') or NOT_REPORTED}"))
    regressions = body.get("regressions") if isinstance(body.get("regressions"), dict) else {}
    parts += [
        Text(totals_line(body.get("totals"), separator=separator, times=times)),
        Text(""),
        execution_suites_table(body.get("suites"), separator=separator, clean=clean),
        Text(""),
        suite_regressions_view(regressions.get("suites"), times=times, clean=clean),
        Text(""),
        test_regressions_view(regressions.get("tests"), times=times, clean=clean),
        Text(""),
        slowest_tests_view(body.get("slowest_tests"), clean=clean),
        Text(""),
        Text(clean(scope_caption(body.get("meta"), separator=separator))),
    ]
    return Group(*parts)


__all__ = [
    "CELL_MISSING",
    "NOT_REPORTED",
    "execution_view",
    "format_ms",
    "has_duration_findings",
    "kpi_line",
    "run_timing_view",
]
