"""TestLookup CLI — root application and command registration."""
import sys

import typer

from testlookup_cli import __version__
from testlookup_cli.commands.auth import auth_app
from testlookup_cli.commands.health import health_app
from testlookup_cli.commands.projects import projects_app
from testlookup_cli.commands.runs import runs_app
from testlookup_cli.commands.tests import tests_app
from testlookup_cli.commands.search import search_app
from testlookup_cli.commands.intelligence import intelligence_app
from testlookup_cli.commands.deep import deep_app
from testlookup_cli.commands.reports import reports_app
from testlookup_cli.commands.keys import keys_app
from testlookup_cli.commands.upload import upload_app

app = typer.Typer(
    name="testlookup",
    help="TestLookup CLI — QA intelligence from your terminal",
    no_args_is_help=True,
)


# ANSI escapes that mirror the GUI's bracketed wordmark exactly:
# blue brackets (#4493f8 ≈ 256-color slot 75), bold "testlookup", dim suffix.
# Auto-disabled when stdout is not a TTY (piping to a file, CI logs without
# colour, etc.) so test output stays clean.
def _banner_for_version() -> str:
    if sys.stdout.isatty():
        BLUE = "\033[38;5;75m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RESET = "\033[0m"
        return f"{BLUE}[{RESET}{BOLD}testlookup{RESET}{BLUE}]{RESET} {DIM}v{__version__} · local-first{RESET}"
    return f"[testlookup] v{__version__} · local-first"


def version_callback(value: bool):
    if value:
        typer.echo(_banner_for_version())
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-V", callback=version_callback, is_eager=True, help="Show version"),
):
    """TestLookup CLI — inspect runs, tests, intelligence, and reports from your terminal."""


# Register command groups
app.add_typer(auth_app, name="auth")
app.add_typer(health_app, name="health")
app.add_typer(projects_app, name="projects")
app.add_typer(runs_app, name="runs")
app.add_typer(tests_app, name="tests")
app.add_typer(search_app, name="search")
app.add_typer(intelligence_app, name="intelligence")
app.add_typer(deep_app, name="deep")
app.add_typer(reports_app, name="reports")
app.add_typer(keys_app, name="keys")
app.add_typer(upload_app, name="upload")


if __name__ == "__main__":
    app()
