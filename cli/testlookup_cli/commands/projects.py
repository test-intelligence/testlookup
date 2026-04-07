"""Project commands — list, get."""
import asyncio
import typer

from testlookup_cli import client, output

projects_app = typer.Typer(name="projects", help="Manage projects")


@projects_app.command("list")
def list_projects(
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List all accessible projects."""
    try:
        data = asyncio.run(client.request("GET", "/api/v1/projects", profile_name=profile_name))
        items = data if isinstance(data, list) else data.get("items", data)
        output.render(items, output_format, columns=["id", "name", "description", "created_at"], title="Projects")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("get")
def get_project(
    project_id: str = typer.Argument(..., help="Project ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Get project details."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/projects/{project_id}", profile_name=profile_name))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)
