"""Health check command."""
import asyncio
import typer

from testlookup_cli import client, output

health_app = typer.Typer(name="health", help="Check server health", invoke_without_command=True)


@health_app.callback(invoke_without_command=True)
def health(
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Check TestLookup server health."""
    try:
        data = asyncio.run(client.request("GET", "/health/ready", profile_name=profile_name))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
