"""Auth commands — login, logout, whoami."""
import asyncio
import os
import sys

import typer
from rich.prompt import Prompt

from testlookup_cli import client, config, output

auth_app = typer.Typer(name="auth", help="Authenticate with TestLookup")


@auth_app.command()
def login(
    url: str = typer.Option(None, "--url", help="TestLookup server URL (defaults to TESTLOOKUP_URL / active profile)"),
    username: str = typer.Option(None, "--username", "-u", help="Username (prompted if omitted)"),
    password: str = typer.Option(
        None, "--password", "-p",
        help=(
            "Password. Discouraged: other users of this machine and CI logs can "
            "see the command line. Use --password-stdin, or omit both to be prompted."
        ),
    ),
    password_stdin: bool = typer.Option(
        False, "--password-stdin",
        help="Read the password from stdin (needs --username), e.g. `printenv TL_PASSWORD | testlookup auth login -u ci --password-stdin`.",
    ),
    profile_name: str = typer.Option(None, "--profile", help="Profile name to save as"),
):
    """Log in to TestLookup and save credentials to a profile."""
    if password_stdin:
        if password:
            raise typer.BadParameter("Give --password or --password-stdin, not both.")
        if not username:
            raise typer.BadParameter(
                "--password-stdin needs --username: stdin carries the password, "
                "so the username cannot be prompted for."
            )
        # One line, as `docker login --password-stdin`: the line ending is not
        # part of the password.
        password = sys.stdin.read().rstrip("\r\n")
        if not password:
            raise typer.BadParameter("No password on stdin. Pipe it in, or omit --password-stdin to be prompted.")
    elif password:
        output.print_warning(
            "--password puts the password on the command line, where other users "
            "of this machine and CI logs can see it. Use --password-stdin or the prompt."
        )
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
    """End the server session and remove the profile's stored credentials.

    Removes the access token, the refresh token and a stored API key, and
    keeps the profile's URL. A profile that is not saved (the credentials come
    from TESTLOOKUP_API_KEY) has nothing on disk to remove, and nothing is written.
    """
    name = profile_name or config.get_active_profile_name()
    profile = config.get_saved_profile(name)
    if profile is None:
        # Never save here: get_profile() would hand back the environment's API
        # key, and saving it would put a key on disk that was never stored.
        note = (
            " TESTLOOKUP_API_KEY is set in the environment; unset it there to stop using it."
            if os.environ.get("TESTLOOKUP_API_KEY") else ""
        )
        output.print_warning(f"No saved credentials for profile {name}: nothing was removed.{note}")
        return

    session_ended = False
    if profile.get("access_token") or profile.get("refresh_token"):
        try:
            asyncio.run(client.logout(
                profile.get("url", ""), profile.get("access_token"), profile.get("refresh_token"),
            ))
            session_ended = True
        except Exception as e:  # noqa: BLE001 - best effort: the local cleanup always runs
            output.print_warning(
                f"Could not end the session on the server ({e}). The stored tokens are "
                "removed from this machine, but the session stays valid on the server "
                "until it expires."
            )

    labels = {"access_token": "access token", "refresh_token": "refresh token", "api_key": "API key"}
    removed = [label for field, label in labels.items() if profile.pop(field, None)]
    config.save_profile(name, profile)

    if not removed:
        output.print_success(f"Logged out (profile: {name}). It held no stored credentials.")
        return
    output.print_success(f"Logged out (profile: {name}). Removed from this machine: {', '.join(removed)}.")
    if session_ended:
        output.print_success("The session was ended on the server.")
    if "API key" in removed:
        output.print_warning(
            "The API key is still valid on the server. Revoke it with "
            "`testlookup keys revoke <id>` or in Settings if it should stop working."
        )


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
