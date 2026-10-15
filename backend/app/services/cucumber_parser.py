"""Cucumber JSON report parser (``cucumber --format json`` and compatibles).

The Cucumber JSON format is emitted near-identically by cucumber-jvm,
cucumber-js, behave (``--format json``) and SpecFlow: the root is an ARRAY of
Feature objects, each with ``elements`` (scenarios):

.. code-block:: json

    [
      {
        "keyword": "Feature",
        "name": "Login",
        "uri": "features/login.feature",
        "elements": [
          {
            "keyword": "Scenario",
            "type": "scenario",
            "name": "Valid login",
            "id": "login;valid-login",
            "tags": [{"name": "@smoke"}],
            "steps": [
              {
                "keyword": "Given ",
                "name": "I am on the login page",
                "match": {"location": "steps/login.py:12"},
                "result": {"status": "passed", "duration": 1234567890}
              },
              {
                "keyword": "Then ",
                "name": "I see the dashboard",
                "result": {
                  "status": "failed",
                  "duration": 500000000,
                  "error_message": "AssertionError: dashboard not visible"
                }
              }
            ]
          }
        ]
      }
    ]

Key nuances:

* ``duration`` is **nanoseconds** (cucumber-jvm/js). Converted to ms here.
* A scenario has no status of its own — it is DERIVED from its steps:
  any ``failed`` → FAILED; else any ``undefined``/``ambiguous`` → BROKEN
  (a missing/ambiguous step definition is an automation bug, not a product
  failure — same FAILED-vs-BROKEN vocabulary the TestNG parser preserves);
  else any ``pending``/``skipped`` → SKIPPED (reason recorded); else PASSED.
* ``type: "background"`` elements carry no verdict of their own — their steps
  are folded into the NEXT scenario element, matching Gherkin execution
  semantics (a failing Background fails the scenario it precedes).
* Scenario Outline examples arrive as separate scenario elements sharing a
  name; ``id`` (which includes the example-row suffix) disambiguates
  ``full_name``.
* Feature → suite (``uri`` kept as ``class_name``); Gherkin keyword (Given/
  When/Then) → common-step ``keyword``.

Timing (EXEC-01):

* ``duration_ms`` is what the framework reports for a scenario: its steps PLUS
  its hooks — the scenario ``before`` / ``after`` hooks and each step's
  ``before`` / ``after`` (BeforeStep / AfterStep) hooks. Until EXEC-01 only
  the steps were summed, so a hook-heavy scenario under-reported; a report
  without hooks keeps exactly the old figure.
* When a scenario lists ``before`` / ``after`` hooks: ``setup_ms`` = its before
  hooks, ``teardown_ms`` = its after hooks, ``call_ms`` = steps + step hooks.
* ``start_timestamp`` (cucumber-jvm 6+) → ``started_at``; ``finished_at`` =
  start + duration (computed, so ``timing_source='derived'``).
* The format states no feature or run timing.

Malformed input returns an empty list rather than raising — ingestion tasks
are fire-and-forget; one bad upload must not wedge the worker.
"""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

from app.services.parser_timing import set_case_timing
from app.services.report_timings import (
    TIMING_SOURCE_DERIVED,
    TIMING_SOURCE_REPORT,
    ReportTimings,
    empty_timings,
    finish_from_duration,
    to_utc,
)

logger = logging.getLogger(__name__)

_MAX_STEPS = 100
_MAX_TRACE_CHARS = 8000
_MAX_MESSAGE_CHARS = 2000

# Cucumber step-result → TestLookup status (strict PASSED/FAILED/SKIPPED/
# BROKEN/UNKNOWN set — the granular ``test_steps.status`` column is fronted by
# a strict enum that silently 422s reads on drift).
_STEP_STATUS_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "skipped": "SKIPPED",
    "pending": "SKIPPED",
    "undefined": "BROKEN",
    "ambiguous": "BROKEN",
}


def _ns_to_ms(duration: Any) -> Optional[int]:
    if not isinstance(duration, (int, float)):
        return None
    return int(duration / 1_000_000)


def _step_dicts(steps: list) -> List[dict]:
    """Map Cucumber steps to the common cross-framework step shape."""
    out: List[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if len(out) >= _MAX_STEPS:
            break
        result = step.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        raw_status = str(result.get("status") or "").lower()
        status = _STEP_STATUS_MAP.get(raw_status, "UNKNOWN")

        message = None
        trace = None
        raw_error = result.get("error_message")
        if raw_error:
            text = str(raw_error)
            # Cucumber packs message + stack into one field; first line is the
            # human-readable part, the remainder the trace.
            first_line, _, rest = text.partition("\n")
            message = first_line.strip()[:_MAX_MESSAGE_CHARS] or None
            trace = text[:_MAX_TRACE_CHARS]
        elif raw_status == "undefined":
            message = "Step undefined — no matching step definition"
        elif raw_status == "ambiguous":
            message = "Step ambiguous — multiple matching step definitions"
        elif raw_status == "pending":
            message = "Step pending — marked as not yet implemented"

        keyword = str(step.get("keyword") or "").strip() or None
        out.append({
            "name": str(step.get("name") or keyword or "step"),
            "keyword": keyword,
            "status": status,
            "start_ms": None,
            "duration_ms": _ns_to_ms(result.get("duration")),
            "assertion_message": message,
            "assertion_trace": trace,
            "expected": None,
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": [],
        })
    return out


def _hooks_ms(hooks: Any) -> Optional[int]:
    """Summed duration (ns → ms) of a ``before`` / ``after`` hook list; None when it lists no timed hook."""
    if not isinstance(hooks, list):
        return None
    total: Optional[int] = None
    for hook in hooks:
        if not isinstance(hook, dict) or not isinstance(hook.get("result"), dict):
            continue
        ms = _ns_to_ms(hook["result"].get("duration"))
        if ms is not None and ms >= 0:
            total = (total or 0) + ms
    return total


def _plus(a: Optional[int], b: Optional[int]) -> Optional[int]:
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def _derive_status(step_statuses: List[str], raw_statuses: List[str]) -> str:
    """Scenario verdict from its steps (see module docstring for the rules)."""
    if not step_statuses:
        return "UNKNOWN"
    if "FAILED" in step_statuses:
        return "FAILED"
    if "BROKEN" in step_statuses:
        return "BROKEN"
    if not raw_statuses:
        # Steps present but none carried a result object — no verdict basis.
        return "UNKNOWN"
    if any(s in ("pending", "skipped") for s in raw_statuses):
        return "SKIPPED"
    if all(s == "passed" for s in raw_statuses):
        return "PASSED"
    return "UNKNOWN"


def _scenario_error(steps: list) -> tuple[Optional[str], Optional[str]]:
    """(error_message, stack_trace) from the first non-passing step."""
    for step in steps:
        if not isinstance(step, dict):
            continue
        result = step.get("result") or {}
        if not isinstance(result, dict):
            continue
        raw_status = str(result.get("status") or "").lower()
        if raw_status in ("failed", "undefined", "ambiguous", "pending"):
            step_label = f"{str(step.get('keyword') or '').strip()} {step.get('name') or ''}".strip()
            raw_error = result.get("error_message")
            if raw_error:
                text = str(raw_error)
                first_line = text.partition("\n")[0].strip()
                message = f"{step_label}: {first_line}"[:_MAX_MESSAGE_CHARS]
                return message, text[:_MAX_TRACE_CHARS]
            reason = {
                "undefined": "step undefined — no matching step definition",
                "ambiguous": "step ambiguous — multiple matching step definitions",
                "pending": "step pending — marked as not yet implemented",
            }.get(raw_status, raw_status)
            return f"{step_label}: {reason}"[:_MAX_MESSAGE_CHARS], None
    return None, None


def _collect_tags(element: dict) -> List[str]:
    tags = element.get("tags")
    if not isinstance(tags, list):
        return []
    out = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            out.append(str(tag["name"]).lstrip("@"))
    return out


def parse_cucumber_json(content: str, test_run_id: str) -> List[dict]:
    """Parse a Cucumber JSON report into normalized test case dicts.

    Returns the same shape produced by ``cypress_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed input
    logs and returns ``[]``.
    """
    return _parse(content, test_run_id)[0]


def parse_cucumber_json_with_timings(content: str, test_run_id: str) -> Tuple[List[dict], ReportTimings]:
    """The cases of :func:`parse_cucumber_json`; the format states no run or feature timing (always empty)."""
    return _parse(content, test_run_id)


def _parse(content: str, test_run_id: str) -> Tuple[List[dict], ReportTimings]:
    try:
        payload = json.loads(content)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse Cucumber JSON — invalid JSON: %s", exc)
        return [], empty_timings()

    if not isinstance(payload, list):
        logger.warning("Cucumber JSON root must be an array, got %s", type(payload).__name__)
        return [], empty_timings()

    normalized: List[dict] = []
    for feature in payload:
        if not isinstance(feature, dict):
            continue
        feature_name = str(feature.get("name") or "Unknown Feature")
        uri = str(feature.get("uri") or "") or None
        elements = feature.get("elements")
        if not isinstance(elements, list):
            continue

        # Background steps fold into the NEXT scenario (Gherkin semantics: the
        # Background runs before each scenario; a failing Background fails the
        # scenario the report attaches it to).
        pending_background_steps: list = []
        feature_tags = _collect_tags(feature)

        for element in elements:
            if not isinstance(element, dict):
                continue
            el_type = str(element.get("type") or "scenario").lower()
            el_steps = element.get("steps")
            el_steps = el_steps if isinstance(el_steps, list) else []

            if el_type == "background":
                pending_background_steps = el_steps
                continue

            all_steps = pending_background_steps + el_steps
            pending_background_steps = []

            name = str(element.get("name") or "").strip() or "Unnamed scenario"
            raw_statuses = [
                str((s.get("result") or {}).get("status") or "").lower()
                for s in all_steps
                if isinstance(s, dict) and isinstance(s.get("result"), dict)
            ]
            common_steps = _step_dicts(all_steps)
            status = _derive_status([s["status"] for s in common_steps], raw_statuses)
            error_message, stack_trace = _scenario_error(all_steps)

            step_durations = [s["duration_ms"] for s in common_steps if s["duration_ms"] is not None]
            call_ms: Optional[int] = sum(step_durations) if step_durations else None
            for step in all_steps:
                if isinstance(step, dict):
                    call_ms = _plus(call_ms, _plus(_hooks_ms(step.get("before")), _hooks_ms(step.get("after"))))
            setup_ms = _hooks_ms(element.get("before"))
            teardown_ms = _hooks_ms(element.get("after"))
            # The framework's scenario duration: steps + step hooks + scenario hooks.
            duration_ms = _plus(_plus(setup_ms, call_ms), teardown_ms)

            # Scenario Outline example rows share a name; the id carries the
            # ;example-row suffix that disambiguates full_name.
            element_id = str(element.get("id") or "").strip()
            full_name = f"{feature_name}.{element_id or name}"

            case = {
                "test_run_id": test_run_id,
                "test_name": name,
                "full_name": full_name,
                "suite_name": feature_name,
                "class_name": uri,
                "package_name": None,
                "status": status,
                "duration_ms": duration_ms,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "attachments": [],
                "tags": feature_tags + _collect_tags(element),
                "steps": common_steps,
                "framework": "cucumber",
            }
            started = to_utc(element.get("start_timestamp"))
            has_hooks = setup_ms is not None or teardown_ms is not None
            set_case_timing(
                case,
                started_at=started,
                finished_at=finish_from_duration(started, duration_ms),
                setup_ms=(setup_ms or 0) if has_hooks else None,
                call_ms=(call_ms or 0) if has_hooks else None,
                teardown_ms=(teardown_ms or 0) if has_hooks else None,
                source=TIMING_SOURCE_DERIVED if started is not None else TIMING_SOURCE_REPORT,
            )
            normalized.append(case)

    return normalized, empty_timings()
