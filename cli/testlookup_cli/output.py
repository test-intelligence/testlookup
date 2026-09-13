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


def _glyph(preferred: str, fallback: str, stream) -> str:
    """Return ``preferred`` if the stream can encode it, else ``fallback``.

    ``auth login`` exited 1 with a UnicodeEncodeError on a Windows cp1252
    console — *after* the login succeeded and the profile was saved. The
    credentials were fine; the command reported failure anyway, and a CI
    wrapper reads the exit code, not the profile on disk.

    Rich already degrades its own rendering (a full table renders under cp1252
    because it substitutes ASCII box-drawing), but a literal glyph handed to it
    inside markup is just text, and Rich passes it straight through to an
    encoder that cannot represent it.

    Deliberately not a global ``sys.stdout.reconfigure``: mutating the
    process's streams from a library import has a far larger blast radius than
    choosing a character, and would change byte-for-byte output for every
    consumer of this CLI.

    A stream with no ``encoding`` (StringIO, pytest capture) is treated as
    capable — degrading output for every harness that captures stdout would be
    the wrong trade.
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return preferred
    try:
        preferred.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return fallback
    return preferred


def print_success(message: str) -> None:
    console.print(f"[green]{_glyph('✓', '[OK]', sys.stdout)}[/] {message}")


def print_error(message: str) -> None:
    error_console.print(f"[red]{_glyph('✗', '[X]', sys.stderr)}[/] {message}")


def print_warning(message: str) -> None:
    error_console.print(f"[yellow]{_glyph('⚠', '[!]', sys.stderr)}[/] {message}")


def print_review_notice(data: Any) -> None:
    """Print an AI report's review state and disclaimer to stderr (E8.4).

    Stderr keeps ``--output json`` pipeable while the caveat still reaches the
    person at the terminal. A payload without a review block prints ``unknown``
    and a warning: a missing state must never read as reviewed.
    """
    review = data.get("review") if isinstance(data, dict) else None
    state = review.get("state") if isinstance(review, dict) else None
    if not state:
        error_console.print(
            "Review state: unknown. Treat any AI content as an unreviewed draft.",
            markup=False, highlight=False,
        )
        return
    error_console.print(f"Review state: {state}", markup=False, highlight=False)
    if review.get("message"):
        error_console.print(str(review["message"]), markup=False, highlight=False)
    if data.get("ai_disclaimer"):
        error_console.print(str(data["ai_disclaimer"]), markup=False, highlight=False)
