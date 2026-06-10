"""pytest-json-report (`pytest --json-report`) parser (MRU-14).

pytest's JUnit XML already ingests via the JUnit/TestNG parser; this adds the
richer ``pytest-json-report`` plugin output, a single JSON document:

.. code-block:: json

    {
      "exitcode": 1, "root": "/repo", "summary": {"passed": 1, "failed": 1, "total": 2},
      "tests": [
        {
          "nodeid": "tests/test_x.py::TestC::test_m[p]",
          "outcome": "passed|failed|error|skipped|xfailed|xpassed",
          "setup":    {"duration": 0.01, "outcome": "passed"},
          "call":     {"duration": 1.20, "outcome": "failed", "longrepr": "...traceback..."},
          "teardown": {"duration": 0.00, "outcome": "passed"}
        }
      ]
    }

One normalized case per test. ``nodeid`` → file (suite) + class + name; outcome
→ status; duration = setup+call+teardown (s → ms); the failing phase's
``longrepr`` → error_message. Never raises — returns [] on malformed input.
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# pytest outcome → TestLookup status.
_OUTCOME_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "error": "BROKEN",     # setup/teardown error — infra/test-setup, not a product bug
    "skipped": "SKIPPED",
    "xfailed": "SKIPPED",  # expected failure — not a real failure
    "xpassed": "PASSED",   # unexpectedly passed
}


def parse_pytest_json(content: str, test_run_id: str) -> List[dict]:
    """Parse ``pytest --json-report`` output into normalized cases."""
    import json
    try:
        payload = json.loads(content)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse pytest JSON — invalid JSON: %s", exc)
        return []
    if not isinstance(payload, dict):
        return []
    tests = payload.get("tests")
    if not isinstance(tests, list):
        return []
    out: List[dict] = []
    for t in tests:
        case = _normalize(t, test_run_id)
        if case is not None:
            out.append(case)
    return out


def _normalize(t: object, test_run_id: str) -> Optional[dict]:
    if not isinstance(t, dict):
        return None
    nodeid = str(t.get("nodeid") or "").strip()
    if not nodeid:
        return None

    status = _OUTCOME_MAP.get(str(t.get("outcome") or "").lower(), "UNKNOWN")
    suite_name, class_name, test_name = _split_nodeid(nodeid)

    # Duration = sum of phase durations (pytest reports seconds → ms).
    total = 0.0
    have = False
    for phase in ("setup", "call", "teardown"):
        p = t.get(phase)
        if isinstance(p, dict) and isinstance(p.get("duration"), (int, float)):
            total += float(p["duration"])
            have = True
    duration_ms = int(total * 1000) if have else None

    # Error text: the failing phase's longrepr/crash (call first, then fixtures).
    error_message = None
    for phase in ("call", "setup", "teardown"):
        p = t.get(phase)
        if isinstance(p, dict) and p.get("outcome") in ("failed", "error"):
            lr = p.get("longrepr") or p.get("crash")
            if lr:
                error_message = str(lr)[:2000]
                break

    return {
        "test_run_id": test_run_id,
        "test_name": test_name,
        "full_name": nodeid,
        "suite_name": suite_name,
        "class_name": class_name,
        "package_name": None,
        "status": status,
        "duration_ms": duration_ms,
        "error_message": error_message,
        "stack_trace": error_message,
        "tags": [],
        "attachments": [],
        # Synthesize one pseudo-step per pytest phase (setup/call/teardown) so
        # the granular snapshot mirrors the framework's own execution phases.
        "steps": _phase_steps(t),
        "framework": "pytest",
    }


def _phase_steps(t: dict) -> List[dict]:
    """Build setup/call/teardown pseudo-steps in the common step dict shape.

    pytest has no nested user steps in ``--json-report``; the three execution
    phases are the natural granularity. Each phase carries its own outcome,
    duration (s → ms) and ``longrepr``/``crash`` trace. Phases that pytest did
    not report (missing key) are skipped; a reported phase with no duration
    still emits a step so the UI shows the phase ran.
    """
    out: List[dict] = []
    for phase in ("setup", "call", "teardown"):
        p = t.get(phase)
        if not isinstance(p, dict):
            continue
        dur = p.get("duration")
        duration_ms = int(float(dur) * 1000) if isinstance(dur, (int, float)) else None
        outcome = str(p.get("outcome") or "").lower()
        trace = None
        if outcome in ("failed", "error"):
            lr = p.get("longrepr") or p.get("crash")
            if lr:
                trace = str(lr)[:8000]
        out.append({
            "name": phase,
            "keyword": phase,
            "status": _OUTCOME_MAP.get(outcome, "UNKNOWN"),
            "start_ms": None,
            "duration_ms": duration_ms,
            "assertion_message": (str(trace)[:2000] if trace else None),
            "assertion_trace": trace,
            "expected": None,
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": [],
        })
    return out


def _split_nodeid(nodeid: str):
    """Split a pytest nodeid into (suite_name, class_name, test_name).

    ``tests/test_x.py::TestClass::test_m[p]`` →
      suite='tests/test_x.py', class='tests/test_x.py::TestClass', name='test_m[p]'.
    ``tests/test_x.py::test_func`` →
      suite='tests/test_x.py', class='tests/test_x.py', name='test_func'.

    The FILE is folded into ``class_name`` (not just ``suite_name``) because the
    dedup fingerprint is ``sha256(class_name::test_name)`` — without the file,
    same-named tests in different files (``test_smoke`` everywhere) would collide
    and overwrite each other. This matches the playwright/cypress parsers, which
    set ``class_name = file``. ``suite_name`` stays the bare file for grouping.

    A trailing parametrize suffix ``[...]`` is split off first so a ``::`` INSIDE
    a param id (e.g. ``test_m[a::b]``) doesn't corrupt the split.
    """
    param = ""
    base = nodeid
    lb = nodeid.find("[")
    if lb != -1 and nodeid.endswith("]"):
        base, param = nodeid[:lb], nodeid[lb:]

    parts = base.split("::")
    file_path = parts[0] if parts else base
    if len(parts) >= 3:
        class_name: Optional[str] = "::".join(parts[:-1])   # file + class path
        test_name = parts[-1] + param
    elif len(parts) == 2:
        class_name = file_path                               # function-level: file as class
        test_name = parts[1] + param
    else:
        class_name = None
        test_name = (base + param) or nodeid
    return (file_path or None), class_name, (test_name or nodeid)
