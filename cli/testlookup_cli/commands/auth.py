"""Auth commands — login, logout, whoami."""
import asyncio
import typer
from rich.prompt import Prompt

from testlookup_cli import client, config, output

auth_app = typer.Typer(name="auth", help="Authenticate with TestLookup")


@auth_app.command()
def login(
    url: str = typer.Option(None, "--url", help="TestLookup server URL (defaults to TESTLOOKUP_URL / active profile)"),
    username: str = typer.Option(None, "--username", "-u", help="Username (prompted if omitted)"),
    password: str = typer.Option(None, "--password", "-p", help="Password (prompted if omitted)"),
    profile_name: str = typer.Option(None, "--profile", help="Profile name to save as"),
):
    """Log in to TestLookup and save credentials to a profile."""
    if not username:
        username = Prompt.ask("Username")
    if not password:
        password = Prompt.ask("Password", password=True)

    name = profile_name or config.get_active_profile_name()
    # Resolve like every other command does. Hardcoding localhost here meant
    # TESTLOOKUP_URL was honoured everywhere except the one command that
    # establishes the session.
    url = url or config.get_profile(profile_name).get("url")

    try:
        data = asyncio.run(client.login(url, username, password))
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(3)

    profile = {
        "url": url,
        "auth_type": "jwt",
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
    }
    config.save_profile(name, profile)
    config.set_active_profile(name)
    output.print_success(f"Logged in as {username} (profile: {name})")


@auth_app.command()
def logout(
    profile_name: str = typer.Option(None, "--profile", help="Profile to clear"),
):
    """Clear stored credentials for the active profile."""
    name = profile_name or config.get_active_profile_name()
    profile = config.get_profile(name)
    profile.pop("access_token", None)
    profile.pop("refresh_token", None)
    config.save_profile(name, profile)
    output.print_success(f"Logged out (profile: {name})")


@auth_app.command()
def whoami(
    profile_name: str = typer.Option(None, "--profile", help="Profile to inspect"),
    output_format: str = typer.Option("table", "--output", "-o", help="Output format: table|json"),
):
    """Show the currently active profile and authentication state."""
    name = profile_name or config.get_active_profile_name()
    profile = config.get_profile(name)

    info = {
        "profile": name,
        "url": profile.get("url", "—"),
        "auth_type": profile.get("auth_type", "—"),
        "authenticated": bool(profile.get("access_token") or profile.get("api_key")),
    }
    output.render(info, output_format)
