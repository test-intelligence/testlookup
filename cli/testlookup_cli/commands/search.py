"""Search command — global search across entities."""
import asyncio

import typer

from testlookup_cli import client, output
from testlookup_cli.errors import exit_code_for

search_app = typer.Typer(name="search", help="Search across TestLookup", invoke_without_command=True)


@search_app.callback(invoke_without_command=True)
def search(
    query: str = typer.Argument(..., help="Search query"),
    entity_types: str = typer.Option(None, "--types", help="Comma-separated entity types"),
    project: str = typer.Option(None, "--project", "-p"),
    page: int = typer.Option(1, "--page"),
    size: int = typer.Option(20, "--size"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Search tests, runs, suites, defects, and more."""
    params: dict = {"q": query, "page": page, "size": size}
    if entity_types:
        params["entity_types"] = entity_types
    if project:
        params["project_id"] = project

    try:
        data = asyncio.run(client.request("GET", "/api/v1/search/global", params=params, profile_name=profile_name))
        items = data.get("items", [])
        exact = data.get("counts_are_exact", True)
        if data.get("result_status") == "partial":
            failed = ", ".join(data.get("failed_entity_types", []))
            detail = f" Unavailable sources: {failed}." if failed else ""
            output.print_warning(f"Search totals are lower bounds.{detail}")
        output.render(
            items, output_format,
            columns=["entity_type", "title", "subtitle", "relevance_score", "navigation_url"],
            title=f'Search: "{query}" ({data.get("total", 0)}{"+" if not exact else ""} results)',
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
