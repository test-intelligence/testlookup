"""VIZ-308 -- the report context block for exports (PDFs, emailed reports).

A report that leaves the product (a PDF attached to a sign-off, an email)
must say which data it shows, the way the on-screen report chrome does
(VIZ-301): Project, Release, Test Suite, Window, Aggregation, Pass-rate basis,
Generated (UTC) and the "N of M" line. Every value comes from the response's
``meta`` (contract C2, :func:`app.services.analytics_meta.build_meta`): what
the server APPLIED, never what was asked.

Pure functions over the ``meta`` dict; the renderers decide the markup and
escape every value (release and suite names are untrusted text).

Long lists are never cut silently: the context block lists every release and
suite (the renderer wraps them), and the one-line page footer shows the first
few followed by "+N more (listed on page 1)".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

#: Releases/suites named in a one-line footer before the rest are counted.
FOOTER_INLINE_MAX = 3

UNATTRIBUTED_TEXT = "Unattributed runs"

_BASIS_TEXT = {
    "executions": "Pass rate of executions",
    "unique_tests": "Pass rate per unique test",
}

_DIMENSION_TEXT = {"release": "release", "suite": "suite", "window": "time window"}


@dataclass(frozen=True)
class ContextField:
    label: str
    value: str


def _release_name(release: dict) -> str:
    rid = str(release.get("id") or "")
    if rid == "unattributed":
        return UNATTRIBUTED_TEXT
    name = str(release.get("name") or "").strip() or f"Release {rid[:8]}"
    if str(release.get("status") or "").lower() == "archived":
        return f"{name} (archived)"
    return name


def _project_names(meta: dict) -> list[str]:
    return [str(p.get("name") or p.get("id") or "") for p in meta.get("scope", {}).get("projects") or []]


def _release_names(meta: dict) -> list[str]:
    return [_release_name(r) for r in meta.get("scope", {}).get("releases") or []]


def _suite_names(meta: dict) -> list[str]:
    return [str(s) for s in meta.get("scope", {}).get("suites") or []]


def window_text(meta: dict) -> str:
    """"Last 30 days (2026-08-20 to 2026-09-19 UTC)"."""
    window = meta.get("scope", {}).get("window") or {}
    days = int(window.get("days") or 0)
    head = "Last 24 hours" if days == 1 else f"Last {days} days"
    start, end = window.get("from"), window.get("to")
    return f"{head} ({start} to {end} UTC)" if start and end else head


def generated_text(meta: dict) -> Optional[str]:
    """"2026-09-19 17:57 UTC" from ``generated_at``."""
    raw = meta.get("generated_at")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return str(raw)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def context_fields(meta: dict, *, aggregation: Optional[str] = None) -> list[ContextField]:
    """The context block, in the chrome's order. Every list is complete."""
    projects = _project_names(meta)
    releases = _release_names(meta)
    suites = _suite_names(meta)
    fields = [
        ContextField("Project", ", ".join(projects) if projects else "No project"),
        ContextField("Release", ", ".join(releases) if releases else "All releases"),
        ContextField("Test Suite", ", ".join(suites) if suites else "All suites"),
        ContextField("Window", window_text(meta)),
    ]
    if aggregation:
        fields.append(ContextField("Aggregation", aggregation))
    basis = _BASIS_TEXT.get(str(meta.get("pass_rate_basis") or ""))
    if basis:
        fields.append(ContextField("Pass-rate basis", basis))
    generated = generated_text(meta)
    if generated:
        fields.append(ContextField("Generated (UTC)", generated))
    for entry in meta.get("ignored_filters") or []:
        dimension = _DIMENSION_TEXT.get(str(entry.get("dimension")), str(entry.get("dimension")))
        fields.append(ContextField("Not filtered by", f"{dimension}: {entry.get('reason') or ''}".strip()))
    return fields


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def n_of_m_text(meta: dict) -> str:
    """"Showing 18 of 143 runs · 412 of 3,960 executions" (all, when nothing is excluded)."""
    totals = meta.get("totals") or {}
    runs, all_runs = int(totals.get("matched_runs") or 0), int(totals.get("total_runs") or 0)
    execs, all_execs = int(totals.get("matched_executions") or 0), int(totals.get("total_executions") or 0)
    if runs == all_runs and execs == all_execs:
        return f"Showing all {_plural(runs, 'run')} · {_plural(execs, 'execution')}"
    return f"Showing {runs:,} of {_plural(all_runs, 'run')} · {execs:,} of {_plural(all_execs, 'execution')}"


def _inline(values: list[str], all_text: str, inline_max: int) -> str:
    if not values:
        return all_text
    shown = values[:inline_max]
    rest = len(values) - len(shown)
    if not shown:
        return f"{rest} (listed on page 1)"
    return ", ".join(shown) + (f" +{rest} more (listed on page 1)" if rest > 0 else "")


def footer_text(meta: dict, inline_max: int = FOOTER_INLINE_MAX) -> str:
    """The one-line footer every page repeats: Project, Release and Suite.

    ASCII separators: the footer is drawn straight onto the canvas, whose base
    font has no glyph a PDF reader reliably maps for a middle dot. A renderer
    that needs it shorter asks again with a smaller ``inline_max`` (names fewer
    values, still counting the rest), rather than cutting the line.
    """
    projects = _project_names(meta)
    return " | ".join(
        [
            f"Project: {_inline(projects, 'No project', inline_max)}",
            f"Release: {_inline(_release_names(meta), 'All releases', inline_max)}",
            f"Suite: {_inline(_suite_names(meta), 'All suites', inline_max)}",
        ]
    )


def has_context(meta: Any) -> bool:
    """A usable ``meta`` (an older cached payload has none)."""
    return isinstance(meta, dict) and isinstance(meta.get("scope"), dict)
