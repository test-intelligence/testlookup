"""``testlookup release gate|list`` — a release's GO / NO_GO as a CI exit code (W18 DEV-01).

``release gate <release>`` reads the release's standing verdict
(``GET /api/v1/releases/{id}/gate``, the stored snapshot the release page
shows), or records a fresh one with ``--evaluate``
(``POST /api/v1/releases/{id}/gate/evaluate``; QA lead or a project API key).
``<release>`` is an id or a name / version (DEV-05 ``release_resolver``).

Exit-code contract (stable, for CI; frozen in ``tests/test_release_commands.py``):

* ``0`` — GO, or CONDITIONAL_GO (unless ``--fail-on CONDITIONAL_GO``).
* ``1`` — a failing verdict: NO_GO (or CONDITIONAL_GO with ``--fail-on CONDITIONAL_GO``).
* ``3`` — no verdict to act on: NOT_EVALUATED (evaluated, not enough evidence)
  or never evaluated (the 404 "has not been evaluated yet").
* ``2`` — infrastructure error (network, 5xx, auth, permission, an unknown or
  ambiguous release name). **Fails closed**; ``--fail-open`` turns it into 0
  with a loud warning, as ``ci-verdict`` does.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import typer

from testlookup_cli import client, output, release_resolver
from testlookup_cli.errors import EXIT_VALIDATION, CLIError, exit_code_for

release_app = typer.Typer(name="release", help="Releases: the gate verdict as a CI exit code, and the list.")

EXIT_PASS = 0
EXIT_FAILING_VERDICT = 1
EXIT_INFRA = 2
EXIT_NO_VERDICT = 3

PASSING = {"GO", "CONDITIONAL_GO"}
FAIL_ON = ("NO_GO", "CONDITIONAL_GO")


def exit_code_for_verdict(verdict: Optional[str], fail_on: str = "NO_GO") -> int:
    v = str(verdict or "").upper()
    if v in ("", "NOT_EVALUATED", "INSUFFICIENT_EVIDENCE"):
        return EXIT_NO_VERDICT
    if v == "NO_GO":
        return EXIT_FAILING_VERDICT
    if v == "CONDITIONAL_GO":
        return EXIT_FAILING_VERDICT if fail_on == "CONDITIONAL_GO" else EXIT_PASS
    if v == "GO":
        return EXIT_PASS
    return EXIT_NO_VERDICT  # an unknown word is not a pass


async def _gate(release: str, project: Optional[str], evaluate: bool, profile_name: Optional[str]) -> tuple[str, Optional[dict]]:
    pid = await release_resolver.resolve_project(project, profile_name=profile_name)
    rid = await release_resolver.resolve_release(release, project_id=pid, profile_name=profile_name)
    if evaluate:
        return rid, await client.request("POST", f"/api/v1/releases/{rid}/gate/evaluate", profile_name=profile_name, timeout=120.0)
    try:
        return rid, await client.request("GET", f"/api/v1/releases/{rid}/gate", profile_name=profile_name)
    except CLIError as e:
        if e.status_code == 404 and "not been evaluated" in str(e):
            return rid, None
        raise


def _render_gate(release: str, rid: str, gate: Optional[dict]) -> None:
    if gate is None:
        output.print_warning(f"Release {release} ({rid}) has not been evaluated yet. Run with --evaluate (QA lead).")
        return
    verdict = gate.get("verdict") or "NOT_EVALUATED"
    card = gate.get("scorecard") or {}
    reasons = gate.get("blocking_reasons_display") or gate.get("blocking_reasons") or []
    lines = [f"Release gate: {verdict}", f"  release   {release} ({rid})"]
    if gate.get("decided_at"):
        lines.append(f"  decided   {gate['decided_at']}")
    if card.get("pass_rate") is not None:
        lines.append(f"  pass rate {card['pass_rate']}%  (evidence {card.get('evidence_count')}, runs {card.get('run_count')})")
    elif card.get("insufficient_reason"):
        lines.append(f"  evidence  {card['insufficient_reason']}")
    if gate.get("recorded") is not None:
        lines.append(f"  recorded  {'yes' if gate.get('recorded') else 'no (preview)'}")
    total = gate.get("blocking_total", len(reasons))
    lines.append(f"  blockers  {total}")
    lines += [f"    - {r}" for r in reasons[:20]]
    if total > 20:
        lines.append(f"    … {total - 20} more on the release page")
    typer.echo("\n".join(lines))


@release_app.command("gate")
def gate(
    release: str = typer.Argument(..., help="Release id, name or version."),
    project: str = typer.Option(None, "--project", "-p", help="Project id or name (narrows a release name)."),
    evaluate: bool = typer.Option(False, "--evaluate", help="Record a fresh verdict first (QA lead or a project API key)."),
    fail_on: str = typer.Option("NO_GO", "--fail-on", help="NO_GO (default) or CONDITIONAL_GO: the least verdict that fails the step."),
    fail_open: bool = typer.Option(False, "--fail-open", help="Exit 0 on an infrastructure error instead of 2 (with a warning)."),
    as_json: bool = typer.Option(False, "--json", help="Print the verdict body as JSON."),
    profile_name: str = typer.Option(None, "--profile"),
):
    """The release's GO / CONDITIONAL_GO / NO_GO, as an exit code (0 / 0 or 1 / 1; 3 no verdict; 2 infra)."""
    fail_on = fail_on.upper()
    if fail_on not in FAIL_ON:
        output.print_error(f"--fail-on takes one of: {', '.join(FAIL_ON)}.")
        raise typer.Exit(EXIT_VALIDATION)
    try:
        rid, body = asyncio.run(_gate(release, project, evaluate, profile_name))
    except Exception as e:
        output.print_error(str(e))
        if fail_open:
            output.print_warning("--fail-open: treating the infrastructure error as a pass.")
            raise typer.Exit(EXIT_PASS)
        raise typer.Exit(EXIT_INFRA)
    if as_json:
        output.print_json({"release_id": rid, "gate": body})
    else:
        _render_gate(release, rid, body)
    raise typer.Exit(exit_code_for_verdict(body.get("verdict") if body else None, fail_on))


@release_app.command("list")
def list_releases(
    project: str = typer.Option(None, "--project", "-p", help="Project id or name. Omit for every project you can read."),
    status: str = typer.Option(None, "--status", help="Only releases with this status."),
    output_format: str = typer.Option("table", "--format", "-o", help="table|json|yaml"),
    profile_name: str = typer.Option(None, "--profile"),
):
    """Releases with their id, version, status and standing gate verdict."""
    async def _go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        params = {k: v for k, v in {"project_id": pid, "status": status}.items() if v}
        body = await client.request("GET", "/api/v1/releases", params=params or None, profile_name=profile_name)
        # A one-project list carries no project_name (only the all-projects view adds it).
        names = {}
        if pid:
            names = {str(p.get("id")): p.get("name") for p in release_resolver._items(
                await client.request("GET", "/api/v1/projects", profile_name=profile_name))}
        return body, names

    try:
        body, names = asyncio.run(_go())
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
    items = body.get("items", []) if isinstance(body, dict) else body or []
    if output_format != "table":
        output.render(items, output_format)
        return
    rows = [{
        "name": r.get("name"), "version": r.get("version") or "-", "status": r.get("status") or "-",
        "gate": (r.get("gate") or {}).get("verdict") or "never evaluated",
        "project": r.get("project_name") or names.get(str(r.get("project_id"))) or r.get("project_id"), "id": r.get("id"),
    } for r in items]
    output.print_table(rows, ["name", "version", "status", "gate", "project", "id"], title=f"Releases ({len(rows)})")
