#!/usr/bin/env python3
"""Authorize one SHA using existing, trusted main-push secret-scan evidence.

Only GET requests are made. Missing or pending evidence is retried for a
bounded interval; this tool never starts a workflow or substitutes another SHA.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

WORKFLOW_PATH = ".github/workflows/secret-scan.yml"
PENDING_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}


class SecretScanVerificationError(RuntimeError):
    """The candidate has no acceptable secret-scan evidence."""


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _github_api(path: str, *, timeout: float) -> dict:
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "GET", path],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecretScanVerificationError("Cannot query secret-scan evidence") from exc
    if result.returncode:
        # gh's stderr may contain credentials or other sensitive diagnostics.
        raise SecretScanVerificationError("GitHub rejected the secret-scan evidence query")
    try:
        document = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise SecretScanVerificationError("Invalid secret-scan evidence response") from exc
    if not isinstance(document, dict):
        raise SecretScanVerificationError("Invalid secret-scan evidence response")
    return document


def _trusted_run(run: object, *, repository: str, source_sha: str, workflow_id: int) -> bool:
    if not isinstance(run, dict):
        return False
    for field in ("repository", "head_repository"):
        repo = run.get(field)
        if not isinstance(repo, dict) or str(repo.get("full_name", "")).casefold() != repository.casefold():
            return False
    return (
        run.get("workflow_id") == workflow_id
        and run.get("path") == WORKFLOW_PATH
        and run.get("head_sha") == source_sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and all(_positive_integer(run.get(field)) for field in ("id", "run_number", "run_attempt"))
    )


def verify_secret_scan(
    repository: str,
    source_sha: str,
    *,
    timeout_seconds: float = 900,
    poll_seconds: float = 10,
    api_get=None,
    clock=None,
    sleep=None,
) -> dict:
    """Return successful newest-run evidence, or fail closed without publication."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SecretScanVerificationError("repository must be an owner/repository name")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise SecretScanVerificationError("source_sha must be a lowercase full Git SHA")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 1800:
        raise SecretScanVerificationError("timeout must be between 0 and 1800 seconds")
    if not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 60:
        raise SecretScanVerificationError("poll interval must be between 0 and 60 seconds")
    api_get = api_get or _github_api
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout_seconds

    def request(path: str) -> dict:
        remaining = deadline - clock()
        if remaining <= 0:
            raise SecretScanVerificationError("Timed out waiting for successful exact-SHA secret-scan evidence")
        document = api_get(path, timeout=min(20, remaining))
        if not isinstance(document, dict):
            raise SecretScanVerificationError("Invalid secret-scan evidence response")
        return document

    workflow = request(f"/repos/{repository}/actions/workflows/secret-scan.yml")
    workflow_id = workflow.get("id")
    if not _positive_integer(workflow_id) or workflow.get("path") != WORKFLOW_PATH or workflow.get("state") != "active":
        raise SecretScanVerificationError("The trusted secret-scan workflow is missing, disabled or mismatched")

    while True:
        runs = []
        for page in range(1, 11):
            query = urlencode({
                "head_sha": source_sha, "event": "push", "branch": "main",
                "per_page": 100, "page": page,
            })
            document = request(f"/repos/{repository}/actions/workflows/{workflow_id}/runs?{query}")
            batch, total = document.get("workflow_runs"), document.get("total_count")
            if not isinstance(batch, list) or not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise SecretScanVerificationError("Invalid secret-scan run listing")
            runs.extend(batch)
            if len(batch) > 100 or len(runs) > total:
                raise SecretScanVerificationError("Invalid secret-scan run listing")
            if len(runs) >= total:
                break
            if not batch or page == 10:
                raise SecretScanVerificationError("Incomplete secret-scan run listing")
        candidates = [run for run in runs if _trusted_run(
            run, repository=repository, source_sha=source_sha, workflow_id=workflow_id,
        )]
        if candidates:
            latest = max(candidates, key=lambda run: (run["run_number"], run["id"], run["run_attempt"]))
            selected_id, selected_number, selected_attempt = latest["id"], latest["run_number"], latest["run_attempt"]
            # Re-read the run so a stale list response cannot bless a pending
            # or failed rerun of an earlier successful attempt.
            latest = request(f"/repos/{repository}/actions/runs/{latest['id']}")
            if not _trusted_run(latest, repository=repository, source_sha=source_sha, workflow_id=workflow_id) or (
                latest["id"] != selected_id or latest["run_number"] != selected_number
                or latest["run_attempt"] < selected_attempt
            ):
                raise SecretScanVerificationError("Selected secret-scan evidence has mismatched scope")
            if latest.get("status") == "completed":
                if latest.get("conclusion") != "success":
                    raise SecretScanVerificationError("The latest exact-SHA secret scan did not succeed")
                if clock() > deadline:
                    raise SecretScanVerificationError("Timed out waiting for successful exact-SHA secret-scan evidence")
                return {
                    "repository": repository,
                    "source_sha": source_sha,
                    "workflow_path": WORKFLOW_PATH,
                    "workflow_id": workflow_id,
                    "run_id": latest["id"],
                    "run_attempt": latest["run_attempt"],
                }
            if latest.get("status") not in PENDING_STATUSES:
                raise SecretScanVerificationError("The exact-SHA secret scan has an unsupported state")
        remaining = deadline - clock()
        if remaining <= 0:
            raise SecretScanVerificationError("Timed out waiting for successful exact-SHA secret-scan evidence")
        sleep(min(poll_seconds, remaining))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--poll-seconds", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        evidence = verify_secret_scan(
            args.repository, args.source_sha,
            timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds,
        )
    except SecretScanVerificationError as exc:
        print(f"Secret-scan authorization refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
