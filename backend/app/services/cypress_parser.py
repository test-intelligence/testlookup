"""
Cypress Mochawesome JSON report parser — Tier 1 item 1.

Cypress's default reporter is Mochawesome (``mochawesome-merge`` output) which
produces a single JSON file with the following rough shape:

.. code-block:: json

    {
      "stats": {
        "tests": 42,
        "passes": 38,
        "failures": 3,
        "pending": 1,
        "skipped": 0,
        "duration": 18923
      },
      "results": [
        {
          "file": "cypress/e2e/login.cy.ts",
          "fullFile": "/.../cypress/e2e/login.cy.ts",
          "suites": [
            {
              "title": "Login flow",
              "tests": [
                {
                  "title": "redirects to dashboard on success",
                  "fullTitle": "Login flow redirects to dashboard on success",
                  "duration": 824,
                  "state": "passed|failed|pending|skipped",
                  "err": {"message": "...", "estack": "..."},
                  "pass": true,
                  "fail": false,
                  "pending": false,
                  "skipped": false
                }
              ],
              "suites": [ ...nested... ]
            }
          ]
        }
      ]
    }

Key nuances:

* Suites nest arbitrarily deep. We flatten them and build a ``suite_name``
  from the ``file`` + concatenated parent suite titles.
* Cypress uses ``state`` (passed/failed/pending/skipped); we normalize to the
  TestLookup vocabulary (PASSED/FAILED/SKIPPED/BROKEN). ``pending`` maps to
  ``SKIPPED`` per Mocha convention.
* Stack traces come back in ``err.estack`` when available.
* Duration is already in milliseconds.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Cypress → TestLookup status mapping. Lowercase inputs; emit uppercase.
_STATUS_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "pending": "SKIPPED",
    "skipped": "SKIPPED",
}


def parse_cypress_json(content: str, test_run_id: str) -> List[dict]:
    """Parse a Cypress Mochawesome JSON report into normalized test cases.

    Args:
        content: Raw JSON string (usually the output of ``mochawesome-merge``
            for multi-spec runs, or a single ``mochawesome.json`` for a
            single spec).
        test_run_id: The TestRun UUID to stamp on each normalized row.

    Returns:
        List of dicts matching the TestLookup ingestion contract — the same
        shape produced by ``allure_parser`` and ``testng_parser`` so the
        downstream ``_upsert_test_case`` pipeline is unchanged.

        Malformed input returns an empty list rather than raising — ingestion
        tasks are fire-and-forget; we log and move on so one bad upload can't
        wedge the worker.
    """
    import json
    try:
        payload = json.loads(content)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse Cypress JSON — invalid JSON: %s", exc)
        return []

    if not isinstance(payload, dict):
        logger.warning("Cypress JSON root must be an object, got %s", type(payload).__name__)
        return []

    top_results = payload.get("results") or []
    if not isinstance(top_results, list):
        logger.warning("Cypress JSON 'results' is not a list — skipping")
        return []

    normalized: List[dict] = []
    for result in top_results:
        if not isinstance(result, dict):
            continue
        spec_file = str(result.get("file") or result.get("fullFile") or "")
        for suite in _iter_suites(result.get("suites") or [], parents=()):
            suite_title_path = suite["parents"] + (suite["title"],)
            suite_name = _build_suite_name(spec_file, suite_title_path)
            for test in suite.get("tests") or []:
                case = _normalize_test(
                    test,
                    test_run_id=test_run_id,
                    suite_name=suite_name,
                    spec_file=spec_file,
                )
                if case is not None:
                    normalized.append(case)

        # Some reports place ``tests`` at the top-level of a file entry with
        # no wrapping suite. Handle that shape too.
        top_tests = result.get("tests")
        if isinstance(top_tests, list) and top_tests:
            suite_name = _build_suite_name(spec_file, ())
            for test in top_tests:
                case = _normalize_test(
                    test,
                    test_run_id=test_run_id,
                    suite_name=suite_name,
                    spec_file=spec_file,
                )
                if case is not None:
                    normalized.append(case)

    return normalized


def _iter_suites(suites: list, parents: tuple[str, ...]) -> Iterable[dict]:
    """Depth-first flatten of Cypress suites, preserving parent title chain."""
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        title = str(suite.get("title") or "")
        yield {"title": title, "parents": parents, "tests": suite.get("tests") or []}
        child = suite.get("suites")
        if isinstance(child, list) and child:
            yield from _iter_suites(child, parents + (title,))


def _build_suite_name(spec_file: str, title_path: tuple[str, ...]) -> str:
    """Combine spec filename with the suite title chain.

    Example: ``cypress/e2e/login.cy.ts :: Login flow > Error handling``
    """
    joined_titles = " > ".join(t for t in title_path if t)
    if spec_file and joined_titles:
        return f"{spec_file} :: {joined_titles}"
    return spec_file or joined_titles or "Unknown"


def _normalize_test(
    test: Any,
    *,
    test_run_id: str,
    suite_name: str,
    spec_file: str,
) -> Optional[dict]:
    if not isinstance(test, dict):
        return None
    title = str(test.get("title") or "").strip()
    if not title:
        return None

    state = str(test.get("state") or "").lower()
    # Mocha flags ``pending=true`` for skipped/pending tests that don't carry
    # a state string; catch that fallback before defaulting to broken.
    if not state:
        if test.get("pending"):
            state = "pending"
        elif test.get("pass"):
            state = "passed"
        elif test.get("fail"):
            state = "failed"

    status = _STATUS_MAP.get(state)
    if status is None:
        # Unknown state — treat as BROKEN so it surfaces for triage rather
        # than being silently lost.
        logger.debug("Unknown Cypress test state '%s' for '%s' — defaulting to BROKEN", state, title)
        status = "BROKEN"

    duration_ms = test.get("duration")
    if not isinstance(duration_ms, (int, float)):
        duration_ms = None
    else:
        duration_ms = int(duration_ms)

    err = test.get("err") or {}
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    if isinstance(err, dict):
        raw_message = err.get("message")
        if raw_message:
            error_message = str(raw_message)[:2000]
        raw_estack = err.get("estack") or err.get("stack")
        if raw_estack:
            stack_trace = str(raw_estack)[:10000]

    full_title = str(test.get("fullTitle") or title)

    return {
        "test_run_id": test_run_id,
        "test_name": title,
        "full_name": full_title,
        "suite_name": suite_name,
        "class_name": spec_file or None,
        "package_name": None,
        "status": status,
        "duration_ms": duration_ms,
        "error_message": error_message,
        "stack_trace": stack_trace,
        "attachments": [],
        "steps": [],
        "framework": "cypress",
    }
