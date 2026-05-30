"""Deep analysis commands — start, status, clusters, findings."""
import asyncio
import typer

from testlookup_cli import client, output

deep_app = typer.Typer(name="deep", help="Deep investigation analysis")


@deep_app.command("start")
def start(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Trigger deep investigation for a run."""
    try:
        data = asyncio.run(client.request("POST", f"/api/v1/deep-investigate/{run_id}", json_body={"mode": "deep"}, profile_name=profile_name))
        output.print_success(f"Deep investigation queued: {data.get('message', 'OK')}")
        if data.get("task_id"):
            output.console.print(f"  Task ID: {data['task_id']}")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@deep_app.command("status")
def status(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Check deep analysis pipeline status."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/agents/runs/{run_id}/pipeline-status", params={"workflow_type": "deep"}, profile_name=profile_name))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@deep_app.command("clusters")
def clusters(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List failure clusters from deep analysis."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/deep-investigate/{run_id}/clusters", profile_name=profile_name))
        items = data if isinstance(data, list) else data.get("items", [])
        output.render(items, output_format, columns=["cluster_id", "label", "size", "representative_error"], title="Failure Clusters")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@deep_app.command("findings")
def findings(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List deep investigation findings."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/deep-investigate/{run_id}/findings", profile_name=profile_name))
        items = data if isinstance(data, list) else data.get("items", [])
        output.render(items, output_format, columns=["cluster_id", "failure_category", "severity", "root_cause_summary"], title="Deep Findings")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
