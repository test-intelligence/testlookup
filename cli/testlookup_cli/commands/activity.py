"""``testlookup activity list|show|types|export`` — a project's activity ledger (W18 DEV-04).

Reads the routes the project's Activity tab reads (``GET /api/v1/projects/{id}/activity``,
keyset-paged newest first; ``…/activity/{event_id}``; ``/activity/event-types``;
``…/activity/export``), so the CLI prints the same events in the same order. A
page that has more says so ("N shown, more — --all or --cursor …"): never a
silent cap. Export is QA lead on the project, as in the UI.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from testlookup_cli import client, output, release_resolver
from testlookup_cli.errors import EXIT_VALIDATION, exit_code_for

activity_app = typer.Typer(name="activity", help="A project's activity ledger: who changed what, and when.")

_SINCE = re.compile(r"^(\d+)([hdw])$")
#: --all stops after this many pages (200 events each), and says so.
MAX_PAGES = 50


def since_to_iso(value: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """``7d`` / ``24h`` / ``2w`` -> an ISO timestamp; an ISO date/time passes through."""
    if not value:
        return None
    m = _SINCE.match(value.strip())
    if not m:
        return value
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return ((now or datetime.now(timezone.utc)) - delta).isoformat()


def _fail(e: Exception) -> None:
    output.print_error(str(e))
    raise typer.Exit(exit_code_for(e))


def _actor(e: dict) -> str:
    a = e.get("actor") or {}
    return a.get("label") or a.get("name") or a.get("type") or "-"


def _entity(e: dict) -> str:
    ent = e.get("entity") or {}
    return ent.get("label") or ent.get("name") or ent.get("type") or "-"


@activity_app.command("list")
def list_activity(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    category: list[str] = typer.Option(None, "--category", help="Repeatable, e.g. run, release, defect (see `activity types`)."),
    event_type: list[str] = typer.Option(None, "--event", help="Repeatable event type, e.g. release.gate_evaluated."),
    since: str = typer.Option(None, "--since", help="7d, 24h, 2w, or an ISO date/time."),
    until: str = typer.Option(None, "--until", help="An ISO date/time."),
    actor_type: str = typer.Option(None, "--actor-type", help="user|api_key|service_account|system|agent"),
    release: str = typer.Option(None, "--release", help="Release id or name."),
    query: str = typer.Option(None, "--q", help="Free text over the summary (3+ characters)."),
    limit: int = typer.Option(50, "--limit", help="Events per page (1-200)."),
    cursor: str = typer.Option(None, "--cursor", help="Continue from a previous page's cursor."),
    fetch_all: bool = typer.Option(False, "--all", help=f"Follow the cursor to the end (stops after {MAX_PAGES} pages, and says so)."),
    output_format: str = typer.Option("table", "--format", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The project's activity, newest first — the Activity tab's list."""
    if not 1 <= limit <= 200:
        output.print_error("--limit takes 1-200.")
        raise typer.Exit(EXIT_VALIDATION)

    async def go() -> tuple[list[dict], Optional[str], bool]:
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        rid = await release_resolver.resolve_release(release, project_id=pid, profile_name=profile_name) if release else None
        params: dict[str, Any] = {"limit": limit}
        for key, value in (("category", category), ("event_type", event_type), ("since", since_to_iso(since)),
                           ("until", until), ("actor_type", actor_type), ("release_id", rid), ("q", query)):
            if value:
                params[key] = list(value) if isinstance(value, list) else value
        items: list[dict] = []
        nxt, pages, stopped = cursor, 0, False
        while True:
            if nxt:
                params["cursor"] = nxt
            body = await client.request("GET", f"/api/v1/projects/{pid}/activity", params=params, profile_name=profile_name)
            items += body.get("items", []) if isinstance(body, dict) else []
            nxt = body.get("next_cursor") if isinstance(body, dict) else None
            pages += 1
            if not fetch_all or not nxt:
                break
            if pages >= MAX_PAGES:
                stopped = True
                break
        return items, nxt, stopped

    try:
        items, nxt, stopped = asyncio.run(go())
    except Exception as e:
        _fail(e)
    if output_format != "table":
        output.render({"items": items, "next_cursor": nxt}, output_format)
        return
    rows = [{"when": e.get("occurred_at"), "event": e.get("event_type"), "actor": _actor(e),
             "entity": _entity(e), "summary": e.get("summary"), "id": e.get("id")} for e in items]
    output.print_table(rows, ["when", "event", "actor", "entity", "summary", "id"], title=f"Activity ({len(rows)})")
    if nxt:
        note = f"stopped after {MAX_PAGES} pages" if stopped else "more events"
        output.print_warning(f"{len(rows)} shown, {note} — pass --all, or --cursor {nxt}")


@activity_app.command("show")
def show_event(
    event_id: str = typer.Argument(..., help="Event id (from `activity list`)."),
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """One event with its before / after diff."""
    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        return await client.request("GET", f"/api/v1/projects/{pid}/activity/{event_id}", profile_name=profile_name)

    try:
        e = asyncio.run(go())
    except Exception as exc:
        _fail(exc)
    if as_json or not isinstance(e, dict):
        output.print_json(e)
        return
    lines = [f"{e.get('event_type')}  {e.get('occurred_at')}", f"  actor   {_actor(e)}", f"  entity  {_entity(e)}",
             f"  summary {e.get('summary')}"]
    diff = e.get("diff") or {}
    before, after = diff.get("before") or e.get("before") or {}, diff.get("after") or e.get("after") or {}
    fields = diff.get("changed_fields") or e.get("changed_fields") or sorted(set(before) | set(after))
    if fields:
        lines.append("  changes")
        for f in fields:
            lines.append(f"    {f}: {before.get(f, '—')} -> {after.get(f, '—')}")
    else:
        lines.append("  changes  (none recorded)")
    typer.echo("\n".join(lines))


@activity_app.command("types")
def event_types(
    as_json: bool = typer.Option(False, "--json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Every event type, by category (the filter vocabulary)."""
    try:
        body = asyncio.run(client.request("GET", "/api/v1/activity/event-types", profile_name=profile_name))
    except Exception as e:
        _fail(e)
    if as_json:
        output.print_json(body)
        return
    events = body.get("events") or {}
    rows = []
    for cat, evs in (events.items() if isinstance(events, dict) else []):
        for ev in evs if isinstance(evs, list) else []:
            rows.append({"category": cat, "event": ev.get("event_type") if isinstance(ev, dict) else ev})
    output.print_table(rows, ["category", "event"], title=f"Activity event types ({len(rows)})")


@activity_app.command("export")
def export_activity(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    out: Path = typer.Option(..., "--output", "-o", help="Where to write the file."),
    fmt: str = typer.Option("csv", "--format", help="csv|ndjson"),
    since: str = typer.Option(None, "--since", help="7d, 24h, 2w, or an ISO date/time."),
    category: list[str] = typer.Option(None, "--category"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The filtered history as CSV or NDJSON (QA lead on the project)."""
    if fmt not in ("csv", "ndjson"):
        output.print_error("--format takes csv or ndjson.")
        raise typer.Exit(EXIT_VALIDATION)

    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        params: dict[str, Any] = {"format": fmt}
        if since:
            params["since"] = since_to_iso(since)
        if category:
            params["category"] = list(category)
        return await client.download(f"/api/v1/projects/{pid}/activity/export", params=params, profile_name=profile_name)

    try:
        data = asyncio.run(go())
    except Exception as e:
        _fail(e)
    out.write_bytes(data)
    output.print_success(f"Saved {len(data)} bytes of activity to {out}.")
