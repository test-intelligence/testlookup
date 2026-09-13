"""Intelligence commands — show, refresh."""
import asyncio
import typer

from testlookup_cli import client, output

intelligence_app = typer.Typer(name="intelligence", help="Run intelligence and AI analysis")


@intelligence_app.command("show")
def show(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Show run intelligence summary."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}/intelligence", profile_name=profile_name))
        if output_format == "json":
            output.print_json(data)
        else:
            run = data.get("run", {})
            output.console.print(f"\n[bold]Run Intelligence — Build {run.get('build_number', '?')}[/]")
            output.console.print(f"  Status: {run.get('status', '?')}  |  Pass Rate: {run.get('pass_rate', 0):.1f}%  |  Failed: {run.get('failed_tests', 0)}")

            decision = data.get("release_decision")
            if decision:
                output.console.print(f"  Release: [bold]{decision.get('recommendation', '?')}[/]  (risk: {decision.get('risk_score', '?')}/100)")

            summary = data.get("structured_summary", {})
            if summary and summary.get("executive_summary"):
                output.console.print(f"\n[dim]Executive Summary:[/]\n  {summary['executive_summary'][:300]}")

            deep = data.get("deep_pipeline_status", {})
            if deep and deep.get("status") != "never_run":
                output.console.print(f"\n  Deep Analysis: {deep.get('status', '?')} ({deep.get('completed_at', 'in progress')})")

            output.console.print()
        # E8.4: review state + AI disclaimer on stderr, so --output json stays pipeable.
        output.print_review_notice(data)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@intelligence_app.command("refresh")
def refresh(
    run_id: str = typer.Argument(..., help="Run ID"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Force refresh the intelligence snapshot."""
    try:
        asyncio.run(client.request("POST", f"/api/v1/runs/{run_id}/intelligence/refresh", profile_name=profile_name))
        output.print_success(f"Intelligence refreshed for run {run_id}")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
