"""Output formatting — table, JSON, YAML modes."""
import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()
error_console = Console(stderr=True)


def print_json(data: Any) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2, default=str))


def print_yaml(data: Any) -> None:
    """Print data as YAML."""
    try:
        import yaml
        print(yaml.dump(data, default_flow_style=False, sort_keys=False))
    except ImportError:
        error_console.print("[yellow]PyYAML not installed — falling back to JSON[/]")
        print_json(data)


def print_table(rows: list[dict], columns: list[str], title: str = "") -> None:
    """Print rows as a Rich table."""
    table = Table(title=title, show_lines=False, pad_edge=False)
    for col in columns:
        table.add_column(col.replace("_", " ").title(), style="cyan" if col == columns[0] else None)

    for row in rows:
        table.add_row(*[str(row.get(col, "—")) for col in columns])

    console.print(table)


def render(data: Any, output_format: str, columns: list[str] | None = None, title: str = "") -> None:
    """Render data in the requested format."""
    if output_format == "json":
        print_json(data)
    elif output_format == "yaml":
        print_yaml(data)
    elif output_format == "table" and isinstance(data, list) and columns:
        print_table(data, columns, title)
    elif output_format == "table" and isinstance(data, dict):
        # Single item → key-value table
        table = Table(show_header=False, pad_edge=False)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(k.replace("_", " ").title(), str(v) if v is not None else "—")
        console.print(table)
    else:
        print_json(data)


def print_success(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def print_error(message: str) -> None:
    error_console.print(f"[red]✗[/] {message}")


def print_warning(message: str) -> None:
    error_console.print(f"[yellow]⚠[/] {message}")
