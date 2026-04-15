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
