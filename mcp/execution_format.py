"""Markdown for the execution-time tools (EXEC-06): ``get_run_timing`` and
``get_execution_metrics``.

Pure functions over the REST bodies (``GET /api/v1/runs/{run_id}/timing`` and
``GET /api/v1/analytics/execution-metrics``); the tools return the summary
built here beside the body itself.

Honest by construction:

* ``null`` in the API means the test report did not carry the figure (a JUnit
  file has no timestamps, so no wall clock). It is written "not reported" --
  never 0, never left out so the reader assumes it. A measured 0 is "0 ms".
* A block the server could not judge (``measured: false``) is written with its
  reason ("not enough history (4 of 10)"), never as "no regressions".
* Names (test, suite, branch, worker) come from ingested CI reports: they are
  flattened to one line, ``|`` and backticks neutralised, and cut, so a hostile
  name cannot break the table or open a code span.
"""

from __future__ import annotations

from typing import Any, Optional

NOT_REPORTED = "not reported"
#: A table cell for a figure the report did not carry.
CELL_MISSING = "—"
MAX_NAME = 80
#: Rows per table in a summary (the structured body keeps every row).
MAX_ROWS = 15


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def format_ms(value: Any) -> Optional[str]:
    """``9000`` -> ``9.0 s``; ``None`` -> ``None``."""
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


def ms_phrase(value: Any) -> str:
    return format_ms(value) or NOT_REPORTED


def ms_cell(value: Any) -> str:
    return format_ms(value) or CELL_MISSING


def ratio(value: Any) -> Optional[str]:
    number = _num(value)
    return None if number is None else f"{number:.1f}×"


def pct(value: Any) -> str:
    number = _num(value)
    return CELL_MISSING if number is None else f"{number:+.1f}%"


def name(value: Any) -> str:
    """An ingested name, safe inside a markdown table cell."""
    if value is None or value == "":
        return CELL_MISSING
    text = " ".join(str(value).split()).replace("|", "¦").replace("`", "'")
    return text if len(text) <= MAX_NAME else text[: MAX_NAME - 1] + "…"


def _unmeasured(block: Any) -> Optional[str]:
    if not isinstance(block, dict):
        return "not available"
    if block.get("measured") is False:
        return str(block.get("reason") or NOT_REPORTED)
    return None


def _metric(value: Any) -> str:
    return {"wall": "wall clock", "summed": "test time"}.get(str(value), str(value or CELL_MISSING))


# ── get_run_timing ──────────────────────────────────────────────────────────


def kpi_line(figures: Any) -> str:
    figures = figures if isinstance(figures, dict) else {}
    return " · ".join((
        f"Wall clock {ms_phrase(figures.get('wall_clock_ms'))}",
        f"Test time {ms_phrase(figures.get('summed_ms'))}",
        f"Parallelism {ratio(figures.get('parallelism')) or NOT_REPORTED}",
    ))


def has_duration_findings(findings: Any) -> bool:
    if not isinstance(findings, dict) or findings.get("measured") is False:
        return False
    return bool(
        int(findings.get("spike_count") or 0)
        or int(findings.get("suite_regression_count") or 0)
        or findings.get("spikes")
        or findings.get("suites")
    )


def _findings_lines(findings: Any) -> list[str]:
    if not isinstance(findings, dict):
        return ["### Duration regressions", "Not available for this run."]
    if findings.get("measured") is False:
        return ["### Duration regressions", f"Not measured: {findings.get('reason') or NOT_REPORTED}."]
    spikes = findings.get("spikes") or []
    suites = findings.get("suites") or []
    spike_count = int(findings.get("spike_count") or len(spikes))
    suite_count = int(findings.get("suite_regression_count") or len(suites))
    lines = [
        "### Duration regressions",
        f"{spike_count} test(s) slower than their baseline, {suite_count} suite(s) slower than their history.",
    ]
    if spikes:
        lines += ["", "| Test | Suite | This run | Baseline mean | Ratio | New |", "|---|---|---|---|---|---|"]
        for spike in spikes[:MAX_ROWS]:
            lines.append(
                f"| {name(spike.get('test_name') or spike.get('test_fingerprint'))} "
                f"| {name(spike.get('suite_name'))} | {ms_cell(spike.get('observed_ms'))} "
                f"| {ms_cell(spike.get('baseline_mean_ms'))} | {ratio(spike.get('ratio')) or CELL_MISSING} "
                f"| {'no (already slow)' if spike.get('was_slow_before') else 'yes'} |"
            )
    if suites:
        lines += ["", "| Suite | Metric | Recent median | Baseline median | Ratio | New |",
                  "|---|---|---|---|---|---|"]
        for suite in suites[:MAX_ROWS]:
            lines.append(
                f"| {name(suite.get('suite_name') or suite.get('suite_key'))} | {_metric(suite.get('metric'))} "
                f"| {ms_cell(suite.get('recent_median_ms'))} | {ms_cell(suite.get('baseline_median_ms'))} "
                f"| {ratio(suite.get('ratio')) or CELL_MISSING} "
                f"| {'no (already slow)' if suite.get('was_slow_before') else 'yes'} |"
            )
    return lines


def run_timing_markdown(body: dict) -> str:
    figures = body.get("run") if isinstance(body.get("run"), dict) else {}
    build = body.get("build_number")
    title = f"Run #{name(build)}" if build else f"Run `{str(body.get('run_id', ''))[:8]}`"
    header = " · ".join(p for p in (title, name(body.get("branch")) if body.get("branch") else "",
                                    str(body.get("status") or "")) if p)
    lines = [f"## Timing — {header}", "", f"**{kpi_line(figures)}**"]
    lines.append(
        "Wall clock = how long the run took on the clock; test time = the sum of the test durations; "
        "parallelism = test time / wall clock."
    )
    if figures.get("timing_source") is None:
        lines.append("_This run's timing has not been computed yet (the backfill has not reached it)._")
    counts = (
        f"{int(figures.get('test_count') or 0):,} tests · {int(figures.get('duration_count') or 0):,} with a "
        f"duration · {int(figures.get('timed_count') or 0):,} with start/finish timestamps"
    )
    lines += ["", counts]

    phases = body.get("phases")
    reason = _unmeasured(phases)
    lines.append(
        f"Phases: {reason}" if reason else
        f"Phases: setup {ms_phrase(phases.get('setup_ms'))} · test body {ms_phrase(phases.get('call_ms'))} · "
        f"teardown {ms_phrase(phases.get('teardown_ms'))}"
    )

    suites = body.get("suites")
    reason = _unmeasured(suites)
    lines += ["", "### Suites"]
    if reason:
        lines.append(f"Not measured: {reason}.")
    elif not suites.get("items"):
        lines.append("None.")
    else:
        lines += ["| Suite | Wall clock | Test time | Parallelism | Tests | Failed |", "|---|---|---|---|---|---|"]
        for item in suites["items"][:MAX_ROWS]:
            failed = int(item.get("failed") or 0) + int(item.get("broken") or 0)
            lines.append(
                f"| {name(item.get('suite_name'))} | {ms_cell(item.get('wall_ms'))} "
                f"| {ms_cell(item.get('summed_ms'))} | {ratio(item.get('parallelism')) or CELL_MISSING} "
                f"| {int(item.get('test_count') or 0)} | {failed} |"
            )
        total = int(suites.get("total") or len(suites["items"]))
        if total > min(len(suites["items"]), MAX_ROWS):
            lines.append(f"_{min(len(suites['items']), MAX_ROWS)} of {total} suites shown._")

    slowest = body.get("slowest")
    reason = _unmeasured(slowest)
    lines += ["", "### Slowest tests"]
    if reason:
        lines.append(f"Not measured: {reason}.")
    elif not slowest.get("items"):
        lines.append("None.")
    else:
        lines += ["| # | Test | Suite | Status | Duration | Baseline mean | vs baseline |",
                  "|---|---|---|---|---|---|---|"]
        for index, item in enumerate(slowest["items"], 1):
            baseline = item.get("baseline") if isinstance(item.get("baseline"), dict) else None
            lines.append(
                f"| {index} | {name(item.get('test_name'))} | {name(item.get('suite_name'))} "
                f"| {name(item.get('status'))} | {ms_cell(item.get('duration_ms'))} "
                f"| {ms_cell(baseline.get('mean_ms')) if baseline else CELL_MISSING} "
                f"| {ratio(item.get('baseline_ratio')) or CELL_MISSING} |"
            )
        lines.append(f"_{len(slowest['items'])} of {int(slowest.get('total') or 0):,} tests with a duration._")

    workers = body.get("workers")
    reason = _unmeasured(workers)
    lines += ["", f"Workers: {reason}" if reason else f"Workers: {int(workers.get('total') or 0)} lane(s)."]
    lines += [""] + _findings_lines(body.get("duration_findings"))
    lines += ["", f"_{CELL_MISSING} = not reported by the test report (never 0)._"]
    return "\n".join(lines)


# ── get_execution_metrics ───────────────────────────────────────────────────


def _pct_block(window: Any, key: str, field: str) -> Any:
    block = window.get(key) if isinstance(window, dict) else None
    return block.get(field) if isinstance(block, dict) else None


def _cut_note(block: dict, noun: str) -> list[str]:
    """A cut list says so: ``total`` is the count before the server's ``top``
    (and this summary's ``MAX_ROWS``) cut it."""
    shown = min(len(block.get("items") or []), MAX_ROWS)
    total = int(block.get("total") or 0)
    return [f"_{shown} of {total} {noun} shown (biggest ratio first)._"] if total > shown else []


def _histogram_line(hist: Any) -> str:
    reason = _unmeasured(hist)
    if reason:
        return f"Test durations: {reason}."
    buckets = [b for b in hist.get("buckets") or [] if int(b.get("count") or 0) > 0]
    parts = []
    for bucket in buckets:
        upper = bucket.get("upper_ms")
        label = (f"≥ {ms_cell(bucket.get('lower_ms'))}" if upper is None
                 else f"{ms_cell(bucket.get('lower_ms'))}–{ms_cell(upper)}")
        parts.append(f"{label}: {int(bucket['count']):,}")
    head = (
        f"Test durations ({int(hist.get('n') or 0):,} executions): p50 {ms_phrase(hist.get('p50_ms'))}, "
        f"p95 {ms_phrase(hist.get('p95_ms'))}"
    )
    return head + (f". By bucket: {'; '.join(parts)}." if parts else ".")


def execution_metrics_markdown(body: dict) -> str:
    days = body.get("days")
    scope = [f"last {days} day(s)"]
    if body.get("branch"):
        scope.append(f"branch {name(body['branch'])}")
    lines = [f"## Execution time — {' · '.join(scope)}"]
    if body.get("measured") is False:
        lines += ["", f"Not measured: {body.get('reason') or NOT_REPORTED}."]

    totals = body.get("totals") if isinstance(body.get("totals"), dict) else {}
    runs = int(totals.get("runs") or 0)
    wall = ms_phrase(totals.get("wall_clock_ms"))
    if totals.get("wall_clock_ms") is not None:
        wall += f" over {int(totals.get('wall_runs') or 0):,} of {runs:,} runs"
    lines += ["", (
        f"**Runs {runs:,} · Wall clock {wall} · Test time {ms_phrase(totals.get('summed_ms'))} · "
        f"Parallelism {ratio(totals.get('parallelism')) or NOT_REPORTED}**"
    )]

    suites = body.get("suites") if isinstance(body.get("suites"), dict) else {}
    items = suites.get("items") or []
    lines += ["", "### Suites (this window vs the previous window of the same length)"]
    if not items:
        lines.append("No suite ran in the window.")
    else:
        lines += [
            "| Suite | Runs | Wall p50 | Wall p95 | Wall p50 change | Test time p50 | Test time p95 "
            "| Test time p50 change |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for item in items[:MAX_ROWS]:
            current = item.get("current") or {}
            lines.append(
                f"| {name(item.get('suite_name') or item.get('suite_key'))} | {int(current.get('runs') or 0)} "
                f"| {ms_cell(_pct_block(current, 'wall', 'p50_ms'))} "
                f"| {ms_cell(_pct_block(current, 'wall', 'p95_ms'))} | {pct(item.get('wall_p50_change_pct'))} "
                f"| {ms_cell(_pct_block(current, 'summed', 'p50_ms'))} "
                f"| {ms_cell(_pct_block(current, 'summed', 'p95_ms'))} | {pct(item.get('summed_p50_change_pct'))} |"
            )
        total = int(suites.get("total") or len(items))
        if total > min(len(items), MAX_ROWS):
            lines.append(f"_{min(len(items), MAX_ROWS)} of {total} suites shown (slowest wall p95 first)._")

    regressions = body.get("regressions") if isinstance(body.get("regressions"), dict) else {}
    rule = regressions.get("rule") if isinstance(regressions.get("rule"), dict) else {}
    block = regressions.get("suites")
    lines += ["", "### What got slower: suites"]
    reason = _unmeasured(block)
    if reason:
        lines.append(f"Not measured: {reason}.")
    elif not block.get("items"):
        lines.append(f"None ({int(block.get('evaluated') or 0)} suite × branch histories judged).")
    else:
        lines.append(
            f"Median of the last {rule.get('recent_runs', 5)} runs ≥ {rule.get('ratio', 1.5)}× the median of the "
            f"{rule.get('baseline_runs', 20)} before, same branch."
        )
        lines += ["| Suite | Branch | Metric | Recent median | Baseline median | Ratio | New |",
                  "|---|---|---|---|---|---|---|"]
        for item in block["items"][:MAX_ROWS]:
            lines.append(
                f"| {name(item.get('suite_name') or item.get('suite_key'))} | {name(item.get('branch'))} "
                f"| {_metric(item.get('metric'))} | {ms_cell(item.get('recent_median_ms'))} "
                f"| {ms_cell(item.get('baseline_median_ms'))} | {ratio(item.get('ratio')) or CELL_MISSING} "
                f"| {'no (already slow)' if item.get('was_slow_before') else 'yes'} |"
            )
        lines += _cut_note(block, "suite regressions")

    block = regressions.get("tests")
    lines += ["", "### What got slower: tests"]
    reason = _unmeasured(block)
    if reason:
        lines.append(f"Not measured: {reason}.")
    elif not block.get("items"):
        lines.append(f"None ({int(block.get('considered') or 0)} baselined tests judged).")
    else:
        lines += ["| Test | Suite | Window p50 | Baseline mean | Ratio |", "|---|---|---|---|---|"]
        for item in block["items"][:MAX_ROWS]:
            lines.append(
                f"| {name(item.get('test_name') or item.get('test_fingerprint'))} | {name(item.get('suite_name'))} "
                f"| {ms_cell(item.get('window_p50_ms'))} | {ms_cell(item.get('baseline_mean_ms'))} "
                f"| {ratio(item.get('ratio')) or CELL_MISSING} |"
            )
        lines += _cut_note(block, "test regressions")

    block = body.get("slowest_tests")
    lines += ["", "### Slowest tests (by p95)"]
    reason = _unmeasured(block)
    if reason:
        lines.append(f"Not measured: {reason}.")
    elif not block.get("items"):
        lines.append("None.")
    else:
        lines += ["| Test | Suite | Runs | p50 | p95 | Max |", "|---|---|---|---|---|---|"]
        for item in block["items"][:MAX_ROWS]:
            lines.append(
                f"| {name(item.get('test_name') or item.get('test_fingerprint'))} | {name(item.get('suite_name'))} "
                f"| {int(item.get('n') or 0)} | {ms_cell(item.get('p50_ms'))} | {ms_cell(item.get('p95_ms'))} "
                f"| {ms_cell(item.get('max_ms'))} |"
            )

    lines += ["", _histogram_line(body.get("duration_histogram"))]
    lines += ["", f"_{CELL_MISSING} = not reported by the test reports (never 0)._"]
    return "\n".join(lines)
