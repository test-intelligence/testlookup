"""Run commands — list, get."""
import asyncio

import typer

from testlookup_cli import client, output
from testlookup_cli.errors import exit_code_for

runs_app = typer.Typer(name="runs", help="Inspect test runs")


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
