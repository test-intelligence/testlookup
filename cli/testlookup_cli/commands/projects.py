"""Project commands — list, get, export, import."""
import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.errors import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_TIMEOUT, CLIError, exit_code_for

projects_app = typer.Typer(name="projects", help="Manage projects")

# ── W24 ADM-05: export settings + catalog / import as a NEW project ─────────
# The server's sentences, restated (the CLI installs without the backend):
# backend/app/services/project_export_vocab.py ERROR_SENTENCES / SKIP_REASONS.
IMPORT_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
UPLOAD_TOO_LARGE = "The file is larger than 50 MB."
UPLOAD_EMPTY = "Choose a .zip file exported from TestLookup."
NOT_A_ZIP = "The file is not a valid ZIP archive."
SKIP_REASONS = {
    "user_not_found": "the person does not exist here",
    "assignment": "the new project has no other members yet",
    "row_unresolved": "it pointed at a row that is not in the export",
}
INERT_NOTE = (
    "Webhooks, team channels, Git integrations and notification rules arrive switched off; "
    "run cadences arrive paused for 90 days."
)
# What happened to an entity in the report's ``switched_off`` map — the page's words (projectDataModel.ts): a cadence
# is paused and a reset review returns to draft, neither is "switched off".
SWITCHED_OFF_SUFFIX = {
    "webhook_subscriptions": "switched off — secret required",
    "github_integrations": "switched off — secret required",
    "gitlab_integrations": "switched off — secret required",
    "team_notification_channels": "switched off",
    "notification_transition_policies": "switched off",
    "quarantine_lifecycle_policies": "automatic defects switched off",
    "run_cadences": "paused for 90 days",
    "releases.deactivated": "set inactive — one active release is kept",
    "managed_test_cases.review_reset": "returned to draft — review requests are not exported",
}
SWITCHED_OFF_DEFAULT = "switched off"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_TERMINAL = ("succeeded", "failed", "expired")
_POLL_SECONDS = 2.0
_sleep = asyncio.sleep          # patched by the tests


def _now() -> float:
    return time.monotonic()


def import_client_token(file: Path, name: Optional[str], *, new_copy: bool = False) -> uuid.UUID:
    """The idempotency token: the same file + name always give the same token, so
    re-running ``projects import`` creates ONE project; ``--new-copy`` is a fresh uuid4."""
    if new_copy:
        return uuid.uuid4()
    digest = hashlib.sha256()
    with open(file, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return uuid.uuid5(uuid.NAMESPACE_URL, "testlookup-import:" + digest.hexdigest() + ":" + (name or ""))


def check_import_file(file: Path) -> None:
    """Refuse locally, before any request: not a file, empty, over 50 MiB, not ZIP magic."""
    if not file.is_file():
        raise CLIError(f"Not a file: {file}", EXIT_ERROR)
    size = file.stat().st_size
    if size == 0:
        raise CLIError(UPLOAD_EMPTY, EXIT_ERROR)
    if size > IMPORT_UPLOAD_MAX_BYTES:
        raise CLIError(UPLOAD_TOO_LARGE, EXIT_ERROR)
    with open(file, "rb") as fh:
        if fh.read(4) not in _ZIP_MAGIC:
            raise CLIError(NOT_A_ZIP, EXIT_ERROR)


def human_size(size: Optional[int]) -> str:
    if not size:
        return "0 B"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many.format(count=count)


def report_lines(report: dict[str, Any]) -> list[str]:
    """The import report in the page's sentences (singular for a count of 1)."""
    lines: list[str] = []
    for skip in report.get("skipped") or []:
        reason = SKIP_REASONS.get(str(skip.get("reason")), str(skip.get("reason")))
        count = int(skip.get("count") or 0)
        where = skip["entity"] + (f".{skip['column']}" if skip.get("column") else "")
        lines.append(f"{where}: " + _plural(count, f"1 row was skipped: {reason}", "{count} rows were skipped: " + reason))
    for item in report.get("cleared") or []:
        count = int(item.get("count") or 0)
        where = item["entity"] + (f".{item['column']}" if item.get("column") else "")
        if item.get("reason") == "assignment":
            lines.append(f"{where}: " + _plural(count, "1 assignment was cleared", "{count} assignments were cleared"))
        else:
            reason = SKIP_REASONS.get(str(item.get("reason")), str(item.get("reason")))
            lines.append(f"{where}: " + _plural(count, f"1 value was cleared: {reason}", "{count} values were cleared: " + reason))
    for entity, count in sorted((report.get("switched_off") or {}).items()):
        what = SWITCHED_OFF_SUFFIX.get(entity, SWITCHED_OFF_DEFAULT)
        lines.append(f"{entity}: " + _plural(int(count), f"1 row {what}", "{count} rows " + what))
    return lines


@projects_app.command("list")
def list_projects(
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List all accessible projects."""
    try:
        data = asyncio.run(client.request("GET", "/api/v1/projects", profile_name=profile_name))
        items = data if isinstance(data, list) else data.get("items", data)
        output.render(items, output_format, columns=["id", "name", "description", "created_at"], title="Projects")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@projects_app.command("get")
def get_project(
    project_id: str = typer.Argument(..., help="Project ID"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Get project details."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/projects/{project_id}", profile_name=profile_name))
        output.render(data, output_format)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


async def _resolve_project(project: str, profile_name: Optional[str]) -> dict[str, Any]:
    """A project by UUID, else by name or slug (case-insensitive) among the accessible ones."""
    try:
        project_id = str(uuid.UUID(project.strip()))
    except ValueError:
        project_id = ""
    if project_id:
        found: dict[str, Any] = await client.request("GET", f"/api/v1/projects/{project_id}", profile_name=profile_name)
        return found
    data = await client.request("GET", "/api/v1/projects", profile_name=profile_name)
    items = data if isinstance(data, list) else (data or {}).get("items", [])
    wanted = project.strip().lower()
    matches = [p for p in items if wanted in (str(p.get("name", "")).lower(), str(p.get("slug", "")).lower())]
    if not matches:
        raise CLIError(f"No accessible project is called {project}.", EXIT_NOT_FOUND)
    if len(matches) > 1:
        raise CLIError(f"More than one project is called {project}; pass its id.", EXIT_ERROR)
    return matches[0]


async def _wait_for_job(job: dict[str, Any], *, profile_name: Optional[str], timeout: float) -> dict[str, Any]:
    """Poll ``GET /api/v1/project-data-jobs/{id}`` every 2 s until the job is terminal."""
    deadline = _now() + timeout
    while job.get("status") not in _TERMINAL:
        if _now() >= deadline:
            raise CLIError(
                f"Job {job.get('id')} is still {job.get('status')} after {int(timeout)} s; check it again later.",
                EXIT_TIMEOUT,
            )
        await _sleep(_POLL_SECONDS)
        job_id = job["id"]
        job = await client.request("GET", f"/api/v1/project-data-jobs/{job_id}", profile_name=profile_name)
    return job


def _job_error(job: dict[str, Any]) -> str:
    error = job.get("error") or {}
    return str(error.get("message") or f"The job ended {job.get('status')}.")


def _stamp(job: dict[str, Any]) -> str:
    try:
        finished = datetime.fromisoformat(str(job.get("finished_at")).replace("Z", "+00:00"))
    except ValueError:
        finished = datetime.now(timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return finished.astimezone(timezone.utc).strftime("%Y%m%d-%H%M")


async def _export(
    project: str, out: Optional[Path], *, force: bool, wait: bool, timeout: float, profile_name: Optional[str],
) -> tuple[dict[str, Any], dict[str, Any], Optional[Path], int]:
    found = await _resolve_project(project, profile_name)
    project_id = found["id"]
    if out is not None and out.exists() and not force:
        raise CLIError(f"{out} already exists; pass --force to overwrite it.", EXIT_ERROR)
    try:
        job = await client.request("POST", f"/api/v1/projects/{project_id}/export", profile_name=profile_name)
    except client.APIError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code != 409 or detail.get("code") != "export_running" or not detail.get("job_id"):
            raise
        output.print_warning(f"{detail.get('message', 'An export is already running.')} Following it.")
        job_id = detail["job_id"]
        job = await client.request("GET", f"/api/v1/project-data-jobs/{job_id}", profile_name=profile_name)
    if not wait:
        return found, job, None, 0
    job = await _wait_for_job(job, profile_name=profile_name, timeout=timeout)
    if job.get("status") != "succeeded":
        raise CLIError(_job_error(job), EXIT_ERROR)
    dest = out or Path(f"{found.get('slug') or project_id}-settings-catalog-{_stamp(job)}.zip")
    if dest.exists() and not force:
        raise CLIError(f"{dest} already exists; pass --force to overwrite it.", EXIT_ERROR)
    job_id = job["id"]
    written = await client.download_to_file(
        f"/api/v1/project-data-jobs/{job_id}/download", dest, profile_name=profile_name,
    )
    return found, job, dest, written


@projects_app.command("export")
def export_project(
    project: str = typer.Argument(..., help="Project name, slug or id"),
    out: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Where to write the ZIP (default ./<slug>-settings-catalog-<stamp>.zip)",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the export and download it"),
    timeout: float = typer.Option(600.0, "--timeout", help="Seconds to wait for the export"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
):
    """Export a project's settings and test catalog as a signed ZIP (QA lead or admin).

    Run history, logs and attachments are not included; secrets never are.

    Examples:
        testlookup projects export "Payment Service"
        testlookup projects export <project-id> -o payments.zip --force
    """
    try:
        found, job, dest, written = asyncio.run(
            _export(project, out, force=force, wait=wait, timeout=timeout, profile_name=profile_name)
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
    if dest is None:
        output.print_success(f"Started the export of {found.get('name')}: job {job.get('id')} ({job.get('status')}).")
        return
    # W25a (W24 F-8): ``counts`` carries every exported table, zeros included; only the tables that hold rows are
    # counted (as the import line does and the Export card says). Singular for 1.
    filled = [int(v) for v in (job.get("counts") or {}).values() if int(v) > 0]
    rows, tables = sum(filled), len(filled)
    rows_text = _plural(rows, "1 row", "{count} rows")
    tables_text = _plural(tables, "1 table", "{count} tables")
    output.print_success(
        f"Exported {found.get('name')}: {rows_text} in {tables_text} ({human_size(job.get('bytes') or written)})."
    )
    output.print_success(f"Saved {dest}.")


async def _import(
    file: Path, name: Optional[str], *, new_copy: bool, wait: bool, timeout: float, profile_name: Optional[str],
) -> tuple[dict[str, Any], bool]:
    """``(job, already_imported)``. Every local refusal happens before any request."""
    check_import_file(file)
    token = import_client_token(file, name, new_copy=new_copy)
    fields = {"client_token": str(token)}
    if name:
        fields["name"] = name
    job: dict[str, Any] = await client.upload_file("/api/v1/projects/import", file, fields, profile_name=profile_name)
    if job.get("status") == "succeeded":
        return job, True          # the server answered 200 with the token's finished job
    if not wait:
        return job, False
    job = await _wait_for_job(job, profile_name=profile_name, timeout=timeout)
    if job.get("status") != "succeeded":
        raise CLIError(_job_error(job), EXIT_ERROR)
    return job, False


@projects_app.command("import")
def import_project(
    file: Path = typer.Argument(..., help="A ZIP written by `testlookup projects export` or the Project data page"),
    name: Optional[str] = typer.Option(None, "--name", help="The new project's name (default: '<source> (imported)')"),
    new_copy: bool = typer.Option(False, "--new-copy", help="Import a second copy of a file already imported"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the import to finish"),
    timeout: float = typer.Option(600.0, "--timeout", help="Seconds to wait for the import"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
):
    """Import an export as a NEW project (admin). Nothing is merged into an existing project.

    Re-running the same command with the same file and name creates ONE project;
    --new-copy makes a deliberate second one.

    Examples:
        testlookup projects import payment-service-settings-catalog-20261030-1200.zip
        testlookup projects import payments.zip --name "Payments sandbox"
    """
    clean = name.strip() if name and name.strip() else None
    try:
        job, already = asyncio.run(
            _import(file, clean, new_copy=new_copy, wait=wait, timeout=timeout, profile_name=profile_name)
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
    report = job.get("import_report") or {}
    if already:
        output.print_success(f"Already imported as {report.get('project_name')} ({report.get('project_id')}).")
        return
    if job.get("status") != "succeeded":
        output.print_success(f"Started the import: job {job.get('id')} ({job.get('status')}).")
        return
    output.print_success(f"Created {report.get('project_name')} ({report.get('project_id')}).")
    inserted = {table: int(n) for table, n in (report.get("inserted") or {}).items()}
    typer.echo(f"Inserted {sum(inserted.values())} rows in {sum(1 for n in inserted.values() if n)} tables:")
    for table, count in sorted(inserted.items()):
        if count:
            typer.echo(f"  {table}: {count}")
    for line in report_lines(report):
        typer.echo(line)
    if report.get("switched_off"):
        typer.echo(INERT_NOTE)
