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
