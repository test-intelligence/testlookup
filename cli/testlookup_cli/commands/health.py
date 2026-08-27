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

    A 503 that arrives with no ``checks`` at all is a different failure and is
    called out as such. Readiness is gated on this same endpoint, so when a
    critical dependency goes down every replica is pulled from the Service and
    a proxy answers instead of the app. "A dependency is down" and "there is no
    server left to ask" both surface as 503, and an operator needs to tell them
    apart: the first is degraded, the second is an outage.
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
        if "503" in str(e):
            output.print_error(
                "No per-dependency detail came back, so this did not come from "
                "the application. Readiness is gated on /health/ready, so a "
                "critical dependency being down takes every replica out of the "
                "Service and a proxy answers instead. Check the pods and the "
                "dependency itself, not just the API."
            )
        raise typer.Exit(1)

    output.render(data, output_format)
    # "ready" ⇒ all critical dependencies up. Anything else (a 503 not_ready
    # body, or an unexpected shape) is a failure for exit-code purposes.
    if not (isinstance(data, dict) and data.get("status") == "ready"):
        raise typer.Exit(1)
