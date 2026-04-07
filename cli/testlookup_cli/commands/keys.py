"""API key commands — create, list, revoke."""
import asyncio
import typer

from testlookup_cli import client, output

keys_app = typer.Typer(name="keys", help="Manage API keys")


@keys_app.command("create")
def create(
    name: str = typer.Option(..., "--name", "-n", help="Key name/description"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Create a new personal API key."""
    try:
        data = asyncio.run(client.request("POST", "/api/v1/keys", json_body={"name": name}, profile_name=profile_name))
        if output_format == "json":
            output.print_json(data)
        else:
            output.print_success("API key created")
            output.console.print(f"\n  [bold yellow]Raw Key (shown once):[/] {data.get('raw_key', '???')}")
            output.console.print(f"  Key Hint: {data.get('key_hint', '???')}")
            output.console.print(f"  ID: {data.get('id', '???')}\n")
            output.print_warning("Save this key now — it cannot be retrieved again.")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@keys_app.command("list")
def list_keys(
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List your API keys."""
    try:
        data = asyncio.run(client.request("GET", "/api/v1/keys", profile_name=profile_name))
        items = data if isinstance(data, list) else data.get("items", [])
        output.render(items, output_format, columns=["id", "key_hint", "name", "is_active", "created_at", "expires_at"], title="API Keys")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@keys_app.command("revoke")
def revoke(
    key_id: str = typer.Argument(..., help="Key ID to revoke"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Revoke an API key."""
    if not yes:
        confirm = typer.confirm(f"Revoke API key {key_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        asyncio.run(client.request("DELETE", f"/api/v1/keys/{key_id}", profile_name=profile_name))
        output.print_success(f"API key {key_id} revoked")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
