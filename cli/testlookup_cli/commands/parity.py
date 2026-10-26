"""CLI parity with the MCP tools (W18 DEV-01): quarantine, Jira defects, investigations.

Each command calls the same endpoint as its MCP tool (``mcp/tools/quarantine.py``,
``defects.py``, ``investigations.py``) and nothing else: the server enforces the
roles and the quarantine workflow flag, the CLI maps its refusals to the stable
exit codes (403 -> 4, 404 -> 5, 422 -> 2). Writes that change what a CI run sees
(approve, release) take ``--reason``, recorded on the audit trail.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import typer

from testlookup_cli import client, output, release_resolver
from testlookup_cli.errors import EXIT_VALIDATION, exit_code_for

quarantine_app = typer.Typer(name="quarantine", help="Flaky-test quarantine: list, approve, reject, release.")
defects_app = typer.Typer(name="defects", help="Defects: preview or file a Jira issue for a failure signature.")
jira_app = typer.Typer(name="jira", help="File a failure signature as a Jira issue (or preview it).")
defects_app.add_typer(jira_app, name="jira")
investigations_app = typer.Typer(name="investigations", help="Deep investigations of a run: start, list, show.")


def _call(coro_fn, profile_name: Optional[str]) -> Any:
    try:
        return asyncio.run(coro_fn())
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


async def _pid(project: Optional[str], profile_name: Optional[str]) -> Optional[str]:
    return await release_resolver.resolve_project(project, profile_name=profile_name)


# ── quarantine ──────────────────────────────────────────────────────────────


@quarantine_app.command("list")
def quarantine_list(
    project: str = typer.Option(None, "--project", "-p", help="Project id or name."),
    status: str = typer.Option(None, "--status", help="PROPOSED, QUARANTINED, RELEASED, …"),
    live_only: bool = typer.Option(False, "--live-only", help="Hide RELEASED / REJECTED / EXPIRED."),
    limit: int = typer.Option(50, "--limit", help="Rows (cap 500)."),
    output_format: str = typer.Option("table", "--format", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Quarantine requests — the Flaky › Quarantine tab."""
    async def go():
        params: dict = {"limit": limit, "live_only": live_only}
        pid = await _pid(project, profile_name)
        if pid:
            params["project_id"] = pid
        if status:
            params["status_filter"] = status
        return await client.request("GET", "/api/v1/quarantine", params=params, profile_name=profile_name)

    rows = _call(go, profile_name) or []
    if output_format != "table":
        output.render(rows, output_format)
        return
    table = [{
        "test": r.get("test_name") or r.get("test_fingerprint"), "status": r.get("status"),
        "flip_rate": r.get("flip_rate"), "expires": r.get("expires_at") or "-", "id": r.get("id"),
    } for r in rows]
    output.print_table(table, ["test", "status", "flip_rate", "expires", "id"], title=f"Quarantine requests ({len(table)})")


def _quarantine_write(request_id: str, action: str, notes: Optional[str], profile_name: Optional[str], extra: Optional[dict] = None) -> None:
    body: dict = dict(extra or {})
    if notes is not None:
        body["notes"] = notes
    row = _call(lambda: client.request("POST", f"/api/v1/quarantine/{request_id}/{action}", json_body=body, profile_name=profile_name), profile_name)
    output.print_success(f"{action.title()}d quarantine request {request_id}: now {row.get('status') if isinstance(row, dict) else row}.")


@quarantine_app.command("approve")
def quarantine_approve(
    request_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Why (recorded on the audit trail)."),
    days: int = typer.Option(None, "--days", help="Quarantine length in days, 1-90 (server default when omitted)."),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Quarantine the test (QA lead)."""
    _quarantine_write(request_id, "approve", reason, profile_name, {"quarantine_duration_days": days} if days else None)


@quarantine_app.command("reject")
def quarantine_reject(
    request_id: str = typer.Argument(...),
    reason: str = typer.Option(None, "--reason", help="Why (recorded on the audit trail)."),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Keep the test in rotation (QA lead)."""
    _quarantine_write(request_id, "reject", reason, profile_name)


@quarantine_app.command("release")
def quarantine_release(
    request_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Why the quarantine ends early (recorded)."),
    profile_name: str = typer.Option(None, "--profile"),
):
    """End an active quarantine early (QA lead)."""
    _quarantine_write(request_id, "release", reason, profile_name)


# ── defects jira ────────────────────────────────────────────────────────────


def _signature(fingerprint: Optional[str], cluster_id: Optional[str]) -> dict:
    if bool(fingerprint) == bool(cluster_id):
        output.print_error("Pass exactly one of --fingerprint or --cluster.")
        raise typer.Exit(EXIT_VALIDATION)
    return {"fingerprint": fingerprint} if fingerprint else {"cluster_id": cluster_id}


@jira_app.command("preview")
def jira_preview(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    fingerprint: str = typer.Option(None, "--fingerprint", help="Test fingerprint of the failure."),
    cluster_id: str = typer.Option(None, "--cluster", help="Failure-cluster id."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """What would be filed (summary, description, duplicate check) — nothing is created."""
    sig = _signature(fingerprint, cluster_id)

    async def go():
        pid = await _pid(project, profile_name)
        return await client.request("GET", f"/api/v1/projects/{pid}/defects/jira/preview", params=sig, profile_name=profile_name)

    body = _call(go, profile_name)
    if as_json or not isinstance(body, dict):
        output.print_json(body)
        return
    output.render({k: body.get(k) for k in ("summary", "duplicate_of", "dedup", "issue_type", "jira_project_key") if k in body}, "table")


@jira_app.command("create")
def jira_create(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    fingerprint: str = typer.Option(None, "--fingerprint"),
    cluster_id: str = typer.Option(None, "--cluster"),
    target: str = typer.Option("jira", "--target", help="jira (file an issue) or webhook (emit defect.create_requested)."),
    issue_type: str = typer.Option(None, "--issue-type", help="Jira issue type (server default Bug)."),
    jira_project_key: str = typer.Option(None, "--jira-project", help="Jira project key (else the configured default)."),
    assignee: str = typer.Option(None, "--assignee"),
    comment: str = typer.Option(None, "--comment", help="An extra comment on the issue."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """File the failure signature (a recurring one adds to the existing issue)."""
    sig = _signature(fingerprint, cluster_id)
    body = {**sig, "target": target, "issue_type": issue_type, "jira_project_key": jira_project_key,
            "assignee": assignee, "extra_comment": comment}

    async def go():
        pid = await _pid(project, profile_name)
        return await client.request("POST", f"/api/v1/projects/{pid}/defects/jira",
                                    json_body={k: v for k, v in body.items() if v is not None}, profile_name=profile_name)

    result = _call(go, profile_name)
    if as_json or not isinstance(result, dict):
        output.print_json(result)
        return
    output.print_success(f"Defect {result.get('status') or 'requested'}: {result.get('external_key') or result.get('issue_key') or result.get('id') or ''}".strip())


# ── investigations ──────────────────────────────────────────────────────────


@investigations_app.command("start")
def investigations_start(
    run_id: str = typer.Argument(..., help="Run id."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Start a deep investigation of a run (it runs in the background)."""
    body = _call(lambda: client.request("POST", f"/api/v1/runs/{run_id}/investigations", profile_name=profile_name), profile_name)
    if as_json:
        output.print_json(body)
        return
    output.print_success(f"Investigation {body.get('id') if isinstance(body, dict) else ''} started ({body.get('status') if isinstance(body, dict) else 'queued'}). Follow it with `testlookup investigations show <id>`.")


@investigations_app.command("list")
def investigations_list(
    project: str = typer.Option(..., "--project", "-p", help="Project id or name."),
    limit: int = typer.Option(20, "--limit", help="1-100."),
    output_format: str = typer.Option("table", "--format", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """A project's investigations, newest first."""
    async def go():
        pid = await _pid(project, profile_name)
        return await client.request("GET", f"/api/v1/projects/{pid}/investigations",
                                    params={"limit": max(1, min(limit, 100)), "offset": 0}, profile_name=profile_name)

    body = _call(go, profile_name) or {}
    items = body.get("items", []) if isinstance(body, dict) else body
    if output_format != "table":
        output.render(items, output_format)
        return
    rows = [{"id": i.get("id"), "run": i.get("run_id"), "status": i.get("status"),
             "cause": (i.get("primary_cause") or "-") if isinstance(i.get("primary_cause"), str) else (i.get("primary_cause") or {}).get("summary", "-"),
             "started": i.get("started_at") or i.get("created_at")} for i in items]
    output.print_table(rows, ["id", "run", "status", "cause", "started"], title=f"Investigations ({len(rows)})")


@investigations_app.command("show")
def investigations_show(
    investigation_id: str = typer.Argument(...),
    output_format: str = typer.Option("table", "--format", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """One investigation: status, primary cause, confidence, evidence."""
    body = _call(lambda: client.request("GET", f"/api/v1/investigations/{investigation_id}", profile_name=profile_name), profile_name)
    if output_format != "table" or not isinstance(body, dict):
        output.render(body, output_format if output_format != "table" else "json")
        return
    output.render({k: body.get(k) for k in ("id", "run_id", "status", "primary_cause", "confidence", "started_at", "completed_at") if k in body}, "table")
