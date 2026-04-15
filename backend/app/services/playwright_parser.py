"""
Playwright JSON reporter parser — Tier 1 item 1.

Playwright's built-in ``--reporter=json`` (also the ``PLAYWRIGHT_JSON_OUTPUT_FILE``
output) produces a single JSON document roughly shaped like:

.. code-block:: json

    {
      "config": { "projects": [{ "name": "chromium" }, ...] },
      "suites": [
        {
          "title": "login.spec.ts",
          "file": "tests/login.spec.ts",
          "specs": [
            {
              "title": "redirects to dashboard",
              "file": "tests/login.spec.ts",
              "line": 12,
              "tests": [
                {
                  "projectName": "chromium",
                  "expectedStatus": "passed",
                  "status": "expected|unexpected|flaky|skipped",
                  "results": [
                    {
                      "status": "passed|failed|timedOut|skipped|interrupted",
                      "duration": 1234,
                      "retry": 0,
                      "error": {"message": "...", "stack": "..."},
                      "errors": [{"message": "...", "stack": "..."}]
                    }
                  ]
                }
              ]
            }
          ],
          "suites": [ ...nested... ]
        }
      ]
    }

Key nuances:

* Playwright's "test" is a (spec, browser project) pair. We emit one
  normalized case per combination so the same spec running on chromium
  and webkit show up as two cases — matching how users actually triage.
* Results contain retry attempts. We take the final attempt for the
  normalized status but surface ``retry_count`` on the case.
* ``flaky`` in the test-level ``status`` means "failed then passed on
  retry" — Playwright still runs it as PASSED overall but we tag
  ``is_flaky`` so the downstream flaky detector can pick it up.
* Playwright uses ``timedOut`` for its own timeout exception. We map
  that to ``BROKEN`` (infra/test-setup problem) rather than ``FAILED``.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Playwright result.status → TestLookup status.
_STATUS_MAP = {
    "passed": "PASSED",
    "expected": "PASSED",     # top-level test status alias
    "failed": "FAILED",
    "unexpected": "FAILED",   # top-level test status alias
    "timedout": "BROKEN",
    "timed_out": "BROKEN",
    "interrupted": "BROKEN",
    "skipped": "SKIPPED",
    "flaky": "PASSED",        # overall flaky run ends PASSED after retry
}


def parse_playwright_json(content: str, test_run_id: str) -> List[dict]:
    """Parse Playwright ``--reporter=json`` output into normalized cases.

    Args:
        content: Raw JSON string emitted by the Playwright JSON reporter.
        test_run_id: The TestRun UUID to stamp on each normalized row.

    Returns:
        List of dicts matching the TestLookup ingestion contract. Empty
        list on malformed input — never raises. One entry per (spec,
        projectName) pair.
    """
    import json
    try:
        payload = json.loads(content)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse Playwright JSON — invalid JSON: %s", exc)
        return []

    if not isinstance(payload, dict):
        logger.warning("Playwright JSON root must be an object, got %s", type(payload).__name__)
        return []

    top_suites = payload.get("suites") or []
    if not isinstance(top_suites, list):
        logger.warning("Playwright JSON 'suites' is not a list — skipping")
        return []

    normalized: List[dict] = []
    for suite in _walk_suites(top_suites, parents=()):
        suite_title_path = suite["parents"] + (suite["title"],)
        suite_file = suite.get("file") or ""
        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            spec_title = str(spec.get("title") or "")
            spec_file = str(spec.get("file") or suite_file)
            spec_line = spec.get("line")
            suite_name = _build_suite_name(spec_file, suite_title_path)
            for test in spec.get("tests") or []:
                case = _normalize_test(
                    test,
                    spec_title=spec_title,
                    spec_file=spec_file,
                    spec_line=spec_line,
                    suite_name=suite_name,
                    test_run_id=test_run_id,
                )
                if case is not None:
                    normalized.append(case)
    return normalized


def _walk_suites(suites: list, parents: tuple[str, ...]) -> Iterable[dict]:
    """Depth-first flatten. Yields every suite (not just leaves) so
    top-level specs are visited alongside nested ones."""
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        title = str(suite.get("title") or suite.get("file") or "")
        file_ = str(suite.get("file") or "")
        yield {
            "title": title,
            "file": file_,
            "parents": parents,
            "specs": suite.get("specs") or [],
        }
        child = suite.get("suites")
        if isinstance(child, list) and child:
            yield from _walk_suites(child, parents + (title,))


def _build_suite_name(spec_file: str, title_path: tuple[str, ...]) -> str:
    joined = " > ".join(t for t in title_path if t)
    if spec_file and joined:
        return f"{spec_file} :: {joined}"
    return spec_file or joined or "Unknown"


def _normalize_test(
    test: Any,
    *,
    spec_title: str,
    spec_file: str,
    spec_line: Any,
    suite_name: str,
    test_run_id: str,
) -> Optional[dict]:
    if not isinstance(test, dict):
        return None
    project_name = str(test.get("projectName") or "").strip()
    raw_status = str(test.get("status") or "").lower().replace("-", "_")
    results = test.get("results") or []
    if not isinstance(results, list):
        results = []

    # Pick the final attempt — that's what determines the end-state status.
    final_result = results[-1] if results else None
    attempt_count = len(results)
    retry_count = max(0, attempt_count - 1)

    # Resolve the status. Prefer the per-result status (more granular —
    # distinguishes ``timedOut`` from plain ``failed``) and fall back to
    # the top-level test status when results are absent.
    if final_result and isinstance(final_result, dict):
        result_status = str(final_result.get("status") or "").lower().replace("-", "_")
    else:
        result_status = raw_status

    status = _STATUS_MAP.get(result_status) or _STATUS_MAP.get(raw_status) or "BROKEN"
    is_flaky = raw_status == "flaky" or (status == "PASSED" and retry_count > 0 and result_status != "passed")

    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    if isinstance(final_result, dict):
        d = final_result.get("duration")
        if isinstance(d, (int, float)):
            duration_ms = int(d)

        # Prefer ``errors`` (list) over ``error`` (single) — newer Playwright
        # emits the array and may contain multiple assertion failures.
        errors = final_result.get("errors")
        err = None
        if isinstance(errors, list) and errors:
            err = errors[0]
        elif isinstance(final_result.get("error"), dict):
            err = final_result.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if msg:
                error_message = str(msg)[:2000]
            stack = err.get("stack") or err.get("snippet")
            if stack:
                stack_trace = str(stack)[:10000]

    test_name = spec_title
    if project_name:
        test_name = f"{spec_title} [{project_name}]"

    full_name = f"{spec_file}:{spec_line}" if spec_file and spec_line else spec_title

    return {
        "test_run_id": test_run_id,
        "test_name": test_name,
        "full_name": full_name,
        "suite_name": suite_name,
        "class_name": spec_file or None,
        "package_name": None,
        "status": status,
        "duration_ms": duration_ms,
        "error_message": error_message,
        "stack_trace": stack_trace,
        "retry_count": retry_count,
        "is_flaky": is_flaky,
        "tags": [project_name] if project_name else [],
        "attachments": [],
        "steps": [],
        "framework": "playwright",
    }
