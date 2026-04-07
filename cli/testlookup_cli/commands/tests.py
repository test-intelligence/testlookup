"""Test case commands — list, get."""
import asyncio
import typer

from testlookup_cli import client, output

tests_app = typer.Typer(name="tests", help="Inspect test cases")


@tests_app.command("list")
def list_tests(
    run_id: str = typer.Argument(..., help="Run ID"),
    status: str = typer.Option(None, "--status", "-s"),
    suite: str = typer.Option(None, "--suite"),
    page: int = typer.Option(1, "--page"),
    size: int = typer.Option(50, "--size"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List test cases for a run."""
    params: dict = {"page": page, "size": size}
    if status:
        params["status"] = status
    if suite:
        params["suite"] = suite

    try:
        data = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}/test-cases", params=params, profile_name=profile_name))
        items = data.get("items", []) if isinstance(data, dict) else data
        output.render(
            items, output_format,
            columns=["id", "test_name", "suite_name", "status", "duration_ms", "failure_category"],
            title="Test Cases",
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
