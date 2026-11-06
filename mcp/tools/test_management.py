"""Tools: list_managed_test_cases, get_managed_test_case — authored test cases, read-only (W34 STRAT-11).

They read the routes the Test Management page reads (``GET /api/v1/test-management/cases``,
``…/cases/{case_id}`` and, on request, ``…/cases/{case_id}/history``), so an agent asked
"which checkout cases are still drafts?" answers with the same cases in the same order
(newest first). The names are not ``list_test_cases`` / ``get_test_case``: those
(``tools/runs.py``) read one RUN's executed results.

No export tool: the Excel workbook is bytes and does not fit a tool answer (the
compliance-pack precedent); ``testlookup cases export`` writes it.
"""
from __future__ import annotations

from typing import Any, Optional

import client as api  # type: ignore[import]
import release_resolver  # type: ignore[import]

#: The list route's largest page (``size`` 1-200).
MAX_SIZE = 200
#: The fields a listed case keeps (the full case is ``get_managed_test_case``).
LIST_FIELDS = (
    "id", "title", "status", "priority", "test_type", "severity", "suite_name", "owner",
    "is_automated", "automation_status", "tags", "version", "updated_at", "last_execution_status",
)


def short_id(case_id: Any) -> str:
    """``TC-1A2B3C``, as the Test Management page prints a case."""
    return f"TC-{str(case_id or '')[:6].upper()}"


def _cell(value: Any) -> str:
    """A markdown table cell: a pipe or a newline in a title would break the row."""
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def list_markdown(body: dict, page: int) -> str:
    items = [c for c in body.get("items") or [] if isinstance(c, dict)]
    total, pages = body.get("total", 0), body.get("pages", 0)
    if not items:
        return ("No authored test cases match. Deprecated and archived cases are left out unless "
                "status names them or include_archived is true.")
    lines = [
        f"## Test cases — page {page}/{pages} ({total} total)\n",
        "| Case | Title | Status | Priority | Type | Suite | Owner | Automated | ID |",
        "|------|-------|--------|----------|------|-------|-------|-----------|----|",
    ]
    for c in items:
        lines.append(
            f"| {short_id(c.get('id'))} | {_cell(c.get('title'))} | {_cell(c.get('status'))} "
            f"| {_cell(c.get('priority'))} | {_cell(c.get('test_type'))} | {_cell(c.get('suite_name'))} "
            f"| {_cell(c.get('owner'))} | {'yes' if c.get('is_automated') else 'no'} | `{c.get('id')}` |"
        )
    if page < (pages or 0):
        lines.append(f"\n{len(items)} shown of {total}; more — call again with page={page + 1}.")
    lines.append("\n*Use `get_managed_test_case` with an ID for the steps (and include_history=true for the versions).*")
    return "\n".join(lines)


def case_markdown(case: dict, history: Optional[list] = None) -> str:
    tags = ", ".join(str(t) for t in case.get("tags") or []) or "-"
    lines = [
        f"## {short_id(case.get('id'))} — {_cell(case.get('title'))}",
        f"- **Status:** {_cell(case.get('status'))} (version {_cell(case.get('version'))}) · "
        f"**Priority:** {_cell(case.get('priority'))} · **Type:** {_cell(case.get('test_type'))} · "
        f"**Severity:** {_cell(case.get('severity'))}",
        f"- **Suite:** {_cell(case.get('suite_name'))} · **Owner:** {_cell(case.get('owner'))} · "
        f"**Automated:** {'yes' if case.get('is_automated') else 'no'} ({_cell(case.get('automation_status'))})",
        f"- **Tags:** {_cell(tags)}",
        f"- **ID:** `{case.get('id')}`",
    ]
    for label, key in (("Objective", "objective"), ("Preconditions", "preconditions")):
        if case.get(key):
            lines.append(f"\n**{label}:** {case.get(key)}")
    steps = [s for s in case.get("steps") or [] if isinstance(s, dict)]
    if steps:
        lines += ["\n### Steps", "| # | Action | Expected |", "|---|--------|----------|"]
        lines += [f"| {_cell(s.get('step_number'))} | {_cell(s.get('action'))} | {_cell(s.get('expected_result'))} |"
                  for s in steps]
    else:
        lines.append("\n### Steps\n(none recorded)")
    if case.get("expected_result"):
        lines.append(f"\n**Expected result:** {case.get('expected_result')}")
    if history is not None:
        versions = [v for v in history if isinstance(v, dict)]
        lines += [f"\n### History ({len(versions)} saved, newest first)",
                  "| Version | When | Change | By | Status | Fields |", "|---|---|---|---|---|---|"]
        lines += [
            f"| {_cell(v.get('version'))} | {_cell(str(v.get('created_at') or '')[:16])} | {_cell(v.get('change_type'))} "
            f"| {_cell(v.get('changed_by_name'))} | {_cell(v.get('status'))} | {_cell(', '.join(v.get('changed_fields') or []))} |"
            for v in versions
        ]
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_managed_test_cases(
        project_id: str,
        status: Optional[list[str]] = None,
        test_type: Optional[list[str]] = None,
        priority: Optional[list[str]] = None,
        suite_name: Optional[str] = None,
        search: Optional[str] = None,
        owner: Optional[str] = None,
        automation: Optional[str] = None,
        requirement: Optional[str] = None,
        include_archived: bool = False,
        page: int = 1,
        size: int = 25,
    ) -> dict:
        """
        A project's AUTHORED test cases (the Test Management library), newest
        first: the cases people wrote, with their lifecycle status, priority,
        suite and owner. Read-only. Not a run's executed results: for those use
        list_test_cases with a run id.

        Args:
            project_id: Project id or exact project name (required: a non-admin
                who names no project sees nothing).
            status: Lifecycle states to keep (OR): draft, review_requested,
                under_review, approved, active, rejected, needs_update,
                deprecated, archived. Without it deprecated and archived cases
                are left out.
            test_type: Test types to keep (OR), e.g. ["functional"].
            priority: Priorities to keep (OR): critical, high, medium, low
                (P0-P3 also accepted).
            suite_name: One suite, matched exactly (case-insensitive).
            search: Text in the title, description or tags, a TC- id prefix,
                or a requirement key.
            owner: "me" or a user id (the assignee, else the author).
            automation: "automated" or "not_automated".
            requirement: Only cases linked to this requirement key.
            include_archived: Also list archived cases (true/false).
            page: Page number (from 1, default 1).
            size: Cases per page (1-200, default 25).

        Returns {ok, project_id, summary, items, total, page, pages, more}:
        `summary` is a markdown table, `items` the cases' main fields. When
        `more` is true call again with page=page+1. Errors come back as
        {ok: false, status_code, detail} (a 422 names an unknown filter value).
        """
        try:
            pid = await release_resolver.resolve_project(project_id)
        except release_resolver.UnresolvedName as exc:
            return release_resolver.unresolved_payload(exc)
        except Exception as exc:
            return api.error_payload(exc)
        page = max(1, int(page))
        params = {
            "project_id": pid,
            "status": status,
            "test_type": test_type,
            "priority": priority,
            "suite_name": suite_name,
            "search": search,
            "owner": owner,
            "automation": automation,
            "requirement": requirement,
            "include_archived": include_archived,
            "page": page,
            "size": max(1, min(int(size), MAX_SIZE)),
        }
        try:
            body = await api.get("/api/v1/test-management/cases", params=params)
        except Exception as exc:
            return api.error_payload(exc)
        body = body if isinstance(body, dict) else {}
        items = [{k: c.get(k) for k in LIST_FIELDS} | {"short_id": short_id(c.get("id"))}
                 for c in body.get("items") or [] if isinstance(c, dict)]
        pages = body.get("pages", 0) or 0
        return {"ok": True, "project_id": pid, "summary": list_markdown(body, page), "items": items,
                "total": body.get("total", 0), "page": page, "pages": pages, "more": page < pages}

    @mcp.tool()
    async def get_managed_test_case(case_id: str, include_history: bool = False) -> dict:
        """
        One AUTHORED test case (Test Management) with its steps, preconditions
        and expected result; with include_history=true also its saved versions,
        newest first (at most 100: who changed what, and the status). Read-only.

        Args:
            case_id: The case's full id (the `id` from list_managed_test_cases;
                for a TC- short id, call list_managed_test_cases with
                search="TC-…" first).
            include_history: Also read the case's versions (default false).

        Returns {ok, summary, case} (+ `history` when asked): `summary` is
        markdown, `case` the REST body. Errors come back as {ok: false,
        status_code, detail}: 403 another project's case, 404 no such case.
        """
        if not release_resolver.is_uuid(case_id):
            return {"ok": False, "status_code": 400, "code": "not_a_case_id",
                    "detail": f"'{case_id}' is not a case id: pass the full id from list_managed_test_cases "
                              "(a TC- short id: list_managed_test_cases with search set to it)."}
        try:
            case = await api.get(f"/api/v1/test-management/cases/{case_id}")
            history = await api.get(f"/api/v1/test-management/cases/{case_id}/history") if include_history else None
        except Exception as exc:
            return api.error_payload(exc)
        case = case if isinstance(case, dict) else {}
        versions = (history if isinstance(history, list) else []) if include_history else None
        out: dict[str, Any] = {"ok": True, "summary": case_markdown(case, versions), "case": case}
        if versions is not None:
            out["history"] = versions
        return out
