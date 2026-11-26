"""``testlookup ci-verdict`` — gate CI on REAL failures only (US-5.2).

Fetches a run's failed/broken tests plus the project's quarantine manifest
and exits 0 when every failure is a currently-quarantined (known-flaky)
test — so flaky tests stop blocking merges with zero test-code changes.

Exit-code contract (stable, for CI):

* ``0`` — no real failures. Quarantined failures don't block.
* ``1`` — at least one real (non-quarantined) failure; with ``--strict``,
  ANY failure including quarantined ones.
* ``2`` — API/network error, or the run didn't finish within
  ``--timeout-seconds``. **Fails CLOSED by default**: a broken TestLookup
  must never silently green a build. Teams that prefer availability over
  strictness can pass ``--fail-open`` (exit 0 with a loud warning on
  infrastructure errors).
* ``2`` — also when the run cannot be judged yet: it is still in progress
  (its failures may not have arrived), or it holds zero tests (nothing was
  ingested). ``--fail-open`` does not change this; ``--allow-partial`` judges
  the run as it is now.

Matching: failures are matched against the manifest primarily by test
fingerprint (``sha256(class_name::test_name)[:16]``, recomputed
client-side); when that misses, an exact ``(test_name, suite_name)`` tuple
match is used as a fallback.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.verdict_logic import (
    EXIT_INFRA_ERROR,
    FAILURE_STATUSES,
    build_verdict,
    partition_failures,
)

# Run statuses that mean "still executing" — everything else is terminal.
# The backend's run status is ``LaunchStatus`` (backend/app/models/postgres.py):
# IN_PROGRESS, then PASSED, FAILED or STOPPED. ``RUNNING`` is not a backend
# status; it stays here so a server that ever sends it is waited on, not judged.
_IN_PROGRESS_STATUSES = {"IN_PROGRESS", "RUNNING"}


class RunNotReadyError(Exception):
    """The run was read, but a verdict on it now could green a failing build."""


def _refuse_a_run_that_is_not_ready(run_id: str, run: dict) -> None:
    """Refuse a run that is still in progress or holds zero tests.

    Neither has a failed test *yet*, so without this the gate would answer
    "no real failures" and exit 0 for a build whose results have not arrived,
    or whose upload the server rejected.
    """
    status = str(run.get("status", "")).upper()
    if status in _IN_PROGRESS_STATUSES:
        raise RunNotReadyError(
            f"Run {run_id} is still in progress (status {status}), so its "
            "failures may not have arrived yet. Pass --timeout-seconds N to "
            "wait for it to finish, or --allow-partial to judge it as it is now."
        )
    # Only a count the server reports as zero is refused; a payload without
    # the field is judged from its failures as before.
    if run.get("total_tests") == 0:
        raise RunNotReadyError(
            f"Run {run_id} has no tests (status {status or 'unknown'}), so there "
            "is nothing to judge. Check that the results were ingested, or pass "
            "--allow-partial to accept an empty run."
        )
_POLL_INTERVAL_SECONDS = 5.0
_PAGE_SIZE = 200


async def _fetch_run(run_id: str, profile_name: Optional[str]) -> dict:
    return await client.request(
        "GET", f"/api/v1/runs/{run_id}", profile_name=profile_name
    )


async def _wait_for_completion(
    run_id: str, timeout_seconds: int, profile_name: Optional[str]
) -> dict:
    """Poll the run until it leaves IN_PROGRESS or the budget is spent.

    Returns the last-seen run payload; raises ``TimeoutError`` when the run
    is still executing after the budget — a verdict on a partial run could
    green a build whose failures simply haven't landed yet.
    """
    deadline = time.monotonic() + max(0, timeout_seconds)
    run = await _fetch_run(run_id, profile_name)
    while str(run.get("status", "")).upper() in _IN_PROGRESS_STATUSES:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Run {run_id} still in progress after {timeout_seconds}s — "
                "refusing to compute a verdict on partial results"
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        run = await _fetch_run(run_id, profile_name)
    return run


async def _fetch_failures(run_id: str, profile_name: Optional[str]) -> list[dict]:
    """All FAILED + BROKEN test cases for the run (paginated)."""
    failures: list[dict] = []
    for status in FAILURE_STATUSES:
        page = 1
        while True:
            data = await client.request(
                "GET",
                f"/api/v1/runs/{run_id}/tests",
                params={"status": status, "page": page, "size": _PAGE_SIZE},
                profile_name=profile_name,
            )
            items = data.get("items", []) if isinstance(data, dict) else data
            failures.extend(items)
            pages = data.get("pages", 1) if isinstance(data, dict) else 1
            if page >= (pages or 1) or not items:
                break
            page += 1
    return failures


async def _fetch_manifest(project_id: str, profile_name: Optional[str]) -> dict:
    return await client.request(
        "GET",
        f"/api/v1/projects/{project_id}/quarantine/manifest",
        profile_name=profile_name,
    )


async def _gather(
    run_id: str,
    project: Optional[str],
    timeout_seconds: int,
    profile_name: Optional[str],
    allow_partial: bool = False,
) -> tuple[list[dict], dict]:
    if timeout_seconds > 0:
        run = await _wait_for_completion(run_id, timeout_seconds, profile_name)
    else:
        run = await _fetch_run(run_id, profile_name)
    if not allow_partial:
        _refuse_a_run_that_is_not_ready(run_id, run)
    project_id = project or str(run.get("project_id", ""))
    if not project_id:
        raise ValueError(
            "Could not determine the project — pass --project explicitly"
        )
    failures = await _fetch_failures(run_id, profile_name)
    manifest = await _fetch_manifest(project_id, profile_name)
    return failures, manifest


# D-55: three columns, so a CI log at 80 characters keeps each row on its own lines; ``class_name`` stays
# in ``-o json`` (the matching uses it, the reader rarely needs it to find the test).
_FAILURE_COLUMNS = ["test_name", "suite_name", "status"]


def _render_table(real: list[dict], quarantined: list[dict], strict: bool) -> None:
    if real:
        output.print_table(real, _FAILURE_COLUMNS, title="REAL failures (blocking)")
    if quarantined:
        output.print_table(
            quarantined,
            _FAILURE_COLUMNS + ["matched_by"],
            title="QUARANTINED failures (suppressed)",
        )
    total = len(real) + len(quarantined)
    if total == 0:
        output.print_success("No failed or broken tests in this run.")
    elif not real and not strict:
        output.print_success(
            f"All {total} failure(s) are quarantined (known-flaky) — not blocking."
        )
    elif not real and strict:
        output.print_error(
            f"--strict: {len(quarantined)} quarantined failure(s) still block."
        )
    else:
        output.print_error(
            f"{len(real)} REAL failure(s) ({len(quarantined)} quarantined suppressed)."
        )


def ci_verdict(
    # Positional like every other run command (``runs get <id>``, ``tests list
    # <id>``); ``--run`` stays for the pipelines already written with it (D-124).
    run_arg: str | None = typer.Argument(
        None, metavar="[RUN_ID]", help="Run ID to compute the verdict for (or --run)",
    ),
    run_option: str | None = typer.Option(
        None, "--run", help="Run ID to compute the verdict for (same as the RUN_ID argument)",
    ),
    project: Optional[str] = typer.Option(
        None, "--project", "-p",
        help="Project ID (inferred from the run when omitted)",
    ),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit 1 on ANY failure, including quarantined ones",
    ),
    fail_open: bool = typer.Option(
        False, "--fail-open",
        help=(
            "On API/network errors exit 0 with a loud warning instead of 2. "
            "Default is fail-CLOSED: a broken TestLookup must not green a build."
        ),
    ),
    timeout_seconds: int = typer.Option(
        0, "--timeout-seconds",
        help=(
            "Wait up to N seconds for the run to finish before judging "
            "(polls every 5s). Default 0 = do not wait: a run that is still "
            "in progress exits 2 unless --allow-partial is given."
        ),
    ),
    allow_partial: bool = typer.Option(
        False, "--allow-partial",
        help=(
            "Judge the run as it is now even when it is still in progress or "
            "holds zero tests. Without it both exit 2: failures that have not "
            "arrived yet must not green a build."
        ),
    ),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o", help="table|json"),
):
    """Exit 0 when the only failures in a run are quarantined/known-flaky tests.

    Matches the run's FAILED/BROKEN tests against the project's quarantine
    manifest — primarily by test fingerprint (sha256(class::name)[:16],
    recomputed client-side), falling back to an exact (test_name, suite_name)
    tuple match. Exit codes: 0 = no real failures, 1 = real failures
    (or --strict + any failure), 2 = API error / run-completion timeout
    (fail-closed; see --fail-open), and 2 = the run is still in progress or
    holds zero tests (see --allow-partial).
    """
    if run_arg and run_option and run_arg != run_option:
        # A CI gate must not guess which of two runs it was asked about.
        raise typer.BadParameter(
            f"two different runs given: RUN_ID {run_arg!r} and --run {run_option!r}; pass one.",
            param_hint="RUN_ID / --run",
        )
    run_id = run_option or run_arg
    if not run_id:
        raise typer.BadParameter("give the run as RUN_ID or --run <run-id>.", param_hint="RUN_ID / --run")
    try:
        failures, manifest = asyncio.run(
            _gather(run_id, project, timeout_seconds, profile_name, allow_partial)
        )
    except RunNotReadyError as exc:
        # Not an infrastructure error: TestLookup answered, the run is not
        # ready. --fail-open must not turn this into a pass.
        output.print_error(f"ci-verdict refused: {exc}")
        raise typer.Exit(EXIT_INFRA_ERROR)
    except Exception as exc:  # noqa: BLE001 — verdict must map ALL errors to the contract
        if fail_open:
            output.print_warning(
                f"ci-verdict could not reach TestLookup ({exc}) — "
                "--fail-open is set, so NOT blocking this build. "
                "Failures were NOT checked against the quarantine manifest."
            )
            raise typer.Exit(0)
        output.print_error(
            f"ci-verdict failed: {exc} (failing CLOSED — pass --fail-open to override)"
        )
        raise typer.Exit(EXIT_INFRA_ERROR)

    entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
    real, quarantined = partition_failures(failures, entries)
    verdict = build_verdict(real, quarantined, strict=strict)

    if output_format == "json":
        output.print_json(verdict)
    else:
        _render_table(real, quarantined, strict)

    raise typer.Exit(verdict["exit_code"])
