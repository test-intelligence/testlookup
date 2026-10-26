"""Analytics commands — chart data from the terminal (VIZ-211).

Both commands read ``GET /api/v1/analytics/chart-data``: the endpoint the UI's
trend and compare charts read, and the one MCP's ``get_chart_data`` reads. So
one scope gives one series on all three surfaces, and a suite filter counts
only that suite's tests (``/metrics/trends`` counts every run that touched the
suite, whole).

Unlike the MCP leaderboard tools, these are release- and suite-scoped:
``--release`` and ``--suite`` repeat (OR within each, AND across), and an
unreadable release refuses the whole request (exit 4), never part of it.

Known drift, documented rather than hidden:

* The UI clamps a row-grain window to 90 days; ``--days`` is passed through
  and the server's own limit (365) applies.
* The UI Trends page's pass-rate trend card reads ``/metrics/trends`` and
  differs from this under a suite filter.
* ``--release`` takes an id, ``unattributed``, or a release name / version
  (W18 DEV-05, ``release_resolver``): an unknown or ambiguous name exits 4 with
  the choices, never a guess. ``--project`` takes an id or a project name.

``analytics execution`` (EXEC-06) reads ``GET /api/v1/analytics/execution-metrics``
instead -- the endpoint the UI's Trends > Execution time and MCP's
``get_execution_metrics`` read -- with the same ``--release`` / ``--suite``
scope plus ``--branch``. It renders through ``timing_render``.
"""
import asyncio
import sys
from typing import Optional

import typer
from rich.console import Console

from testlookup_cli import chart_render, client, output, release_resolver, timing_render
from testlookup_cli.errors import EXIT_VALIDATION, exit_code_for

analytics_app = typer.Typer(
    name="analytics", help="Chart data and execution time: the numbers the UI's charts draw",
)

CHART_DATA_PATH = "/api/v1/analytics/chart-data"
FORMATS = ("table", "csv", "json")
#: ``--by`` without ``--top-n``: seven series plus "other" fill the eight a
#: chart can hold, instead of the server truncating at an arbitrary cut.
DEFAULT_TOP_N_WITH_BY = 7
MAX_GROUP_BY = 2

RATE_LIMITED = "Rate limited (120 requests a minute per user); retry later."


def chart_params(
    *,
    metric: str,
    group_by: list[str],
    project_id: Optional[str],
    releases: Optional[list[str]],
    suites: Optional[list[str]],
    days: int,
    top_n: Optional[int],
) -> dict:
    """The query string, with the REST parameter names. Lists stay lists (the
    client repeats the key); an unused option is not sent at all, whether
    Typer handed it over as ``None`` or ``[]``."""
    params: dict = {"metric": metric, "group_by": list(group_by), "days": days}
    if project_id:
        params["project_id"] = project_id
    if releases:
        params["release_id"] = list(releases)
    if suites:
        params["suite_name"] = list(suites)
    if top_n is not None:
        params["top_n"] = top_n
    return params


def _stdout_bytes(text: str) -> None:
    """UTF-8 bytes whatever the console's code page, so a CJK suite name in a
    CSV redirected to a file is the name and not a crash or a ``?``."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _encodable(stream):
    """A cleaner that replaces what ``stream`` cannot encode with ``?``."""
    encoding = getattr(stream, "encoding", None) or "utf-8"

    def clean(text: str) -> str:
        try:
            return text.encode(encoding, "replace").decode(encoding)
        except LookupError:
            return text.encode("ascii", "replace").decode("ascii")

    return clean


def _render(body: dict, output_format: str, metric: str, title: str) -> None:
    if output_format == "json":
        output.print_json(body)
        return
    if output_format == "csv":
        _stdout_bytes(chart_render.chart_csv(body, title))
        return
    for warning in chart_render.warnings(body):
        output.print_warning(warning)
    # A fresh console reads COLUMNS now, not at import. Off a terminal there is
    # no colour to draw, so the legacy-Windows renderer (which also narrows the
    # width by one) is not wanted: the same body renders the same everywhere.
    console = Console(highlight=False, legacy_windows=None if sys.stdout.isatty() else False)
    console.print(chart_render.chart_table(
        body,
        metric,
        ramp=output._glyph(chart_render.SPARK_BLOCKS, chart_render.SPARK_ASCII, sys.stdout),
        separator=output._glyph(" · ", " | ", sys.stdout),
        clean=_encodable(sys.stdout),
    ))


def _resolve_scope(
    project_id: Optional[str], releases: Optional[list[str]], profile_name: Optional[str],
) -> tuple[Optional[str], Optional[list[str]]]:
    """Names -> ids for ``--project`` and ``--release`` (DEV-05); ids pass through without a request."""
    async def _go():
        pid = await release_resolver.resolve_project(project_id, profile_name=profile_name)
        rels = await release_resolver.resolve_releases(releases, project_id=pid, profile_name=profile_name)
        return pid, rels

    try:
        return asyncio.run(_go())
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


def _run(params: dict, output_format: str, profile_name: Optional[str], title: str) -> None:
    if output_format not in FORMATS:
        output.print_error(f"Unknown format '{output_format}'. Use one of: {', '.join(FORMATS)}.")
        raise typer.Exit(EXIT_VALIDATION)
    try:
        body = asyncio.run(client.request(
            "GET", CHART_DATA_PATH, params=params, profile_name=profile_name,
        ))
    except Exception as e:
        message = RATE_LIMITED if getattr(e, "status_code", None) == 429 else str(e)
        output.print_error(message)
        raise typer.Exit(exit_code_for(e))
    _render(body, output_format, params["metric"], title)


@analytics_app.command("trends")
def trends(
    project_id: str = typer.Option(None, "--project", "-p", help="Project id or name. Omit for every project you can read."),
    releases: list[str] = typer.Option(None, "--release", help="Release id, name or version, or 'unattributed'. Repeat for several (OR)."),
    suites: list[str] = typer.Option(None, "--suite", help="Test suite name, case-insensitive. Repeat for several (OR)."),
    days: int = typer.Option(30, "--days", help="Window in days ending today, UTC (1-365)."),
    metric: str = typer.Option("pass_rate", "--metric", help="What to measure, e.g. pass_rate, failures, executions."),
    by: str = typer.Option(None, "--by", help="A second dimension: one series per value (e.g. suite, release)."),
    top_n: int = typer.Option(None, "--top-n", help=f"Keep the N largest series (default {DEFAULT_TOP_N_WITH_BY} with --by)."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|csv|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """A metric per UTC day — the pass-rate trend by default."""
    group_by = ["day", by] if by else ["day"]
    if by and top_n is None:
        top_n = DEFAULT_TOP_N_WITH_BY
    project_id, releases = _resolve_scope(project_id, releases, profile_name)
    params = chart_params(
        metric=metric, group_by=group_by, project_id=project_id,
        releases=releases, suites=suites, days=days, top_n=top_n,
    )
    _run(params, output_format, profile_name, f"{metric} by {', '.join(group_by)}")


EXECUTION_PATH = "/api/v1/analytics/execution-metrics"
EXECUTION_FORMATS = ("table", "json")


def execution_params(
    *,
    project_id: str,
    releases: Optional[list[str]],
    suites: Optional[list[str]],
    days: int,
    branch: Optional[str],
    top: Optional[int],
) -> dict:
    """``analytics execution``'s query string, with the REST names; an unused
    option is not sent (the route's own default applies)."""
    params: dict = {"project_id": project_id, "days": days}
    if releases:
        params["release_id"] = list(releases)
    if suites:
        params["suite_name"] = list(suites)
    if branch:
        params["branch"] = branch
    if top is not None:
        params["top"] = top
    return params


@analytics_app.command("execution")
def execution(
    project_id: str = typer.Option(..., "--project", "-p", help="Project id or name (required: one project)."),
    releases: list[str] = typer.Option(None, "--release", help="Release id, name or version, or 'unattributed'. Repeat for several (OR)."),
    suites: list[str] = typer.Option(None, "--suite", help="Test suite name, case-insensitive. Repeat for several (OR)."),
    days: int = typer.Option(30, "--days", help="Window in days ending today, UTC (1-365); compared with the window before it."),
    branch: str = typer.Option(None, "--branch", help="Only runs of this branch (exact). Omit for every branch."),
    top: int = typer.Option(None, "--top", help="Rows in each regressions list and the slowest tests (1-100, default 20)."),
    as_json: bool = typer.Option(False, "--json", help="Print the REST body as JSON (same as -o json)."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Execution time per suite (wall clock and summed test time, p50/p95,
    against the previous window), suite and test duration regressions and the
    slowest tests. A figure the reports did not carry is "-" (not reported),
    never 0."""
    output_format = "json" if as_json else output_format
    if output_format not in EXECUTION_FORMATS:
        output.print_error(f"Unknown format '{output_format}'. Use one of: {', '.join(EXECUTION_FORMATS)}.")
        raise typer.Exit(EXIT_VALIDATION)
    project_id, releases = _resolve_scope(project_id, releases, profile_name)
    params = execution_params(
        project_id=project_id, releases=releases, suites=suites, days=days, branch=branch, top=top,
    )
    try:
        body = asyncio.run(client.request("GET", EXECUTION_PATH, params=params, profile_name=profile_name))
    except Exception as e:
        message = RATE_LIMITED if getattr(e, "status_code", None) == 429 else str(e)
        output.print_error(message)
        raise typer.Exit(exit_code_for(e))
    if output_format == "json":
        output.print_json(body)
        return
    console = Console(highlight=False, legacy_windows=None if sys.stdout.isatty() else False)
    console.print(timing_render.execution_view(
        body,
        separator=output._glyph(" · ", " | ", sys.stdout),
        times=output._glyph("×", "x", sys.stdout),
        clean=_encodable(sys.stdout),
    ))


@analytics_app.command("chart")
def chart(
    metric: str = typer.Option(..., "--metric", help="What to measure, e.g. executions, pass_rate, duration_p95."),
    group_by: list[str] = typer.Option(..., "--group-by", help="The axis; repeat once to key series by a second dimension."),
    project_id: str = typer.Option(None, "--project", "-p", help="Project id or name. Omit for every project you can read."),
    releases: list[str] = typer.Option(None, "--release", help="Release id, name or version, or 'unattributed'. Repeat for several (OR)."),
    suites: list[str] = typer.Option(None, "--suite", help="Test suite name, case-insensitive. Repeat for several (OR)."),
    days: int = typer.Option(30, "--days", help="Window in days ending today, UTC (1-365)."),
    top_n: int = typer.Option(None, "--top-n", help="Keep the N largest keys and merge the rest into 'other'."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|csv|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Any metric over one or two dimensions, exactly as the UI charts it."""
    if len(group_by) > MAX_GROUP_BY:
        output.print_error(f"--group-by takes at most {MAX_GROUP_BY} dimensions, got {len(group_by)}.")
        raise typer.Exit(EXIT_VALIDATION)
    project_id, releases = _resolve_scope(project_id, releases, profile_name)
    params = chart_params(
        metric=metric, group_by=group_by, project_id=project_id,
        releases=releases, suites=suites, days=days, top_n=top_n,
    )
    _run(params, output_format, profile_name, f"{metric} by {', '.join(group_by)}")
