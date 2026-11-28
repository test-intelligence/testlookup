"""Upload test result files to TestLookup for ingestion and AI analysis."""
import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional
from uuid import UUID, uuid4

import typer

from testlookup_cli import client, output
from testlookup_cli.ci_context import resolve_ci_context
from testlookup_cli.commit_range import resolve_commit_range
from testlookup_cli.config import get_profile
from testlookup_cli.run_context import collect_run_context, locates_run_context, rejected_entries
from testlookup_cli.errors import CLIError, diagnostic_message, exit_code_for, map_connection_error, map_http_error, response_request_id, validated_request_id

upload_app = typer.Typer(name="upload", help="Upload test result files")

# Canonical set of formats the ingest endpoint accepts. Mirrors the backend's
# own gate (`_SUPPORTED_FORMATS` in backend/app/routers/ingest.py), which
# rejects anything else with a 400. Kept here as the single source for BOTH
# upload commands' `--format` help and client-side validation, so the two can
# neither drift from each other nor silently under-advertise a format the
# backend actually parses.
SUPPORTED_UPLOAD_FORMATS = (
    "auto", "junit", "testng", "allure", "cypress", "playwright", "pytest",
    "robot", "cucumber", "nunit", "trx", "xunit",
)

_FORMAT_HELP = "File format: " + " | ".join(SUPPORTED_UPLOAD_FORMATS)

# ── Retrying a server that is shedding load (re-audit M4, code review) ──────
#
# Since M4, /api/v1/ingest/file shares the per-project batch budget with the
# SDKs and sits behind the Redis backpressure gate, so a CI upload can meet a
# 429 (budget spent, with Retry-After) or a 503 (memory pressure, Retry-After:
# 5). The CLI failed on the first one. These are the SDKs' rules (the Java
# SDK's TestLookupReporter.RetryPolicy): Retry-After is honoured exactly;
# without it the delay doubles from RETRY_BASE_SECONDS; and a retry that would
# take the total past MAX_RETRY_TOTAL_SECONDS is not made.
RETRYABLE_STATUSES = frozenset({429, 503})
RETRY_BASE_SECONDS = 0.5
MAX_RETRY_TOTAL_SECONDS = 300.0

# ── Waiting for the outcome (re-audit N15) ──────────────────────────────────
#
# An upload is accepted before it is parsed. A report the server then refuses
# -- over INGEST_MAX_RESULTS_PER_UPLOAD, unparseable, empty -- left the CLI
# printing "Ingestion queued" and exiting 0, so the CI job stayed green while
# its results were dropped. --wait polls the upload's status to the end.
WAIT_POLL_SECONDS = 2.0
#: The shortest pause between polls, whatever Retry-After says. Proxies and
#: drains send ``Retry-After: 0``, and honouring it exactly polled some twenty
#: thousand times a second during the outage the wait was riding out (QA of N15).
WAIT_MIN_RETRY_SECONDS = 1.0
WAIT_NOT_FOUND_GRACE_SECONDS = 30.0
TERMINAL_UPLOAD_STATES = frozenset({"succeeded", "failed"})
UPLOAD_STATUS_PATH = "/api/v1/ingest/uploads/{task_id}"
UPLOAD_STATUS_PREFIX = UPLOAD_STATUS_PATH.removesuffix("{task_id}")


class UploadNotFinished(CLIError):
    """--wait ran out of time before the upload reached a terminal state."""

    def __init__(self, message: str, state: str, receipt: Optional[dict] = None, *, request_id: object = None) -> None:
        super().__init__(message, request_id=request_id)
        self.state = state
        self.receipt = receipt

# Seams, so tests can run the retry and wait loops on a fake clock.
_sleep = asyncio.sleep
_clock = time.monotonic


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds a ``Retry-After`` header asks for: delta-seconds or an HTTP-date.

    ``None`` when the header is absent or unparseable, so the caller falls back
    to its own backoff.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def next_retry_delay(
    retry_after: Optional[str],
    attempt: int,
    elapsed: float,
    base: float = RETRY_BASE_SECONDS,
    cap: float = MAX_RETRY_TOTAL_SECONDS,
) -> Optional[float]:
    """Seconds to wait after attempt number ``attempt`` failed, or None to give up.

    An explicit ``Retry-After`` wins exactly; otherwise ``base * 2**(attempt-1)``.
    None when the wait would take the total time past ``cap``.
    """
    if elapsed >= cap:
        return None
    parsed = parse_retry_after(retry_after)
    delay = parsed if parsed is not None else base * (2 ** max(0, attempt - 1))
    if delay > cap - elapsed:
        return None
    return delay


def _outcome(accepted: dict, status: dict) -> dict:
    """The accepted upload, with where its processing ended up."""
    return {
        **accepted,
        "state": status.get("state"),
        "result": status.get("result"),
        "error": status.get("error"),
        **({"request_id": request_id} if (request_id := validated_request_id(status.get("request_id"))) else {}),
    }


def _raise_if_refused(data: dict) -> None:
    if data.get("state") != "failed":
        return
    error = data.get("error") or {}
    code = error.get("code", "failed")
    message = error.get("message", "the server could not ingest this report")
    raise CLIError(f"Ingestion failed ({code}): {message}", request_id=data.get("request_id"))


#: The W23b run-context flags, shared by ``upload file`` and ``upload dir``.
_RUN_CONTEXT_ROOT_HELP = "Directory whose lockfiles are hashed into the run context (default: the working directory)"
_RUNTIME_HELP = "A runtime the tests ran on, KEY=VERSION (repeatable), e.g. --runtime java=21.0.2"
_IMAGE_DIGEST_HELP = "An image the tests ran in, NAME=sha256:<64 hex> (repeatable)"


def _run_context(
    *, root: Optional[str], disabled: bool, runtime: Optional[List[str]], images: Optional[List[str]],
) -> Optional[dict]:
    """The run context the upload sends (W23b FAIL-13), collected locally, or None.

    A ``--runtime`` / ``--image-digest`` entry that is not KEY=VALUE with a valid
    key and value is named in a warning and left out -- never sent for the
    server to refuse.
    """
    for kind, flag, values in (("runtime", "--runtime", runtime), ("image", "--image-digest", images)):
        for entry in rejected_entries(values, kind):
            output.print_warning(f"{flag} {entry!r} ignored: not KEY=VALUE with a valid name and value")
    return collect_run_context(
        root=root, enabled=False if disabled else None, runtime=runtime or None, image_digests=images or None,
    )


def _validate_format(value: str) -> str:
    """Reject an unknown ``--format`` locally, before any network call.

    Without this the value is only checked server-side: a typo (``--format
    juint``) costs a round-trip to come back as an opaque 400, and for
    ``upload dir`` that is one wasted POST per file. A ``BadParameter`` renders
    as a clear CLI usage error naming the valid choices instead. Case-sensitive
    to match the backend gate exactly (it too compares against lowercase keys).
    """
    if value not in SUPPORTED_UPLOAD_FORMATS:
        raise typer.BadParameter(
            f"'{value}' is not a supported format. "
            f"Choose one of: {', '.join(SUPPORTED_UPLOAD_FORMATS)}."
        )
    return value


def _validate_idempotency_key(value: Optional[str]) -> Optional[str]:
    if value is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value, flags=re.ASCII) is None:
        raise typer.BadParameter("Idempotency key must be 1–128 ASCII letters/digits or _.:-, starting with a letter or digit")
    return value


def _unconfirmed_receipt(resp) -> Optional[dict]:
    """Recover only explicit server identities; never follow a Location URL."""
    if resp.status_code != 503:
        return None
    headers = {str(key).lower(): value for key, value in resp.headers.items()}
    task, run = headers.get("x-upload-task-id"), headers.get("x-upload-run-id")
    marked = task is not None or run is not None or UPLOAD_STATUS_PREFIX in str(headers.get("location", ""))
    if not marked:
        return None
    try:
        if not isinstance(task, str) or not isinstance(run, str) or str(UUID(task)) != task.lower() or str(UUID(run)) != run.lower():
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise map_http_error(503, "Upload outcome is uncertain and its polling receipt is incomplete. Do not resubmit with a new idempotency key.", request_id=response_request_id(resp)) from None
    receipt = {"task_id": task.lower(), "run_id": run.lower(), "total_results": 0, "publication_confirmed": False}
    request_id = response_request_id(resp)
    if request_id:
        receipt["request_id"] = request_id
    return receipt


#: How far back the server accepts an execution time (``MAX_BACKDATE`` in
#: ``backend/app/services/execution_time.py``); older is replaced by the upload time.
EXECUTED_AT_MAX_AGE_DAYS = 365

_EXECUTED_AT_HELP = (
    "When the tests ran, ISO 8601 (2026-11-20T14:05:00Z, 2026-11-20T14:05:00+01:00 or 2026-11-20); "
    "places the run on that day in charts and windows instead of the upload day. A time without a zone is UTC"
)


def _validate_executed_at(value: str | None) -> str | None:
    """Parse ``--executed-at`` locally and send it normalised, or refuse it before any upload (D-19).

    A value the server cannot parse is a 422 after the file went over the wire;
    one it can parse but considers implausible (in the future, or more than a
    year back) is silently replaced by the upload time. Both are said here.
    """
    if value is None:
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        raise typer.BadParameter(
            f"{value!r} is not an ISO 8601 date-time. Use for example 2026-11-20T14:05:00Z, "
            "2026-11-20T14:05:00+01:00 or 2026-11-20."
        ) from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if when > now + timedelta(minutes=5):
        raise typer.BadParameter(f"{value!r} is in the future; the server would replace it with the upload time.")
    if now - when > timedelta(days=EXECUTED_AT_MAX_AGE_DAYS):
        raise typer.BadParameter(
            f"{value!r} is more than {EXECUTED_AT_MAX_AGE_DAYS} days ago; "
            "the server would replace it with the upload time."
        )
    return when.isoformat()


#: What became of the supplied commit range (S-C-5), from the upload status.
_COMMIT_RANGE_OUTCOME = {
    "stored": "stored",
    "truncated": "stored, but cut to the first commits the server keeps",
    "ignored_malformed": "ignored: the server could not read it",
    "store_failed": "not stored: the server failed to save it (the results were ingested)",
}


def _print_commit_range_outcome(data: dict, *, name: str | None = None, quiet_when_stored: bool = False) -> None:
    """Say what became of the commit range, when the upload status reports it (S-C-5).

    The server reports it only after ingestion (``--wait``) and only when the
    upload carried a range. ``upload dir`` passes ``quiet_when_stored`` so N
    files do not print N identical "stored" lines.
    """
    result = data.get("result")
    outcome = result.get("commit_range") if isinstance(result, dict) else None
    if not isinstance(outcome, str) or not outcome:
        return
    where = f" ({name})" if name else ""
    text = f"Commit range{where}: {_COMMIT_RANGE_OUTCOME.get(outcome, outcome)}"
    if outcome == "stored":
        if not quiet_when_stored:
            output.print_success(text)
    elif outcome in ("ignored_malformed", "store_failed"):
        output.print_warning(f"{text}. Suspects and bisect cannot use it for this run.")
    else:
        output.print_warning(text)


@upload_app.command("file")
def upload_file(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to test result file"),
    project: str = typer.Option(..., "--project", "-p", help="Project ID (UUID)"),
    build: str = typer.Option(..., "--build", "-b", help="Build number / identifier"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Git branch"),
    commit: Optional[str] = typer.Option(None, "--commit", help="Git commit SHA"),
    release: Optional[str] = typer.Option(None, "--release", help="Release name"),
    ci_provider: Optional[str] = typer.Option(None, "--ci-provider", help="CI provider (overrides auto-detection)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository as org/name (overrides auto-detection)"),
    pr_number: Optional[int] = typer.Option(None, "--pr-number", min=1, help="Pull/merge request number (overrides auto-detection)"),
    ci_actor: Optional[str] = typer.Option(None, "--ci-actor", help="CI actor / triggering user (overrides auto-detection)"),
    ci_run_url: Optional[str] = typer.Option(None, "--ci-run-url", help="CI run/job URL (overrides auto-detection)"),
    commit_range_base: Optional[str] = typer.Option(
        None, "--commit-range-base",
        help="Base ref/SHA for commit collection (overrides CI + local-git detection)",
    ),
    no_commit_range: bool = typer.Option(
        False, "--no-commit-range",
        help="Skip collecting the commit range from local git",
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", help="Environment the run executed against (e.g. staging)",
    ),
    executed_at: str | None = typer.Option(
        None, "--executed-at", help=_EXECUTED_AT_HELP, callback=_validate_executed_at,
    ),
    run_context_root: Optional[str] = typer.Option(None, "--run-context-root", help=_RUN_CONTEXT_ROOT_HELP),
    no_run_context: bool = typer.Option(
        False, "--no-run-context", help="Send no run context (runtimes, lockfile hashes, images, env)",
    ),
    runtime: Optional[List[str]] = typer.Option(None, "--runtime", help=_RUNTIME_HELP),
    image_digest: Optional[List[str]] = typer.Option(None, "--image-digest", help=_IMAGE_DIGEST_HELP),
    no_ai: bool = typer.Option(
        False, "--no-ai", help="Skip the AI analysis, suite comparison and automatic investigation of this run (sends run_ai=false)",
    ),
    format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help=_FORMAT_HELP,
        callback=_validate_format,
    ),
    wait: bool = typer.Option(
        False, "--wait/--no-wait",
        help="Wait until the server has ingested the report; exit non-zero if it refuses it",
    ),
    wait_timeout: float = typer.Option(
        600.0, "--wait-timeout", min=1.0,
        help="Seconds --wait waits before giving up",
    ),
    idempotency_key: Optional[str] = typer.Option(
        None, "--idempotency-key", callback=_validate_idempotency_key,
        help="Reuse this key with identical file/fields after an uncertain response (receipt-aware servers)",
    ),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload a single test result file.

    Accepts JUnit/TestNG XML, Allure JSON, Cypress (Mochawesome) JSON,
    Playwright JSON, pytest --json-report, Robot Framework output.xml,
    Cucumber JSON, NUnit3 XML, Visual Studio TRX, or xUnit.net v2 XML.
    ``--format auto`` (default) detects the format from the file content.

    CI context (provider, repo, PR number, actor, run URL) is auto-detected
    from standard CI env vars (GitHub Actions, GitLab CI, Jenkins, Azure
    DevOps, CircleCI); the --ci-provider/--repo/--pr-number/--ci-actor/
    --ci-run-url options override detection.

    The commit range (base..HEAD, with per-commit changed files) is collected
    from local git so failures can be attributed to commits with no VCS token
    and no network. --commit-range-base pins the base; --no-commit-range (or
    TESTLOOKUP_COMMIT_RANGE=0) skips collection entirely. Any git problem is
    non-fatal — the range is simply omitted.

    The run context (the sha256 of the lockfiles in --run-context-root or the
    working directory, --runtime / TESTLOOKUP_RUNTIME versions, --image-digest
    images and an allowlist of environment values) lets the run page show
    configuration drift; nothing is fetched over the network, and this CLI's
    own Python is never recorded as the tests' runtime. --no-run-context (or
    TESTLOOKUP_RUN_CONTEXT=0) sends none. --environment labels the run;
    --no-ai skips its AI analysis. --executed-at says when the tests ran, so a
    report uploaded later (a backfill, last night's job) lands on that day in
    charts and windows; it is checked here before anything is sent.

    The file is parsed and ingested asynchronously on the server.
    AI analysis is triggered automatically after ingestion completes.
    ``--wait`` waits for that outcome and exits non-zero when the server
    refuses the report (over the result cap, unparseable, empty), and says
    what became of the commit range (stored, ignored, not stored). While it
    waits, a 429, a 5xx or a dropped connection only means "not yet", until
    --wait-timeout. Admission 429/503 responses retry within five minutes.
    A 503 carrying an upload receipt polls that original task instead of
    posting again. --idempotency-key can resume an uncertain no-receipt attempt
    with identical input on a receipt-aware server.

    Examples:
        testlookup upload file results.xml -p <project-id> -b build-42
        testlookup upload file allure.json -p <project-id> -b v2.5.0 --format allure
        testlookup upload file results.xml -p <project-id> -b build-42 --pr-number 421
        testlookup upload file results.xml -p <project-id> -b build-42 --commit-range-base origin/main
        testlookup upload file results.xml -p <project-id> -b build-42 --runtime java=21.0.2 --environment staging --no-ai
        testlookup upload file results.xml -p <project-id> -b build-41 --executed-at 2026-11-20T23:10:00Z
    """
    try:
        commit_range = resolve_commit_range(
            explicit_base=commit_range_base,
            enabled=False if no_commit_range else None,
        )
        run_context = _run_context(
            root=run_context_root, disabled=no_run_context, runtime=runtime, images=image_digest,
        )
        data = asyncio.run(_upload_file(
            path=path, project=project, build=build,
            branch=branch, commit=commit, release=release,
            ci_provider=ci_provider, ci_repo=repo, pr_number=pr_number,
            ci_actor=ci_actor, ci_run_url=ci_run_url,
            commit_range=commit_range,
            format=format, profile_name=profile_name,
            run_context=run_context, environment=environment, run_ai=not no_ai,
            executed_at=executed_at,
            idempotency_key=idempotency_key,
            confirmation_timeout=min(wait_timeout, MAX_RETRY_TOTAL_SECONDS) if wait else MAX_RETRY_TOTAL_SECONDS,
        ))
        if wait and data.get("state") not in TERMINAL_UPLOAD_STATES:
            data = _outcome(data, asyncio.run(_wait_for_upload(
                data["task_id"], profile_name=profile_name, timeout=wait_timeout,
            )))
        output.render(data, output_format)
        _raise_if_refused(data)
        # Keep structured stdout parseable (`upload ... --output json | jq -r
        # '.run_id'` feeds ci-verdict in CI recipes) — the run_id is already
        # in the rendered JSON/YAML document.
        if output_format not in ("json", "yaml"):
            run_id = data.get("run_id", "?")
            verb = "Ingested" if data.get("state") == "succeeded" else "Ingestion queued"
            output.print_success(f"{verb} — run_id={run_id}")
            _print_commit_range_outcome(data)
    except UploadNotFinished as e:
        if e.receipt:
            output.render({**e.receipt, "state": e.state}, output_format)
        output.print_error(str(e))
        raise typer.Exit(1)
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@upload_app.command("dir")
def upload_dir(
    directory: Path = typer.Argument(..., exists=True, file_okay=False, help="Directory of result files"),
    project: str = typer.Option(..., "--project", "-p", help="Project ID (UUID)"),
    build: str = typer.Option(..., "--build", "-b", help="Build number / identifier"),
    branch: Optional[str] = typer.Option(None, "--branch"),
    commit: Optional[str] = typer.Option(None, "--commit"),
    release: Optional[str] = typer.Option(None, "--release"),
    ci_provider: Optional[str] = typer.Option(None, "--ci-provider", help="CI provider (overrides auto-detection)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository as org/name (overrides auto-detection)"),
    pr_number: Optional[int] = typer.Option(None, "--pr-number", min=1, help="Pull/merge request number (overrides auto-detection)"),
    ci_actor: Optional[str] = typer.Option(None, "--ci-actor", help="CI actor / triggering user (overrides auto-detection)"),
    ci_run_url: Optional[str] = typer.Option(None, "--ci-run-url", help="CI run/job URL (overrides auto-detection)"),
    commit_range_base: Optional[str] = typer.Option(
        None, "--commit-range-base",
        help="Base ref/SHA for commit collection (overrides CI + local-git detection)",
    ),
    no_commit_range: bool = typer.Option(
        False, "--no-commit-range",
        help="Skip collecting the commit range from local git",
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", help="Environment the run executed against (e.g. staging)",
    ),
    executed_at: str | None = typer.Option(
        None, "--executed-at", help=_EXECUTED_AT_HELP, callback=_validate_executed_at,
    ),
    run_context_root: Optional[str] = typer.Option(None, "--run-context-root", help=_RUN_CONTEXT_ROOT_HELP),
    no_run_context: bool = typer.Option(
        False, "--no-run-context", help="Send no run context (runtimes, lockfile hashes, images, env)",
    ),
    runtime: Optional[List[str]] = typer.Option(None, "--runtime", help=_RUNTIME_HELP),
    image_digest: Optional[List[str]] = typer.Option(None, "--image-digest", help=_IMAGE_DIGEST_HELP),
    no_ai: bool = typer.Option(
        False, "--no-ai", help="Skip the AI analysis of these runs (sends run_ai=false)",
    ),
    format: str = typer.Option(
        "auto", "--format", "-f",
        help=_FORMAT_HELP,
        callback=_validate_format,
    ),
    wait: bool = typer.Option(
        False, "--wait/--no-wait",
        help="Wait until the server has ingested the report; exit non-zero if it refuses it",
    ),
    wait_timeout: float = typer.Option(
        600.0, "--wait-timeout", min=1.0,
        help="Seconds --wait waits, in total for every file, before giving up",
    ),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload all test result files (.xml, .trx, .json) in a directory.

    With ``--wait``, one ``--wait-timeout`` covers every file: the command
    exits non-zero and names each file that had not finished when it ran out.

    Each file is uploaded as a separate ingestion job. CI context is
    auto-detected from standard CI env vars (override with --ci-provider /
    --repo / --pr-number / --ci-actor / --ci-run-url).

    The commit range is collected from local git ONCE and stamped on every
    file in the directory (they are all one execution of one checkout). Pin it
    with --commit-range-base, or skip it with --no-commit-range.

    Examples:
        testlookup upload dir ./target/surefire-reports -p <project-id> -b build-42
        testlookup upload dir ./allure-results -p <project-id> -b v2.5.0 --format allure
    """
    files = sorted(
        list(directory.glob("*.xml")) + list(directory.glob("*.json")) + list(directory.glob("*.trx"))
    )
    if not files:
        output.print_error(f"No .xml or .json files found in {directory}")
        raise typer.Exit(1)

    # Collected once: every file here came from the same checkout, and N git
    # walks for one range would be pure waste.
    commit_range = resolve_commit_range(
        explicit_base=commit_range_base,
        enabled=False if no_commit_range else None,
    )
    # The run context too: one checkout, one runtime, collected once.
    run_context = _run_context(
        root=run_context_root, disabled=no_run_context, runtime=runtime, images=image_digest,
    )

    results = []
    names: list[str] = []
    reconciled: set[str] = set()
    errors = 0
    for f in files:
        try:
            data = asyncio.run(_upload_file(
                path=f, project=project, build=build,
                branch=branch, commit=commit, release=release,
                ci_provider=ci_provider, ci_repo=repo, pr_number=pr_number,
                ci_actor=ci_actor, ci_run_url=ci_run_url,
                commit_range=commit_range,
                format=format, profile_name=profile_name,
                run_context=run_context, environment=environment, run_ai=not no_ai,
                executed_at=executed_at,
                confirmation_timeout=min(wait_timeout, MAX_RETRY_TOTAL_SECONDS) if wait else MAX_RETRY_TOTAL_SECONDS,
            ))
            results.append(data)
            names.append(f.name)
            if data.get("state") in TERMINAL_UPLOAD_STATES:
                reconciled.add(f.name)
            _raise_if_refused(data)
        except UploadNotFinished as e:
            if e.receipt:
                results.append({**e.receipt, "state": e.state})
                names.append(f.name)
                reconciled.add(f.name)
            output.print_error(f"Not finished: {f.name} — {e}")
            errors += 1
        except Exception as e:
            output.print_error(f"Failed: {f.name} — {e}")
            errors += 1

    if wait:
        # Every file is accepted before any is parsed, so wait for the outcomes
        # only now: the server works through them while we wait. One deadline
        # covers every file (QA of N15): each file used to wait its own
        # --wait-timeout, so 20 files with the workers down held a CI job for
        # 3 h 20 min. A file reached after the deadline still gets one status
        # check, so one that has finished is reported as it is.
        deadline = _clock() + wait_timeout
        waited = []
        unfinished: list[str] = []
        for name, data in zip(names, results):
            # A receipt-bearing 503 already used its bounded confirmation
            # budget. Keep its identity/output and count any failure once.
            if name in reconciled:
                waited.append(data)
                continue
            try:
                data = _outcome(data, asyncio.run(_wait_for_upload(
                    data["task_id"], profile_name=profile_name, timeout=wait_timeout,
                    deadline=deadline,
                )))
                _raise_if_refused(data)
                if output_format not in ("json", "yaml"):
                    _print_commit_range_outcome(data, name=name, quiet_when_stored=True)
            except UploadNotFinished as e:
                output.print_error(f"Not finished: {name} — {e}")
                data = {**data, "state": e.state}
                unfinished.append(name)
                errors += 1
            except Exception as e:
                output.print_error(f"Failed: {name} — {e}")
                errors += 1
            waited.append(data)
        results = waited
        if unfinished:
            output.print_error(
                f"Not finished within --wait-timeout ({wait_timeout:.0f}s): "
                + ", ".join(unfinished)
            )

    if results:
        output.render(results, output_format)

    verb = "Ingested" if wait else "Uploaded"
    summary = f"{verb} {len(files) - errors}/{len(files)} files ({errors} errors)"
    if errors:
        # Any failed upload fails the command. A CI ingest step reads the exit
        # code, and returning 0 here let reports silently never land while the
        # pipeline went green — the same false-success trap the empty-directory
        # guard above avoids. Mirrors `upload file`, which exits non-zero on its
        # single failure.
        output.print_error(summary)
        raise typer.Exit(1)
    output.print_success(summary)


def _build_form_data(
    project: str,
    build: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    release: Optional[str] = None,
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_actor: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    commit_range: Optional[list] = None,
    format: str = "auto",
    run_context: Optional[dict] = None,
    environment: Optional[str] = None,
    run_ai: bool = True,
    executed_at: str | None = None,
) -> dict:
    """Assemble the multipart form fields for POST /api/v1/ingest/file.

    CI context (US-4.3b) is auto-detected from standard CI env vars; the
    explicit ci_* arguments (CLI flags) always win over detection.

    ``commit_range`` (US-8.1) is already-collected local-git output; the
    server takes it as a JSON array string on the multipart form.

    ``run_context`` (W23b) goes as compact JSON text; ``environment`` as the
    form's existing field; ``run_ai=False`` as ``run_ai=false`` (no AI analysis).
    """
    form_data = {
        "project_id": project,
        "build_number": build,
        "format": format,
    }
    if branch:
        form_data["branch"] = branch
    if commit:
        form_data["commit_hash"] = commit
    if release:
        form_data["release_name"] = release

    ci_context = resolve_ci_context({
        "ci_provider": ci_provider,
        "ci_repo": ci_repo,
        "pr_number": pr_number,
        "ci_actor": ci_actor,
        "ci_run_url": ci_run_url,
    })
    for field, value in ci_context.items():
        form_data[field] = str(value)

    if commit_range:
        form_data["commit_range"] = json.dumps(commit_range)
    if run_context:
        form_data["run_context"] = json.dumps(run_context, separators=(",", ":"))
    if environment:
        form_data["environment"] = environment
    if not run_ai:
        form_data["run_ai"] = "false"
    if executed_at:
        # The form's existing field (D-19): the run's execution time, so charts
        # and windows place it on the day it ran, not the day it was uploaded.
        form_data["executed_at"] = executed_at

    return form_data


async def _upload_file(
    path: Path,
    project: str,
    build: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    release: Optional[str] = None,
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_actor: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    commit_range: Optional[list] = None,
    format: str = "auto",
    profile_name: Optional[str] = None,
    run_context: Optional[dict] = None,
    environment: Optional[str] = None,
    run_ai: bool = True,
    executed_at: str | None = None,
    idempotency_key: Optional[str] = None,
    confirmation_timeout: float = MAX_RETRY_TOTAL_SECONDS,
) -> dict:
    """Upload a single file via multipart POST to /api/v1/ingest/file.

    A 422 that blames the ``run_context`` field (a server whose allowlist is
    older than this CLI) is retried ONCE without it, with a warning: results
    are never lost to metadata.
    """
    import httpx

    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    key = _validate_idempotency_key(idempotency_key) or str(uuid4())
    headers = {**client._build_headers(profile), "Idempotency-Key": key}

    form_data = _build_form_data(
        project=project, build=build, branch=branch, commit=commit,
        release=release, ci_provider=ci_provider, ci_repo=ci_repo,
        pr_number=pr_number, ci_actor=ci_actor, ci_run_url=ci_run_url,
        commit_range=commit_range, format=format,
        run_context=run_context, environment=environment, run_ai=run_ai,
        executed_at=executed_at,
    )

    started = _clock()
    attempt = 0
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            while True:
                attempt += 1
                # Re-opened per attempt: a retry must send the whole file again.
                with open(path, "rb") as f:
                    resp = await http.post(
                        f"{base_url}/api/v1/ingest/file",
                        headers=headers,
                        files={"file": (path.name, f, "application/octet-stream")},
                        data=form_data,
                    )

                receipt = _unconfirmed_receipt(resp)
                if receipt is not None:
                    output.print_warning(diagnostic_message(
                        f"Queue confirmation is unavailable for run {receipt['run_id']}; "
                        f"checking upload {receipt['task_id']} without resubmitting.", receipt.get("request_id"),
                    ))
                    deadline = min(started + MAX_RETRY_TOTAL_SECONDS, _clock() + confirmation_timeout)
                    try:
                        status = await _wait_for_upload(
                            receipt["task_id"], profile_name=profile_name,
                            timeout=max(0, deadline - _clock()), deadline=deadline,
                            expected_run_id=receipt["run_id"],
                        )
                    except UploadNotFinished as exc:
                        if exc.request_id:
                            receipt = {**receipt, "request_id": exc.request_id}
                        raise UploadNotFinished(exc.args[0], exc.state, receipt, request_id=exc.request_id or receipt.get("request_id")) from exc
                    return {**_outcome(receipt, status), "publication_confirmed": status.get("state") == "succeeded"}

                if resp.status_code in RETRYABLE_STATUSES:
                    delay = next_retry_delay(
                        resp.headers.get("Retry-After"), attempt, _clock() - started
                    )
                    if delay is not None:
                        output.print_warning(diagnostic_message(
                            f"HTTP {resp.status_code} for {path.name}: the server is "
                            f"shedding load; retrying in {delay:.1f}s", response_request_id(resp),
                        ))
                        await _sleep(delay)
                        continue

                if resp.status_code == 422 and "run_context" in form_data and _blames_run_context(resp):
                    output.print_warning(
                        f"The server refused the run context for {path.name} (422); "
                        "uploading without it."
                    )
                    form_data.pop("run_context", None)
                    continue

                if resp.status_code >= 400:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except Exception:
                        detail = resp.text
                    gave_up = (
                        f" (gave up after {attempt} attempts)"
                        if resp.status_code in RETRYABLE_STATUSES and attempt > 1
                        else ""
                    )
                    raise map_http_error(resp.status_code, f"{detail}{gave_up}", request_id=response_request_id(resp))

                return client._json_body(resp)
    except httpx.RequestError as exc:
        # Server unreachable / DNS failure / timeout on the primary ingest path
        # (the first thing a new self-hoster runs) — give an actionable hint
        # rather than a raw transport traceback.
        error = map_connection_error(exc, base_url)
        raise type(error)(
            f"{error} Upload outcome may be uncertain. On a receipt-aware server, "
            f"retry identical input with --idempotency-key {key}.", error.exit_code,
        ) from exc


def _blames_run_context(resp) -> bool:
    """A 422 whose body locates its error under ``run_context`` (never raises)."""
    try:
        return locates_run_context(resp.json())
    except Exception:
        return False


def _poll_is_transient(status_code: int) -> bool:
    """A status-poll answer that says nothing about the upload itself.

    A 429 or a 5xx, to a GET that changes nothing: an ingress mid-rollout, a
    restarting pod, a rate budget that refills. The upload carries on.
    """
    return status_code == 429 or status_code >= 500


async def _wait_for_upload(
    task_id: str,
    profile_name: Optional[str] = None,
    timeout: float = 600.0,
    deadline: Optional[float] = None,
    expected_run_id: Optional[str] = None,
) -> dict:
    """Poll the upload's status until it succeeds or fails (re-audit N15).

    Returns the terminal status, ``failed`` included: the caller decides what a
    refusal means. Raises :class:`UploadNotFinished` when the deadline passes
    first -- ``deadline`` (a ``_clock()`` reading) when given, which ``upload
    dir`` shares across its files, else ``timeout`` from now -- and at once on
    an answer that cannot change: 401, 403, any other 4xx, or a 404 once
    ``WAIT_NOT_FOUND_GRACE_SECONDS`` have passed. The status is always polled
    at least once, even when the deadline has already passed.

    A transient answer is "not yet" (code review of N15): a 429, a 5xx or a
    dropped connection. One 502 from the ingress during a rolling deploy used
    to fail the CI step although the upload went on to succeed. The next poll
    waits for the server's Retry-After when it sends one, else the poll
    interval, but never past the deadline: the last poll comes at the deadline.
    """
    import httpx

    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    headers = client._build_headers(profile)
    url = base_url + UPLOAD_STATUS_PATH.format(task_id=task_id)
    started = _clock()
    if deadline is None:
        deadline = started + timeout
    state = "pending"
    problem: Optional[str] = None  # why the last poll told us nothing, if it did not
    last_request_id = None
    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            delay = WAIT_POLL_SECONDS
            try:
                resp = await http.get(url, headers=headers)
            except httpx.RequestError as exc:
                problem = f"the last poll failed: {map_connection_error(exc, base_url)}"
            else:
                last_request_id = response_request_id(resp)
                code = resp.status_code
                if code == 404 and _clock() - started < WAIT_NOT_FOUND_GRACE_SECONDS:
                    # The status record may not be written yet.
                    problem = "the server did not know the upload yet (HTTP 404)"
                elif _poll_is_transient(code):
                    problem = f"the last poll got HTTP {code}"
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    if retry_after is not None:
                        delay = max(retry_after, WAIT_MIN_RETRY_SECONDS)
                elif code >= 400:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except Exception:
                        detail = resp.text
                    raise map_http_error(
                        code, f"while waiting for upload {task_id}: {detail}", request_id=last_request_id,
                    )
                else:
                    status = client._json_body(resp)
                    if expected_run_id is not None and (
                        status.get("task_id") != task_id
                        or (status.get("run_id") and status["run_id"] != expected_run_id)
                    ):
                        raise map_http_error(503, f"Status identity differs from upload {task_id}; do not resubmit.", request_id=last_request_id)
                    if status.get("state") in TERMINAL_UPLOAD_STATES:
                        if status.get("state") == "failed":
                            status = {key: value for key, value in status.items() if key != "request_id"}
                            if last_request_id:
                                status["request_id"] = last_request_id
                        return status
                    state, problem = status.get("state", "pending"), None
            remaining = deadline - _clock()
            if remaining <= 0:
                if problem is None:
                    raise UploadNotFinished(
                        f"upload {task_id} is still {state} after {timeout:g}s", state, request_id=last_request_id,
                    )
                raise UploadNotFinished(
                    f"no status for upload {task_id} after {timeout:g}s: {problem}", state, request_id=last_request_id,
                )
            await _sleep(min(delay, remaining))
