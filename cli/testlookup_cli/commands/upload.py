"""Upload test result files to TestLookup for ingestion and AI analysis."""
import asyncio
import json
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.ci_context import resolve_ci_context
from testlookup_cli.commit_range import resolve_commit_range
from testlookup_cli.config import get_profile
from testlookup_cli.errors import exit_code_for, map_connection_error

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


class UploadNotFinished(Exception):
    """--wait ran out of time before the upload reached a terminal state."""

    def __init__(self, message: str, state: str) -> None:
        super().__init__(message)
        self.state = state

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
    }


def _raise_if_refused(data: dict) -> None:
    if data.get("state") != "failed":
        return
    error = data.get("error") or {}
    code = error.get("code", "failed")
    message = error.get("message", "the server could not ingest this report")
    raise Exception(f"Ingestion failed ({code}): {message}")


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

    The file is parsed and ingested asynchronously on the server.
    AI analysis is triggered automatically after ingestion completes.
    ``--wait`` waits for that outcome and exits non-zero when the server
    refuses the report (over the result cap, unparseable, empty). While it
    waits, a 429, a 5xx or a dropped connection only means "not yet", until
    --wait-timeout. An upload that meets a 429 or 503 is retried, honouring
    Retry-After, for up to five minutes.

    Examples:
        testlookup upload file results.xml -p <project-id> -b build-42
        testlookup upload file allure.json -p <project-id> -b v2.5.0 --format allure
        testlookup upload file results.xml -p <project-id> -b build-42 --pr-number 421
        testlookup upload file results.xml -p <project-id> -b build-42 --commit-range-base origin/main
    """
    try:
        commit_range = resolve_commit_range(
            explicit_base=commit_range_base,
            enabled=False if no_commit_range else None,
        )
        data = asyncio.run(_upload_file(
            path=path, project=project, build=build,
            branch=branch, commit=commit, release=release,
            ci_provider=ci_provider, ci_repo=repo, pr_number=pr_number,
            ci_actor=ci_actor, ci_run_url=ci_run_url,
            commit_range=commit_range,
            format=format, profile_name=profile_name,
        ))
        if wait:
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

    results = []
    names: list[str] = []
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
            ))
            results.append(data)
            names.append(f.name)
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
            try:
                data = _outcome(data, asyncio.run(_wait_for_upload(
                    data["task_id"], profile_name=profile_name, timeout=wait_timeout,
                    deadline=deadline,
                )))
                _raise_if_refused(data)
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
) -> dict:
    """Assemble the multipart form fields for POST /api/v1/ingest/file.

    CI context (US-4.3b) is auto-detected from standard CI env vars; the
    explicit ci_* arguments (CLI flags) always win over detection.

    ``commit_range`` (US-8.1) is already-collected local-git output; the
    server takes it as a JSON array string on the multipart form.
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
) -> dict:
    """Upload a single file via multipart POST to /api/v1/ingest/file."""
    import httpx

    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    headers = client._build_headers(profile)

    form_data = _build_form_data(
        project=project, build=build, branch=branch, commit=commit,
        release=release, ci_provider=ci_provider, ci_repo=ci_repo,
        pr_number=pr_number, ci_actor=ci_actor, ci_run_url=ci_run_url,
        commit_range=commit_range, format=format,
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

                if resp.status_code in RETRYABLE_STATUSES:
                    delay = next_retry_delay(
                        resp.headers.get("Retry-After"), attempt, _clock() - started
                    )
                    if delay is not None:
                        output.print_warning(
                            f"HTTP {resp.status_code} for {path.name}: the server is "
                            f"shedding load; retrying in {delay:.1f}s"
                        )
                        await _sleep(delay)
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
                    raise Exception(f"HTTP {resp.status_code}: {detail}{gave_up}")

                return resp.json()
    except httpx.RequestError as exc:
        # Server unreachable / DNS failure / timeout on the primary ingest path
        # (the first thing a new self-hoster runs) — give an actionable hint
        # rather than a raw transport traceback.
        raise map_connection_error(exc, base_url) from exc


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
    url = f"{base_url}/api/v1/ingest/uploads/{task_id}"
    started = _clock()
    if deadline is None:
        deadline = started + timeout
    state = "pending"
    problem: Optional[str] = None  # why the last poll told us nothing, if it did not
    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            delay = WAIT_POLL_SECONDS
            try:
                resp = await http.get(url, headers=headers)
            except httpx.RequestError as exc:
                problem = f"the last poll failed: {map_connection_error(exc, base_url)}"
            else:
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
                    raise Exception(f"HTTP {code} while waiting for upload {task_id}: {detail}")
                else:
                    status = resp.json()
                    if status.get("state") in TERMINAL_UPLOAD_STATES:
                        return status
                    state, problem = status.get("state", "pending"), None
            remaining = deadline - _clock()
            if remaining <= 0:
                if problem is None:
                    raise UploadNotFinished(
                        f"upload {task_id} is still {state} after {timeout:g}s", state
                    )
                raise UploadNotFinished(
                    f"no status for upload {task_id} after {timeout:g}s: {problem}", state
                )
            await _sleep(min(delay, remaining))
