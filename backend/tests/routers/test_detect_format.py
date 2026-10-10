"""
Unit tests for ``routers/ingest._detect_format``. The function is a
pure content sniffer — no HTTP roundtrip required.
"""
from __future__ import annotations

import json

from app.routers.ingest import _detect_format


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_detect_testng_by_root_tag():
    xml = b'<?xml version="1.0"?><testng-results total="5"></testng-results>'
    assert _detect_format("run.xml", xml) == "testng"


def test_detect_junit_by_testsuite_root():
    xml = b'<?xml version="1.0"?><testsuite name="x"></testsuite>'
    assert _detect_format("junit.xml", xml) == "junit"


def test_detect_playwright_by_config_projects_marker():
    payload = _json({
        "config": {"projects": [{"name": "chromium"}]},
        "suites": [{"title": "t", "specs": []}],
    })
    assert _detect_format("report.json", payload) == "playwright"


def test_detect_cypress_by_stats_passes_marker():
    payload = _json({
        "stats": {"tests": 1, "passes": 1, "failures": 0},
        "results": [{"file": "x.cy.ts", "suites": []}],
    })
    assert _detect_format("mochawesome.json", payload) == "cypress"


def test_detect_allure_by_uuid_and_status():
    payload = _json({"uuid": "abc", "name": "test", "status": "passed"})
    assert _detect_format("result.json", payload) == "allure"


def test_allure_extension_fallback_when_no_markers():
    # JSON with no recognizable fields still falls back to allure based on
    # the .json extension — preserves backwards compatibility.
    assert _detect_format("bare.json", b'{"foo": "bar"}') == "allure"


def test_junit_default_for_unknown():
    assert _detect_format("something.txt", b"no markers at all") == "junit"


def test_playwright_wins_over_cypress_when_both_markers_present():
    """Defensive: Playwright's ``config.projects`` check is more specific,
    so a report that happens to contain ``stats`` + ``passes`` as well
    still routes to Playwright."""
    payload = _json({
        "config": {"projects": [{"name": "chromium"}]},
        "suites": [],
        "stats": {"passes": 0},
        "results": [],
    })
    assert _detect_format("report.json", payload) == "playwright"


def test_a_real_playwright_report_whose_config_pushes_suites_past_2kb():
    """E2E 2026-10-10: ``suites`` follows the whole ``config`` block (argv,
    every project, metadata). This repo's own config put it at byte 2573, so
    the report fell through to the ``.json`` -> allure fallback and the
    upload failed as "No test results were found"."""
    config = {
        "configFile": "C:/repo/frontend/playwright.config.ts",
        "rootDir": "C:/repo/frontend/tests",
        "forbidOnly": False,
        "fullyParallel": True,
        "metadata": {"actualWorkers": 4},
        "projects": [
            {"outputDir": f"C:/repo/frontend/test-results/{n}", "name": n, "retries": 2,
             "testDir": "C:/repo/frontend/tests", "testIgnore": [], "testMatch": ["**/*.spec.ts"],
             "timeout": 30000, "metadata": {"browser": n, "padding": "x" * 600}}
            for n in ("chromium", "firefox", "webkit")
        ],
    }
    payload = _json({"config": config, "suites": [{"title": "t", "specs": []}], "stats": {}})
    assert payload.index(b'"suites"') > 2048
    assert _detect_format("results.json", payload) == "playwright"


def test_the_minimal_playwright_report_shipped_in_samples():
    """``samples/playwright/playwright-results.json`` has ``config.rootDir``
    and no ``projects``: it auto-detected as allure and parsed to nothing."""
    payload = _json({"config": {"rootDir": "/app", "testDir": "./tests/e2e"}, "suites": []})
    assert _detect_format("playwright-results.json", payload) == "playwright"


def test_a_config_key_that_is_not_first_is_not_playwright():
    """The new rule keys on ``config`` being the FIRST root key with a
    Playwright setting in it; Allure results stay Allure."""
    payload = _json({"uuid": "abc", "name": "t", "status": "passed", "config": {"rootDir": "/x"}})
    assert _detect_format("result.json", payload) == "allure"
