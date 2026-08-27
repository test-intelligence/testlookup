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
    """Check TestLookup server health.

    Proxies ``GET /health/ready``. When a critical dependency (PostgreSQL,
    MongoDB, or Redis) is down the server answers ``503`` with a per-dependency
    ``checks`` object naming which service failed. This renders that body — so
    the operator sees *which* dependency is down — and exits non-zero, instead
    of collapsing it to an opaque ``"Server error (503)."`` that hides the
    reason. The non-zero exit keeps the command usable as a CI preflight.
    """
    try:
        # raise_for_status=False so a 503 "not_ready" body survives to render;
        # a genuinely unreachable server still raises a mapped CLIError below.
        data = asyncio.run(
            client.request(
                "GET", "/health/ready", profile_name=profile_name, raise_for_status=False
            )
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)

    output.render(data, output_format)
    # "ready" ⇒ all critical dependencies up. Anything else (a 503 not_ready
    # body, or an unexpected shape) is a failure for exit-code purposes.
    if not (isinstance(data, dict) and data.get("status") == "ready"):
        raise typer.Exit(1)
