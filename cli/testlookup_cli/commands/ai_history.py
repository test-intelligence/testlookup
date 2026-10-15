"""AI-history commands (AIH-07): ``testlookup ai-history`` and ``testlookup
compare-ai``.

Thin clients over the W03 routes, registered as top-level commands like
``ci-verdict`` (``ai-history`` is also ``testlookup intelligence history``):

* ``ai-history <run>`` -> ``GET /api/v1/runs/{run}/ai-history`` (the stored AI
  versions of the run, newest first); with ``--diff A..B`` the list resolves the
  version numbers to ids and ``GET /api/v1/runs/{run}/ai-history/diff`` diffs them.
* ``compare-ai <run>`` -> ``GET /api/v1/runs/{run}/ai-compare`` (the run's AI
  analysis against the previous run of the same suite and branch, or
  ``--baseline``).

``--json`` prints the REST body verbatim; the review state and AI disclaimer go
to stderr so it stays pipeable. Exit codes: the stable HTTP mapping; a usage
error is 2 before any request; ``--fail-on-verdict-regression`` exits 8
(``EXIT_VERDICT_REGRESSION``) only when the AI verdict got worse.
"""
from __future__ import annotations

import asyncio
import re
import sys
from typing import NoReturn

import typer
from rich.console import Console
from rich.text import Text

from testlookup_cli import ai_history_render, client, output
from testlookup_cli.errors import (
    EXIT_VALIDATION,
    EXIT_VERDICT_REGRESSION,
    exit_code_for,
)

FORMATS = ("table", "json")
#: ``--kind`` -> the REST ``kind``.
KINDS = {
    "digest": "analysis_digest",
    "analysis_digest": "analysis_digest",
    "summary": "run_summary",
    "run_summary": "run_summary",
}
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
#: ``--diff`` resolves version numbers against the whole list (the per-run cap
#: is at most 100 versions per kind, so one page holds every version).
RESOLVE_LIMIT = 100
_DIFF_SPEC = re.compile(r"^\s*v?(\d+)\s*\.\.\s*(v?\d+|latest|live)\s*$", re.IGNORECASE)

DiffTarget = int | str


def parse_diff_spec(spec: str) -> tuple[int, DiffTarget] | None:
    """``1..3`` / ``v1..v3`` / ``2..latest`` / ``1..live`` -> ``(1, 3)`` /
    ``(2, "latest")``; ``None`` for anything else. A must be a version number."""
    match = _DIFF_SPEC.match(spec or "")
    if not match:
        return None
    frm = int(match.group(1))
    raw = match.group(2).lower()
    to: DiffTarget = raw if raw in ("latest", "live") else int(raw.lstrip("v"))
    if frm < 1 or (isinstance(to, int) and to < 1):
        return None
    return frm, to


def _console() -> Console:
    # Off a terminal there is no colour; the legacy-Windows renderer is not
    # wanted, so one body renders the same everywhere (as ``runs timing``).
    return Console(highlight=False, legacy_windows=None if sys.stdout.isatty() else False)


def _print_lines(lines: list[str]) -> None:
    console = _console()
    for line in lines:
        # Text, never markup: a test name of ``[bold]`` is that text.
        console.print(Text(line), soft_wrap=True)


def _glyphs() -> dict:
    return {
        "arrow": output._glyph("→", "->", sys.stdout),
        "separator": output._glyph(" · ", " | ", sys.stdout),
        "clean": output.encodable(sys.stdout),
    }


def _fail(message: str, code: int) -> NoReturn:
    output.print_error(message)
    raise typer.Exit(code)


def _request(path: str, params: dict, profile_name: str | None):
    try:
        return asyncio.run(client.request("GET", path, params=params, profile_name=profile_name))
    except Exception as e:  # noqa: BLE001 - every failure prints and exits with its mapped code
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


def ai_history(
    run_id: str = typer.Argument(..., help="Run ID"),
    kind: str = typer.Option("digest", "--kind", help="digest (the run's AI analysis) | summary (the run summary)."),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", "-n", help=f"How many versions to list (1-{MAX_LIMIT})."),
    diff: str = typer.Option(
        None, "--diff",
        help="Diff two versions: A..B where A is a version number (1 or v1) and B is a number, "
        "'latest' or 'live' (the current data).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the REST body as JSON (same as -o json)."),
    output_format: str = typer.Option("table", "--output", "-o", help="table|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The stored versions of a run's AI analysis: when each was generated, by
    which engine and model, its verdict and how many failing tests it covered.
    With --diff, what changed between two versions (provenance first: an
    engine change, rules vs LLM, is not a product change)."""
    output_format = "json" if as_json else output_format
    if output_format not in FORMATS:
        _fail(f"Unknown format '{output_format}'. Use one of: {', '.join(FORMATS)}.", EXIT_VALIDATION)
    rest_kind = KINDS.get((kind or "").lower())
    if rest_kind is None:
        _fail(f"Unknown --kind '{kind}'. Use digest or summary.", EXIT_VALIDATION)
    if not 1 <= limit <= MAX_LIMIT:
        _fail(f"--limit must be 1-{MAX_LIMIT}, got {limit}.", EXIT_VALIDATION)

    if diff is None:
        body = _request(f"/api/v1/runs/{run_id}/ai-history", {"kind": rest_kind, "limit": limit}, profile_name)
        if output_format == "json":
            output.print_json(body)
        else:
            glyphs = _glyphs()
            _print_lines(ai_history_render.history_lines(
                body if isinstance(body, dict) else {}, separator=glyphs["separator"], clean=glyphs["clean"],
            ))
        output.print_review_notice(body)
        return

    if rest_kind != "analysis_digest":
        _fail("--diff compares versions of the AI analysis; drop --kind summary.", EXIT_VALIDATION)
    spec = parse_diff_spec(diff)
    if spec is None:
        _fail(
            f"--diff must be A..B (A a version number; B a number, 'latest' or 'live'), got '{diff}'.",
            EXIT_VALIDATION,
        )
    frm, to = spec
    listing = _request(
        f"/api/v1/runs/{run_id}/ai-history", {"kind": "analysis_digest", "limit": RESOLVE_LIMIT}, profile_name,
    )
    ids = {
        v.get("version"): v.get("version_id")
        for v in (listing.get("versions") if isinstance(listing, dict) else None) or []
        if isinstance(v, dict) and v.get("version_id")
    }
    stored = ", ".join(f"v{n}" for n in sorted(k for k in ids if isinstance(k, int))) or "none"
    for number in (frm, to):
        if isinstance(number, int) and number not in ids:
            _fail(f"v{number} is not a stored AI version of this run (stored: {stored}).", EXIT_VALIDATION)
    params = {"from": ids[frm], "to": ids[to] if isinstance(to, int) else to}
    body = _request(f"/api/v1/runs/{run_id}/ai-history/diff", params, profile_name)
    if output_format == "json":
        output.print_json(body)
    else:
        _print_lines(ai_history_render.diff_view_lines(body if isinstance(body, dict) else {}, **_glyphs()))
    output.print_review_notice(body)


def compare_ai(
    run_id: str = typer.Argument(..., help="Run ID"),
    baseline: str = typer.Option(
        None, "--baseline",
        help="Compare with this run instead of the default (the newest completed run of the same suite and "
        "branch before this one).",
    ),
    suite: str = typer.Option(None, "--suite", help="The suite whose previous run is the baseline."),
    fail_on_verdict_regression: bool = typer.Option(
        False, "--fail-on-verdict-regression",
        help=f"Exit {EXIT_VERDICT_REGRESSION} when the AI verdict got worse than the baseline's (for CI). "
        "No baseline, or a side without AI analysis, cannot be judged: a warning, exit 0.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the REST body as JSON (same as -o json)."),
    output_format: str = typer.Option("table", "--output", "-o", help="table|json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """What changed in the AI analysis since the previous run: the verdict, new
    and resolved root causes, tests whose category changed. The summary is an
    AI narrative when one was generated, else a deterministic summary (it says
    which)."""
    output_format = "json" if as_json else output_format
    if output_format not in FORMATS:
        _fail(f"Unknown format '{output_format}'. Use one of: {', '.join(FORMATS)}.", EXIT_VALIDATION)
    if baseline is not None and baseline.strip().lower() == run_id.strip().lower():
        _fail("--baseline must be another run than the one compared.", EXIT_VALIDATION)
    params: dict = {}
    if baseline:
        params["baseline_run_id"] = baseline
    if suite:
        params["suite_name"] = suite
    body = _request(f"/api/v1/runs/{run_id}/ai-compare", params, profile_name)

    if output_format == "json":
        output.print_json(body)
    else:
        _print_lines(ai_history_render.compare_lines(body if isinstance(body, dict) else {}, **_glyphs()))
    output.print_review_notice(body)

    if fail_on_verdict_regression:
        direction = ai_history_render.verdict_direction(body)
        if direction == "worse":
            output.print_warning("The AI verdict got worse than the baseline's (--fail-on-verdict-regression).")
            raise typer.Exit(EXIT_VERDICT_REGRESSION)
        if direction is None:
            why = body.get("message") or body.get("reason") if isinstance(body, dict) else None
            output.print_warning(
                "The AI verdict could not be compared with a baseline"
                + (f" ({why})" if why else "")
                + "; --fail-on-verdict-regression does not fail on that."
            )
