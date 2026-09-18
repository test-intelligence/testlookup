"""Report commands — pdf, share."""
import asyncio
from pathlib import Path

import typer

from testlookup_cli import client, output
from testlookup_cli.errors import exit_code_for

reports_app = typer.Typer(name="reports", help="Export and share reports")


@reports_app.command("pdf")
def pdf(
    run_id: str = typer.Argument(..., help="Run ID"),
    out: Path = typer.Option("report.pdf", "--out", "-o", help="Output file path"),
    layout: str = typer.Option("executive", "--layout", help="executive|engineering"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Download a PDF report for a run."""
    try:
        data = asyncio.run(client.download(f"/api/v1/reports/runs/{run_id}/pdf", params={"layout": layout}, profile_name=profile_name))
        out.write_bytes(data)
        output.print_success(f"Report saved to {out} ({len(data):,} bytes)")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@reports_app.command("share")
def share(
    run_id: str = typer.Argument(..., help="Run ID"),
    expires_days: int = typer.Option(7, "--expires", help="Expiry in days (1-30)"),
    layout: str = typer.Option("executive", "--layout"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Create a time-limited share link for a run report."""
    try:
        data = asyncio.run(client.request(
            "POST", f"/api/v1/reports/runs/{run_id}/share",
            json_body={"expiry_days": expires_days, "layout": layout},
            profile_name=profile_name,
        ))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
