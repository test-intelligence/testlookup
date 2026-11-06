"""``testlookup cases list|get|history|export`` — a project's authored test cases, read-only (W34 STRAT-11).

Reads the routes the Test Management page reads (``GET /api/v1/test-management/cases``,
``…/cases/{case_id}``, ``…/cases/{case_id}/history`` and the Excel export
``…/cases/export/excel``), so the CLI lists the same cases in the same order
(newest first). The group is ``cases`` because ``tests`` is a run's results.

``--project`` (id or name) is required on ``list`` and ``export``: a non-admin
who names no project gets an empty list from the server, never an error. A page
that has more says so ("N shown of M, more — --all or --page …"): never a silent
cap. ``export`` sends only the Excel route's own filters (it has no suite and no
include-archived: the workbook holds every status, as the page's export does).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from testlookup_cli import client, output, release_resolver
from testlookup_cli.errors import EXIT_VALIDATION, CLIError, exit_code_for

cases_app = typer.Typer(name="cases", help="A project's authored test cases (Test Management), read-only.")

#: --all stops after this many pages, and says so.
MAX_PAGES = 50
#: The list route's largest page (``size`` 1-200).
MAX_SIZE = 200
DEFAULT_SIZE = 25
#: The UI prints a case as ``TC-`` and the first six characters of its id.
SHORT_ID = "TC-"
FORMATS = ("table", "json", "yaml")


def _fail(e: Exception) -> None:
    output.print_error(str(e))
    raise typer.Exit(exit_code_for(e))


def _check_format(output_format: str) -> None:
    if output_format not in FORMATS:
        output.print_error(f"Unknown format '{output_format}'. Use one of: {', '.join(FORMATS)}.")
        raise typer.Exit(EXIT_VALIDATION)


def short_id(case_id: Any) -> str:
    """``TC-1A2B3C``, as the Test Management page prints a case."""
    return f"{SHORT_ID}{str(case_id or '')[:6].upper()}"


def list_params(
    *,
    project_id: str,
    statuses: Optional[list[str]] = None,
    test_types: Optional[list[str]] = None,
    priorities: Optional[list[str]] = None,
    suite: Optional[str] = None,
    search: Optional[str] = None,
    owner: Optional[str] = None,
    automation: Optional[str] = None,
    requirement: Optional[str] = None,
    include_archived: bool = False,
    page: int = 1,
    size: int = DEFAULT_SIZE,
) -> dict:
    """``cases list``'s query string, with the REST names; an unused option is
    not sent (the route's own default applies)."""
    params: dict[str, Any] = {"project_id": project_id, "page": page, "size": size}
    if statuses:
        params["status"] = list(statuses)
    if test_types:
        params["test_type"] = list(test_types)
    if priorities:
        params["priority"] = list(priorities)
    if suite:
        params["suite_name"] = suite
    if search:
        params["search"] = search
    if owner:
        params["owner"] = owner
    if automation:
        params["automation"] = automation
    if requirement:
        params["requirement"] = requirement
    if include_archived:
        params["include_archived"] = True
    return params


def export_params(
    *,
    project_id: str,
    statuses: Optional[list[str]] = None,
    test_types: Optional[list[str]] = None,
    priorities: Optional[list[str]] = None,
    search: Optional[str] = None,
    owner: Optional[str] = None,
    automation: Optional[str] = None,
    requirement: Optional[str] = None,
) -> dict:
    """``cases export``'s query string: ONLY the Excel route's filters. It
    reads no ``suite_name`` and no ``include_archived`` (FastAPI would drop
    them without a word and the workbook would hold every suite)."""
    params: dict[str, Any] = {"project_id": project_id}
    if statuses:
        params["status"] = list(statuses)
    if test_types:
        params["test_type"] = list(test_types)
    if priorities:
        params["priority"] = list(priorities)
    if search:
        params["search"] = search
    if owner:
        params["owner"] = owner
    if automation:
        params["automation"] = automation
    if requirement:
        params["requirement"] = requirement
    return params


async def _pages(params: dict, *, fetch_all: bool, profile_name: Optional[str]) -> dict:
    """One page, or (``fetch_all``) every page up to ``MAX_PAGES``."""
    params = dict(params)
    items: list[dict] = []
    total, pages, fetched, stopped = 0, 0, 0, False
    page = int(params.get("page") or 1)
    while True:
        params["page"] = page
        body = await client.request("GET", "/api/v1/test-management/cases", params=params, profile_name=profile_name)
        body = body if isinstance(body, dict) else {}
        items += [c for c in body.get("items") or [] if isinstance(c, dict)]
        total, pages = int(body.get("total") or 0), int(body.get("pages") or 0)
        fetched += 1
        if page >= pages or not fetch_all:
            break
        if fetched >= MAX_PAGES:
            stopped = True
            break
        page += 1
    return {"items": items, "total": total, "pages": pages,
            "next_page": page + 1 if page < pages else None, "stopped": stopped}


async def resolve_case(value: str, *, project_id: Optional[str], profile_name: Optional[str]) -> str:
    """A case id from its id, or from the ``TC-`` short id the UI prints.

    A short id is searched in the project's cases (archived included) and must
    match exactly one case id by prefix; none or several is an error naming
    the candidates (exit 2), never a guess.
    """
    value = (value or "").strip()
    if release_resolver.is_uuid(value):
        return value
    if not value.upper().startswith(SHORT_ID) or not value[len(SHORT_ID):]:
        raise CLIError(
            f"'{value}' is not a case id: pass the case's id, or its {SHORT_ID} short id as the UI shows it.",
            EXIT_VALIDATION,
        )
    if not project_id:
        raise CLIError(f"A {SHORT_ID} short id needs --project: it is looked up in that project's cases.", EXIT_VALIDATION)
    prefix = value[len(SHORT_ID):].lower()
    params = list_params(project_id=project_id, search=value, include_archived=True, size=MAX_SIZE)
    found = await _pages(params, fetch_all=True, profile_name=profile_name)
    hits = [c for c in found["items"] if str(c.get("id") or "").lower().startswith(prefix)]
    if len(hits) == 1:
        return str(hits[0]["id"])
    if hits:
        lines = "\n".join(f"  - {short_id(c.get('id'))} {c.get('title')} ({c.get('status')}) {c.get('id')}" for c in hits)
        raise CLIError(f"{len(hits)} cases start with {value.upper()}. Pass the full id:\n{lines}", EXIT_VALIDATION)
    raise CLIError(
        f"No case {value.upper()} in this project (a deprecated case needs its full id).", EXIT_VALIDATION,
    )


def _automated(c: dict) -> str:
    return "yes" if c.get("is_automated") else "no"


@cases_app.command("list")
def list_cases(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name (required)."),
    statuses: list[str] = typer.Option(None, "--status", help="Repeatable lifecycle state: draft, review_requested, under_review, approved, active, rejected, needs_update, deprecated, archived."),
    test_types: list[str] = typer.Option(None, "--type", help="Repeatable test type, e.g. functional, regression."),
    priorities: list[str] = typer.Option(None, "--priority", help="Repeatable: critical, high, medium, low (or P0-P3)."),
    suite: str = typer.Option(None, "--suite", help="Suite name (exact, case-insensitive)."),
    search: str = typer.Option(None, "--search", help="Title, description, tags, a TC- id prefix, or a requirement key."),
    owner: str = typer.Option(None, "--owner", help="'me' or a user id."),
    automation: str = typer.Option(None, "--automation", help="automated|not_automated"),
    requirement: str = typer.Option(None, "--requirement", help="Only cases linked to this requirement key."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Also list archived cases (deprecated ones need --status deprecated)."),
    page: int = typer.Option(1, "--page", help="Page to read (from 1)."),
    size: int = typer.Option(None, "--size", help=f"Cases per page (1-{MAX_SIZE}; default {DEFAULT_SIZE}, or {MAX_SIZE} with --all)."),
    fetch_all: bool = typer.Option(False, "--all", help=f"Read every page from --page on (stops after {MAX_PAGES} pages, and says so)."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The project's authored cases, newest first — the Test Management list."""
    _check_format(output_format)
    size = size if size is not None else (MAX_SIZE if fetch_all else DEFAULT_SIZE)
    if not 1 <= size <= MAX_SIZE or page < 1:
        output.print_error(f"--size takes 1-{MAX_SIZE} and --page 1 or more.")
        raise typer.Exit(EXIT_VALIDATION)

    async def go() -> dict:
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        params = list_params(
            project_id=pid, statuses=statuses, test_types=test_types, priorities=priorities, suite=suite,
            search=search, owner=owner, automation=automation, requirement=requirement,
            include_archived=include_archived, page=page, size=size,
        )
        return await _pages(params, fetch_all=fetch_all, profile_name=profile_name)

    try:
        found = asyncio.run(go())
    except Exception as e:
        _fail(e)
    items, nxt = found["items"], found["next_page"]
    if output_format != "table":
        output.render({"items": items, "total": found["total"], "pages": found["pages"], "next_page": nxt}, output_format)
        return
    # The short id the UI prints (``cases get TC-… -p …`` resolves it); -o json carries every field.
    rows = [{"case": short_id(c.get("id")), "title": c.get("title"), "status": c.get("status"),
             "priority": c.get("priority"), "suite": c.get("suite_name") or "-", "owner": c.get("owner") or "-"}
            for c in items]
    output.print_table(rows, ["case", "title", "status", "priority", "suite", "owner"],
                       title=f"Test cases ({len(rows)} of {found['total']})")
    if nxt:
        note = f"stopped after {MAX_PAGES} pages" if found["stopped"] else "more cases"
        output.print_warning(f"{len(rows)} shown of {found['total']}, {note} — pass --all, or --page {nxt}")


def _case_lines(c: dict) -> list[str]:
    """One case as text: its fields, preconditions, steps and expected result."""
    lines = [f"{short_id(c.get('id'))}  {c.get('title')}",
             f"  status     {c.get('status')} (version {c.get('version')})",
             f"  priority   {c.get('priority')}   type {c.get('test_type')}   severity {c.get('severity')}",
             f"  suite      {c.get('suite_name') or '-'}",
             f"  owner      {c.get('owner') or '-'}",
             f"  automated  {_automated(c)} ({c.get('automation_status') or '-'})",
             f"  tags       {', '.join(str(t) for t in c.get('tags') or []) or '-'}",
             f"  id         {c.get('id')}"]
    for label, key in (("Objective", "objective"), ("Preconditions", "preconditions")):
        if c.get(key):
            lines.append(f"{label}: {c.get(key)}")
    steps = [s for s in c.get("steps") or [] if isinstance(s, dict)]
    lines.append("Steps" if steps else "Steps: (none)")
    for s in steps:
        expected = f"  -> {s.get('expected_result')}" if s.get("expected_result") else ""
        lines.append(f"  {s.get('step_number')}. {s.get('action')}{expected}")
    if c.get("expected_result"):
        lines.append(f"Expected result: {c.get('expected_result')}")
    return lines


@cases_app.command("get")
def get_case(
    case_id: str = typer.Argument(..., help="Case id, or its TC- short id (needs --project)."),
    project: str = typer.Option(None, "--project", "-p", help="Project id or name (needed for a TC- short id)."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """One authored case with its steps."""
    _check_format(output_format)

    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        cid = await resolve_case(case_id, project_id=pid, profile_name=profile_name)
        return await client.request("GET", f"/api/v1/test-management/cases/{cid}", profile_name=profile_name)

    try:
        case = asyncio.run(go())
    except Exception as e:
        _fail(e)
    if output_format != "table" or not isinstance(case, dict):
        output.render(case, output_format if output_format != "table" else "json")
        return
    clean = output.encodable(sys.stdout)
    typer.echo("\n".join(clean(line) for line in _case_lines(case)))


@cases_app.command("history")
def case_history(
    case_id: str = typer.Argument(..., help="Case id, or its TC- short id (needs --project)."),
    project: str = typer.Option(None, "--project", "-p", help="Project id or name (needed for a TC- short id)."),
    output_format: str = typer.Option("table", "--format", "--output", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The case's saved versions, newest first (at most 100)."""
    _check_format(output_format)

    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        cid = await resolve_case(case_id, project_id=pid, profile_name=profile_name)
        return await client.request("GET", f"/api/v1/test-management/cases/{cid}/history", profile_name=profile_name)

    try:
        versions = asyncio.run(go())
    except Exception as e:
        _fail(e)
    versions = versions if isinstance(versions, list) else []
    if output_format != "table":
        output.render(versions, output_format)
        return
    rows = [{"version": v.get("version"), "when": str(v.get("created_at") or "-")[:16].replace("T", " "),
             "change": v.get("change_type"),
             "by": v.get("changed_by_name") or "-", "status": v.get("status"),
             "fields": ", ".join(v.get("changed_fields") or []) or "-", "summary": v.get("change_summary") or "-"}
            for v in versions if isinstance(v, dict)]
    output.print_table(rows, ["version", "when", "change", "by", "status", "fields", "summary"],
                       title=f"Case history ({len(rows)})")


@cases_app.command("export")
def export_cases(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name (required)."),
    out: Path = typer.Option(..., "--output", "-o", help="Where to write the .xlsx workbook."),
    statuses: list[str] = typer.Option(None, "--status", help="Repeatable lifecycle state (default: every status, deprecated and archived included)."),
    test_types: list[str] = typer.Option(None, "--type", help="Repeatable test type."),
    priorities: list[str] = typer.Option(None, "--priority", help="Repeatable: critical, high, medium, low (or P0-P3)."),
    search: str = typer.Option(None, "--search", help="As on `cases list`."),
    owner: str = typer.Option(None, "--owner", help="'me' or a user id."),
    automation: str = typer.Option(None, "--automation", help="automated|not_automated"),
    requirement: str = typer.Option(None, "--requirement", help="Only cases linked to this requirement key."),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The project's cases as an Excel workbook (the page's Export). No --suite: the export reads none."""
    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        params = export_params(
            project_id=pid, statuses=statuses, test_types=test_types, priorities=priorities,
            search=search, owner=owner, automation=automation, requirement=requirement,
        )
        return await client.download("/api/v1/test-management/cases/export/excel", params=params, profile_name=profile_name)

    try:
        data = asyncio.run(go())
    except Exception as e:
        _fail(e)
    out.write_bytes(data)
    if not data:
        output.print_warning("The server answered an empty file: the project is not in your scope.")
    output.print_success(f"Saved {len(data)} bytes of test cases to {out}.")
