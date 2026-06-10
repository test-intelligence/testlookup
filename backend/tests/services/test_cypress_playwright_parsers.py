"""
Unit tests for Tier 1 item 1 parsers — Cypress Mochawesome JSON and
Playwright ``--reporter=json`` output. Covers happy path, malformed
input, status mapping, and the nested-suite flattener.
"""
from __future__ import annotations

import json
import uuid

from app.services.cypress_parser import parse_cypress_json
from app.services.playwright_parser import parse_playwright_json


# ── Cypress ────────────────────────────────────────────────────────────────


def test_cypress_parser_handles_nested_suites_and_status_mapping():
    content = json.dumps({
        "stats": {"tests": 4, "passes": 2, "failures": 1, "pending": 1},
        "results": [
            {
                "file": "cypress/e2e/login.cy.ts",
                "suites": [
                    {
                        "title": "Login flow",
                        "tests": [
                            {
                                "title": "redirects to dashboard",
                                "fullTitle": "Login flow redirects to dashboard",
                                "state": "passed",
                                "pass": True,
                                "duration": 824,
                            },
                            {
                                "title": "shows error on bad password",
                                "fullTitle": "Login flow shows error on bad password",
                                "state": "failed",
                                "fail": True,
                                "duration": 912,
                                "err": {
                                    "message": "Expected '[data-testid=error]' to exist",
                                    "estack": "AssertionError: at cypress/e2e/login.cy.ts:42",
                                },
                            },
                        ],
                        "suites": [
                            {
                                "title": "Error handling",
                                "tests": [
                                    {
                                        "title": "locks out after 5 attempts",
                                        "state": "pending",
                                        "pending": True,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    })

    run_id = str(uuid.uuid4())
    results = parse_cypress_json(content, run_id)

    assert len(results) == 3

    by_name = {r["test_name"]: r for r in results}

    passed = by_name["redirects to dashboard"]
    assert passed["status"] == "PASSED"
    assert passed["duration_ms"] == 824
    assert passed["suite_name"] == "cypress/e2e/login.cy.ts :: Login flow"
    assert passed["class_name"] == "cypress/e2e/login.cy.ts"
    assert passed["framework"] == "cypress"

    failed = by_name["shows error on bad password"]
    assert failed["status"] == "FAILED"
    assert "Expected" in failed["error_message"]
    assert "AssertionError" in failed["stack_trace"]

    pending = by_name["locks out after 5 attempts"]
    assert pending["status"] == "SKIPPED"
    # Nested suite titles chain through the parent.
    assert "Login flow > Error handling" in pending["suite_name"]


def test_cypress_parser_handles_top_level_tests_without_wrapping_suite():
    content = json.dumps({
        "stats": {"tests": 1, "passes": 1, "failures": 0},
        "results": [
            {
                "file": "smoke.cy.ts",
                "tests": [
                    {"title": "smoke passes", "state": "passed", "pass": True, "duration": 10}
                ],
            }
        ],
    })

    results = parse_cypress_json(content, str(uuid.uuid4()))
    assert len(results) == 1
    assert results[0]["test_name"] == "smoke passes"
    assert results[0]["suite_name"] == "smoke.cy.ts"


def test_cypress_parser_rejects_malformed_json():
    assert parse_cypress_json("not json", "run-1") == []
    assert parse_cypress_json('["array at root"]', "run-1") == []
    assert parse_cypress_json('{"results": "not-a-list"}', "run-1") == []


def test_cypress_parser_emits_assertion_step_with_structured_expected_actual():
    """Cypress is the only in-scope framework with a structured diff
    (err.expected / err.actual). The emitted common-shape assertion step must
    populate the dedicated expected/actual fields plus message + trace."""
    content = json.dumps({
        "stats": {},
        "results": [
            {
                "file": "cypress/e2e/cart.cy.ts",
                "suites": [
                    {
                        "title": "Cart",
                        "tests": [
                            {
                                "title": "totals match",
                                "state": "failed",
                                "fail": True,
                                "duration": 130,
                                "err": {
                                    "message": "expected 42 to equal 41",
                                    "estack": "AssertionError: expected 42 to equal 41\n    at cart.cy.ts:9",
                                    "expected": 41,
                                    "actual": 42,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    })

    results = parse_cypress_json(content, str(uuid.uuid4()))
    assert len(results) == 1
    case = results[0]

    # Case-level stack trace is persisted (previously dropped).
    assert "AssertionError" in case["stack_trace"]

    steps = case["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["name"] == "assertion"
    assert step["status"] == "FAILED"
    assert step["assertion_message"] == "expected 42 to equal 41"
    assert "AssertionError" in step["assertion_trace"]
    # The unique structured-diff fields are populated (stringified).
    assert step["expected"] == "41"
    assert step["actual"] == "42"
    assert step["duration_ms"] == 130
    # Common-shape contract keys are all present.
    for key in ("keyword", "start_ms", "parameters", "attachments", "steps"):
        assert key in step


def test_cypress_parser_passing_test_emits_single_passed_assertion_step():
    content = json.dumps({
        "stats": {},
        "results": [
            {
                "file": "smoke.cy.ts",
                "tests": [
                    {"title": "smoke passes", "state": "passed", "pass": True, "duration": 10}
                ],
            }
        ],
    })
    results = parse_cypress_json(content, "run-1")
    assert len(results) == 1
    steps = results[0]["steps"]
    assert len(steps) == 1
    assert steps[0]["status"] == "PASSED"
    assert steps[0]["expected"] is None
    assert steps[0]["actual"] is None
    assert steps[0]["assertion_message"] is None


def test_cypress_parser_unknown_state_becomes_broken():
    content = json.dumps({
        "stats": {},
        "results": [
            {
                "file": "x.cy.ts",
                "suites": [
                    {
                        "title": "s",
                        "tests": [{"title": "weird", "state": "cosmic-ray"}],
                    }
                ],
            }
        ],
    })
    results = parse_cypress_json(content, "run-1")
    assert len(results) == 1
    assert results[0]["status"] == "BROKEN"


# ── Playwright ─────────────────────────────────────────────────────────────


def test_playwright_parser_emits_one_case_per_project():
    content = json.dumps({
        "config": {"projects": [{"name": "chromium"}, {"name": "webkit"}]},
        "suites": [
            {
                "title": "tests/login.spec.ts",
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
                                "status": "expected",
                                "results": [
                                    {"status": "passed", "duration": 820, "retry": 0}
                                ],
                            },
                            {
                                "projectName": "webkit",
                                "expectedStatus": "passed",
                                "status": "unexpected",
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 1100,
                                        "retry": 0,
                                        "errors": [
                                            {
                                                "message": "locator.click: Timeout 5000ms exceeded",
                                                "stack": "TimeoutError: at login.spec.ts:14",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    })

    results = parse_playwright_json(content, str(uuid.uuid4()))
    assert len(results) == 2

    by_name = {r["test_name"]: r for r in results}
    chromium = by_name["redirects to dashboard [chromium]"]
    webkit = by_name["redirects to dashboard [webkit]"]

    assert chromium["status"] == "PASSED"
    assert chromium["duration_ms"] == 820
    assert chromium["framework"] == "playwright"
    assert "chromium" in (chromium["tags"] or [])

    assert webkit["status"] == "FAILED"
    assert "Timeout 5000ms" in webkit["error_message"]
    assert "TimeoutError" in webkit["stack_trace"]


def test_playwright_parser_marks_flaky_as_passed_with_is_flaky_true():
    content = json.dumps({
        "config": {"projects": [{"name": "chromium"}]},
        "suites": [
            {
                "title": "tests/flaky.spec.ts",
                "file": "tests/flaky.spec.ts",
                "specs": [
                    {
                        "title": "sometimes passes",
                        "file": "tests/flaky.spec.ts",
                        "line": 3,
                        "tests": [
                            {
                                "projectName": "chromium",
                                "status": "flaky",
                                "results": [
                                    {"status": "failed", "duration": 500, "retry": 0},
                                    {"status": "passed", "duration": 300, "retry": 1},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    })

    results = parse_playwright_json(content, "run-1")
    assert len(results) == 1
    case = results[0]
    assert case["status"] == "PASSED"
    assert case["is_flaky"] is True
    assert case["retry_count"] == 1


def test_playwright_parser_timeout_maps_to_broken():
    content = json.dumps({
        "config": {},
        "suites": [
            {
                "title": "slow.spec.ts",
                "specs": [
                    {
                        "title": "slow test",
                        "tests": [
                            {
                                "projectName": "chromium",
                                "status": "unexpected",
                                "results": [{"status": "timedOut", "duration": 30000}],
                            }
                        ],
                    }
                ],
            }
        ],
    })
    results = parse_playwright_json(content, "run-1")
    assert len(results) == 1
    assert results[0]["status"] == "BROKEN"


def test_playwright_parser_rejects_malformed_json():
    assert parse_playwright_json("not json", "run-1") == []
    assert parse_playwright_json('"scalar"', "run-1") == []
    assert parse_playwright_json('{"suites": "not-a-list"}', "run-1") == []


# ── Playwright granular step dict (Phase 3) ──────────────────────────────────


def _common_step_keys():
    return {
        "name", "keyword", "status", "start_ms", "duration_ms",
        "assertion_message", "assertion_trace", "expected", "actual",
        "parameters", "attachments", "steps",
    }


def _collect_assertion_messages(steps):
    out = []
    for s in steps:
        if s.get("assertion_message"):
            out.append(s["assertion_message"])
        out.extend(_collect_assertion_messages(s.get("steps") or []))
    return out


def test_playwright_parser_emits_nested_common_step_dict():
    """Native results[].steps[] map recursively to the common step dict:
    title->name, category->keyword, duration->duration_ms, error.message->
    assertion_message, error.stack->assertion_trace, error.snippet->expected.
    A step is FAILED iff it carries an error; otherwise PASSED."""
    content = json.dumps({
        "config": {},
        "suites": [
            {
                "title": "tests/checkout.spec.ts",
                "file": "tests/checkout.spec.ts",
                "specs": [
                    {
                        "title": "completes checkout",
                        "file": "tests/checkout.spec.ts",
                        "line": 7,
                        "tests": [
                            {
                                "projectName": "chromium",
                                "status": "unexpected",
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 2100,
                                        "retry": 0,
                                        "steps": [
                                            {
                                                "title": "navigate to cart",
                                                "category": "test.step",
                                                "duration": 300,
                                                "steps": [
                                                    {
                                                        "title": "expect cart visible",
                                                        "category": "expect",
                                                        "duration": 120,
                                                        "error": {
                                                            "message": "expect(locator).toBeVisible failed",
                                                            "stack": "Error: at checkout.spec.ts:11",
                                                            "snippet": "  9 | await expect(cart)",
                                                        },
                                                    }
                                                ],
                                            }
                                        ],
                                        "errors": [
                                            {
                                                "message": "expect(locator).toBeVisible failed",
                                                "stack": "Error: at checkout.spec.ts:11",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    })

    results = parse_playwright_json(content, str(uuid.uuid4()))
    assert len(results) == 1
    case = results[0]

    # Case-level: stack_trace is persisted (was previously dropped).
    assert case["status"] == "FAILED"
    assert "toBeVisible failed" in case["error_message"]
    assert "checkout.spec.ts:11" in case["stack_trace"]

    steps = case["steps"]
    assert len(steps) == 1
    top = steps[0]
    # Every emitted step carries the full common step dict shape.
    assert _common_step_keys().issubset(set(top.keys()))
    assert top["name"] == "navigate to cart"
    assert top["keyword"] == "test.step"
    assert top["duration_ms"] == 300
    assert top["status"] == "PASSED"  # no error on this node

    child = top["steps"][0]
    assert child["name"] == "expect cart visible"
    assert child["keyword"] == "expect"
    assert child["status"] == "FAILED"
    assert "toBeVisible failed" in child["assertion_message"]
    assert "checkout.spec.ts:11" in child["assertion_trace"]
    # error.snippet surfaces as expected-context.
    assert "await expect(cart)" in child["expected"]


def test_playwright_parser_keeps_all_errors_not_just_first():
    """ALL results[].errors[] are surfaced. Errors not represented in the step
    tree become synthetic top-level step entries; the one already attached to a
    step is NOT duplicated."""
    content = json.dumps({
        "config": {},
        "suites": [
            {
                "title": "multi.spec.ts",
                "file": "multi.spec.ts",
                "specs": [
                    {
                        "title": "two soft assertions fail",
                        "file": "multi.spec.ts",
                        "tests": [
                            {
                                "projectName": "chromium",
                                "status": "unexpected",
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 400,
                                        "steps": [
                                            {
                                                "title": "check A",
                                                "category": "expect",
                                                "error": {"message": "soft assertion A failed"},
                                            }
                                        ],
                                        "errors": [
                                            {"message": "soft assertion A failed"},
                                            {"message": "soft assertion B failed",
                                             "stack": "Error: B at multi.spec.ts:20"},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    })

    results = parse_playwright_json(content, "run-1")
    case = results[0]
    messages = _collect_assertion_messages(case["steps"])
    # Both errors present; A only once (already on a step, not re-added).
    assert messages.count("soft assertion A failed") == 1
    assert "soft assertion B failed" in messages
    # The synthetic error step carries the second error's trace.
    b_step = next(
        s for s in case["steps"]
        if s.get("assertion_message") == "soft assertion B failed"
    )
    assert "multi.spec.ts:20" in b_step["assertion_trace"]


def test_playwright_parser_flaky_from_both_pass_and_fail_attempts():
    """is_flaky is true when a test has BOTH a failed and a passed attempt,
    even when the top-level status label is absent. retry_count = len-1."""
    content = json.dumps({
        "config": {},
        "suites": [
            {
                "title": "flaky2.spec.ts",
                "file": "flaky2.spec.ts",
                "specs": [
                    {
                        "title": "eventually passes",
                        "tests": [
                            {
                                "projectName": "chromium",
                                # NOTE: no top-level "flaky" label.
                                "status": "expected",
                                "results": [
                                    {"status": "failed", "duration": 50},
                                    {"status": "passed", "duration": 40},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    })
    case = parse_playwright_json(content, "run-1")[0]
    assert case["status"] == "PASSED"
    assert case["is_flaky"] is True
    assert case["retry_count"] == 1
