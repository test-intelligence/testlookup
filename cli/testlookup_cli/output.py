"""Output formatting — table, JSON, YAML modes."""
import json
import re
import sys
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

console = Console()
error_console = Console(stderr=True)

# C0 and C1 control characters and DEL, except newline and tab. ESC (0x1b) and
# CSI (0x9b) start terminal escape sequences; a carriage return or backspace
# lets one line overwrite another.
_CONTROL_CODE_POINTS = (*range(0x00, 0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0))
_CONTROL_CHARACTERS = re.compile("[" + "".join(re.escape(chr(code)) for code in _CONTROL_CODE_POINTS) + "]")


def plain(value: Any) -> str:
    """``value`` as text with the control characters removed.

    Test names, suite names, error details and AI summaries come from the
    server, and most were first written by whoever ingested a run. Printed
    as they are, an ESC in a test name could clear the reader's screen or set
    the terminal title. Use this for anything printed without Rich
    (``typer.echo``) or wrapped in a Rich ``Text``.
    """
    return _CONTROL_CHARACTERS.sub("", str(value))


def safe(value: Any) -> str:
    """``value`` made safe to put inside a Rich markup string.

    Control characters are removed (see ``plain``) and markup is escaped, so
    ``[link=...]`` in a test name prints as those characters instead of
    making a hyperlink, and a stray ``[/]`` cannot crash the command. Use it
    for every server value interpolated into a string that Rich will parse:
    ``console.print(f"[bold]Build[/] {safe(build)}")``.
    """
    return escape(plain(value))


def _literal(target: Console, value: Any) -> Text:
    """``value`` as a Rich ``Text`` that is never parsed as markup.

    Used for table cells and messages. The console still highlights numbers
    and the like as it does for a plain string; only markup and emoji codes
    in the data are left alone.
    """
    return target.render_str(plain(value), markup=False, emoji=False)


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
    # The title can carry what the user typed or a server name: text, in the
    # style Rich gives a table title.
    heading = (
        console.render_str(plain(title), style="table.title", markup=False, emoji=False, highlight=False)
        if title else None
    )
    table = Table(title=heading, show_lines=False, pad_edge=False)
    for col in columns:
        table.add_column(col.replace("_", " ").title(), style="cyan" if col == columns[0] else None)

    for row in rows:
        table.add_row(*[_literal(console, row.get(col, "—")) for col in columns])

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
            table.add_row(
                _literal(console, str(k).replace("_", " ").title()),
                _literal(console, v if v is not None else "—"),
            )
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


def encodable(stream):
    """A cleaner that replaces what ``stream`` cannot encode with ``?``.

    For ingested names (a CJK suite name) printed on a console whose code page
    cannot hold them: the command still prints and exits 0 instead of dying on
    a UnicodeEncodeError after the request succeeded.

    The cleaner also removes control characters (see ``plain``): every
    renderer that prints ingested names through it gets both."""
    encoding = getattr(stream, "encoding", None) or "utf-8"

    def clean(text: str) -> str:
        text = plain(text)
        try:
            return text.encode(encoding, "replace").decode(encoding)
        except LookupError:
            return text.encode("ascii", "replace").decode("ascii")

    return clean


def _print_message(target: Console, glyph: str, colour: str, message: Any) -> None:
    """One coloured glyph, then the message as text.

    The message is never parsed as markup: most messages carry a server error
    detail or a name from the data (see ``plain``)."""
    target.print(Text.assemble((glyph, colour), " ", _literal(target, message)))


def print_success(message: str) -> None:
    _print_message(console, _glyph('✓', '[OK]', sys.stdout), "green", message)


def print_error(message: str) -> None:
    _print_message(error_console, _glyph('✗', '[X]', sys.stderr), "red", message)


def print_warning(message: str) -> None:
    _print_message(error_console, _glyph('⚠', '[!]', sys.stderr), "yellow", message)


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
    error_console.print(f"Review state: {plain(state)}", markup=False, highlight=False)
    if review.get("message"):
        error_console.print(plain(review["message"]), markup=False, highlight=False)
    if data.get("ai_disclaimer"):
        error_console.print(plain(data["ai_disclaimer"]), markup=False, highlight=False)
