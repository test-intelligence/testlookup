"""Run commands — list, get, timing."""
import asyncio
import sys

import typer
from rich.console import Console

from testlookup_cli import client, output, timing_render
from testlookup_cli.errors import EXIT_REGRESSION, EXIT_VALIDATION, exit_code_for

runs_app = typer.Typer(name="runs", help="Inspect test runs")

#: ``runs timing``'s formats (the body is the REST body; there is no CSV here:
#: the per-test file is ``GET /api/v1/runs/{run_id}/timing.csv``).
TIMING_FORMATS = ("table", "json")
DEFAULT_SLOWEST = 10
MAX_SLOWEST = 100


@runs_app.command("list")
def list_runs(
    project: str = typer.Option(None, "--project", "-p", help="Project ID"),
    status: str = typer.Option(None, "--status", "-s", help="Filter by status"),
    page: int = typer.Option(1, "--page"),
    size: int = typer.Option(20, "--size"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List test runs."""
    params: dict = {"page": page, "size": size}
    if project:
        params["project_id"] = project
    if status:
        params["status"] = status

    try:
        data = asyncio.run(client.request("GET", "/api/v1/runs", params=params, profile_name=profile_name))
        items = data.get("items", []) if isinstance(data, dict) else data
        output.render(
            items, output_format,
            columns=["id", "build_number", "branch", "status", "pass_rate", "total_tests", "failed_tests", "created_at"],
            title="Test Runs",
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@runs_app.command("get")
def get_run(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Get run details."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}", profile_name=profile_name))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


def timing_params(*, slowest: int, output_format: str) -> dict:
    """The query string. The table draws no timeline, so it asks for one bar
    instead of the default 2,000; ``json`` asks for the route's defaults and
    prints the REST body verbatim."""
    params: dict = {"slowest": slowest}
    if output_format == "table":
        params["timeline_limit"] = 1
    return params


@runs_app.command("timing")
def run_timing(
    run_id: str = typer.Argument(..., help="Run ID"),
    slowest: int = typer.Option(
        DEFAULT_SLOWEST, "--slowest", "-n", help=f"How many slowest tests to list (1-{MAX_SLOWEST})."
    ),
    fail_on_regression: bool = typer.Option(
        False, "--fail-on-regression",
        help=f"Exit {EXIT_REGRESSION} when the run has duration regressions (a slower test or suite). "
        "For CI: without it slowness never changes the exit code.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the REST body as JSON (same as -o json)."),
    output_format: str = typer.Option("table", "--output", "-o", help="table|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """How long a run took and where the time went: wall clock, summed test
    time, parallelism, the suites, the slowest tests and the run's duration
    regressions. A figure the test report did not carry is "not reported",
    never 0."""
    output_format = "json" if as_json else output_format
    if output_format not in TIMING_FORMATS:
        output.print_error(f"Unknown format '{output_format}'. Use one of: {', '.join(TIMING_FORMATS)}.")
        raise typer.Exit(EXIT_VALIDATION)
    if not 1 <= slowest <= MAX_SLOWEST:
        output.print_error(f"--slowest must be 1-{MAX_SLOWEST}, got {slowest}.")
        raise typer.Exit(EXIT_VALIDATION)
    try:
        body = asyncio.run(client.request(
            "GET", f"/api/v1/runs/{run_id}/timing",
            params=timing_params(slowest=slowest, output_format=output_format),
            profile_name=profile_name,
        ))
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))

    if output_format == "json":
        output.print_json(body)
    else:
        # Off a terminal there is no colour to draw; the legacy-Windows
        # renderer (which narrows the width by one) is not wanted, so one body
        # renders the same everywhere (as ``analytics trends`` does).
        console = Console(highlight=False, legacy_windows=None if sys.stdout.isatty() else False)
        console.print(timing_render.run_timing_view(
            body,
            slowest=slowest,
            separator=output._glyph(" · ", " | ", sys.stdout),
            times=output._glyph("×", "x", sys.stdout),
            clean=output.encodable(sys.stdout),
        ))

    if fail_on_regression:
        findings = body.get("duration_findings") if isinstance(body, dict) else None
        if timing_render.has_duration_findings(findings):
            output.print_warning("The run has duration regressions (--fail-on-regression).")
            raise typer.Exit(EXIT_REGRESSION)
        if not isinstance(findings, dict) or findings.get("measured") is False:
            reason = findings.get("reason") if isinstance(findings, dict) else None
            output.print_warning(
                "Duration regressions could not be judged for this run"
                + (f" ({reason})" if reason else "")
                + "; --fail-on-regression does not fail on that."
            )
