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


# Playwright result.status → TestLookup status vocab (PASSED/FAILED/SKIPPED/
# BROKEN/UNKNOWN). The granular ``test_steps.status`` column is fronted by the
# strict ``TestStatus`` enum, so a value outside this vocab silently 422s the
# read endpoint — every emitted (case- and step-level) status MUST land here.
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

# Hard caps on the granular step tree. Customer-supplied reporter JSON is
# untrusted input at the upload/webhook boundary; Playwright's ``results[].steps``
# nests arbitrarily (each ``expect``/fixture/hook is a node with sub-steps).
# Mirrors allure_parser's caps so a pathological tree cannot (a) blow Python's
# recursion limit mid-ingest, nor (b) materialise an unbounded number of
# ``test_steps`` rows. The shared ``ingestion._insert_step`` re-applies the same
# caps defensively; this keeps the PARSER output bounded at the source too.
_MAX_STEP_DEPTH = 20
_MAX_STEP_NODES = 2000


def _map_step_status(raw: Optional[str], *, has_error: bool = False) -> str:
    """Map a Playwright step/result outcome to the TestLookup status vocab.

    Steps in the JSON reporter carry no explicit ``status`` — a step is failed
    iff it has an ``error``. When ``has_error`` is set and no explicit status is
    given we resolve to FAILED; otherwise fall back to the status map (PASSED
    default for an error-free step)."""
    s = str(raw or "").lower().replace("-", "_")
    if s:
        mapped = _STATUS_MAP.get(s)
        if mapped:
            return mapped
    if has_error:
        return "FAILED"
    return "PASSED" if not s else "UNKNOWN"


def _parse_pw_error(err: Any) -> dict:
    """Normalise one Playwright error object into the common assertion fields.

    Playwright errors carry ``message``, ``stack`` and (for assertion failures)
    a ``snippet`` of the offending source. We surface ``snippet`` as ``expected``
    context when present so the UI's expected/actual column isn't empty.
    """
    if not isinstance(err, dict):
        return {}
    msg = err.get("message")
    stack = err.get("stack")
    snippet = err.get("snippet")
    return {
        "assertion_message": (str(msg)[:2000] if msg else None),
        "assertion_trace": (str(stack)[:10000] if stack else None),
        "expected": (str(snippet)[:2000] if snippet else None),
    }


def _result_errors(result: dict) -> list:
    """Return ALL error objects for a result attempt, preferring the ``errors``
    array (newer Playwright, may hold multiple assertion failures) and falling
    back to the single ``error`` object."""
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return [e for e in errors if isinstance(e, dict)]
    single = result.get("error")
    if isinstance(single, dict):
        return [single]
    return []


def _parse_pw_steps(
    raw_steps: Any,
    depth: int = 0,
    counter: Optional[dict] = None,
) -> List[dict]:
    """Recursively normalise Playwright ``results[].steps`` into the common
    step dict shape.

    Each native step node carries ``title``, ``category`` (e.g. ``test.step`` /
    ``expect`` / ``hook`` / ``pw:api``), ``duration`` (ms), optional ``error``
    and nested ``steps``. A step is FAILED iff it has an ``error`` (the reporter
    does not emit a per-step status field), so we derive status from error
    presence. Bounded by ``_MAX_STEP_DEPTH`` / shared ``_MAX_STEP_NODES`` so a
    hostile tree cannot recurse/expand without bound — mirrors allure_parser.
    """
    out: List[dict] = []
    if not isinstance(raw_steps, list):
        return out
    if depth > _MAX_STEP_DEPTH:
        return out  # truncate subtrees deeper than the cap
    if counter is None:
        counter = {"nodes": 0}
    for node in raw_steps:
        if not isinstance(node, dict):
            continue
        if counter["nodes"] >= _MAX_STEP_NODES:
            break  # global node budget exhausted — drop the remainder
        counter["nodes"] += 1
        err = node.get("error")
        err_fields = _parse_pw_error(err)
        dur = node.get("duration")
        out.append({
            "name": str(node.get("title") or "step"),
            "keyword": (str(node["category"]) if node.get("category") else None),
            "status": _map_step_status(None, has_error=bool(err_fields)),
            "start_ms": None,
            "duration_ms": int(dur) if isinstance(dur, (int, float)) else None,
            "assertion_message": err_fields.get("assertion_message"),
            "assertion_trace": err_fields.get("assertion_trace"),
            "expected": err_fields.get("expected"),
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": _parse_pw_steps(node.get("steps") or [], depth + 1, counter),
        })
    return out


def _result_step_tree(final_result: Optional[dict]) -> List[dict]:
    """Build the common step tree for the final attempt: the native nested
    ``steps`` plus a synthetic top-level step per ``errors[]`` entry that the
    step tree didn't already surface, so EVERY assertion failure is captured
    (not just the first)."""
    if not isinstance(final_result, dict):
        return []
    counter = {"nodes": 0}
    steps = _parse_pw_steps(final_result.get("steps") or [], 0, counter)

    # Keep ALL result-level errors as top-level assertion entries. The native
    # step tree often carries only the first failure (or none, when the failure
    # is a hook/timeout outside test.step), so append a synthetic step per error
    # that the tree didn't represent. Dedup on assertion_message to avoid
    # double-listing an error that the step tree already attached.
    seen_msgs = set()

    def _collect(nodes: list) -> None:
        for n in nodes:
            if n.get("assertion_message"):
                seen_msgs.add(n["assertion_message"])
            _collect(n.get("steps") or [])

    _collect(steps)

    for err in _result_errors(final_result):
        if counter["nodes"] >= _MAX_STEP_NODES:
            break
        fields = _parse_pw_error(err)
        msg = fields.get("assertion_message")
        if msg and msg in seen_msgs:
            continue
        counter["nodes"] += 1
        if msg:
            seen_msgs.add(msg)
        steps.append({
            "name": "error",
            "keyword": "error",
            "status": "FAILED",
            "start_ms": None,
            "duration_ms": None,
            "assertion_message": fields.get("assertion_message"),
            "assertion_trace": fields.get("assertion_trace"),
            "expected": fields.get("expected"),
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": [],
        })
    return steps


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

    # Flaky == the test had BOTH a failed and a passed attempt (Playwright's own
    # ``flaky`` top-level status means exactly this: failed then passed on retry).
    # Derive structurally from the per-attempt statuses so we still catch flakes
    # when the reporter omits the top-level ``flaky`` label.
    attempt_statuses = [
        _STATUS_MAP.get(str(r.get("status") or "").lower().replace("-", "_"))
        for r in results
        if isinstance(r, dict)
    ]
    had_pass = any(s == "PASSED" for s in attempt_statuses)
    had_fail = any(s in ("FAILED", "BROKEN") for s in attempt_statuses)
    is_flaky = raw_status == "flaky" or (had_pass and had_fail)

    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    if isinstance(final_result, dict):
        d = final_result.get("duration")
        if isinstance(d, (int, float)):
            duration_ms = int(d)

        # Case-level error_message/stack_trace come from the FIRST error of the
        # final attempt (the headline failure). ALL errors are preserved in the
        # granular step tree below — stack_trace was previously DROPPED when the
        # error carried only ``snippet``; now it is persisted via assertion_trace
        # on the step entries and the headline error here.
        first_errs = _result_errors(final_result)
        if first_errs:
            head = _parse_pw_error(first_errs[0])
            error_message = head.get("assertion_message")
            stack_trace = head.get("assertion_trace") or head.get("expected")

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
        # Native nested step tree of the final attempt in the common step dict
        # shape (+ a synthetic top-level step per remaining errors[] entry).
        "steps": _result_step_tree(final_result if isinstance(final_result, dict) else None),
        "framework": "playwright",
    }
