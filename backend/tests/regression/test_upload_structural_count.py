"""Re-audit N21: the pre-parse cap bounds a crafted Cypress/Playwright/Cucumber/Allure report.

M5 counted one marker key per result before parsing. For these four formats
the marker key is optional: a result without it is still parsed. So a report
that left it out carried any number of results past the pre-parse check, and
only the exact check after parsing -- after the memory was spent -- refused it.

They are now counted structurally: every object directly inside an array under
the key the parser reads results from, at any depth (Allure: every object in
the root array, or the root object). The parsers take results from nowhere
else, so the count is at least what parsing yields for any valid JSON.

Checked on the repo's REAL sample fixtures (the M5 bound: parsed <= counted <=
4 x parsed), and on crafted reports that drop every marker key.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services import upload_limits
from app.services.upload_limits import TooManyResults
from tests.regression.test_upload_result_cap import SAMPLES, _ALLURE

pytestmark = pytest.mark.regression

CAP = 5
STRUCTURAL = ("cypress", "playwright", "cucumber", "allure")
_REAL = [s for s in SAMPLES if s[0] in STRUCTURAL] + [("allure", _ALLURE, "results.json")]
_PARSERS = {
    "cypress": "app.services.cypress_parser.parse_cypress_json",
    "playwright": "app.services.playwright_parser.parse_playwright_json",
    "cucumber": "app.services.cucumber_parser.parse_cucumber_json",
    "allure": "app.services.allure_parser.parse_allure_result",
}


def _parsed(fmt: str, report: str, filename: str = "report.json") -> int:
    from app.worker.tasks import _parse_file_to_results

    return len(_parse_file_to_results(report, fmt, filename, "run-1"))


def _marker_count(report: str, fmt: str) -> int:
    """What M5's marker count saw."""
    return len(upload_limits._RESULT_MARKERS[fmt].findall(report))


# Reports that carry each format's results WITHOUT its M5 marker key.
def _cypress(n: int) -> str:
    tests = [{"title": f"t{i}", "state": "passed", "duration": 1} for i in range(n)]
    return json.dumps({"results": [{"file": "a.cy.js", "suites": [
        {"title": "outer", "tests": [], "suites": [{"title": "inner", "tests": tests}]},
    ]}]})


def _playwright(n: int) -> str:
    tests = [{"projectName": "chromium", "status": "expected"}]
    specs = [{"title": f"t{i}", "file": "a.spec.ts", "tests": tests} for i in range(n)]
    return json.dumps({"suites": [{"title": "a.spec.ts", "file": "a.spec.ts", "specs": [],
                                   "suites": [{"title": "nested", "specs": specs}]}]})


def _cucumber(n: int) -> str:
    elements = [{"type": "scenario", "name": f"s{i}", "id": f"f;s{i}"} for i in range(n)]
    return json.dumps([{"name": "F", "uri": "f.feature", "elements": elements}])


def _allure(n: int) -> str:
    return json.dumps([{"name": f"t{i}", "fullName": f"C.t{i}", "status": "passed"} for i in range(n)])


CRAFTED = {"cypress": _cypress, "playwright": _playwright, "cucumber": _cucumber, "allure": _allure}


@pytest.mark.parametrize("fmt,fixture,filename", _REAL, ids=[f"{f}-{n}" for f, _, n in _REAL])
def test_the_real_samples_stay_inside_the_m5_bound(fmt, fixture, filename):
    parsed = _parsed(fmt, fixture, filename)
    counted = upload_limits.estimated_results(fixture, fmt)
    assert parsed > 0
    assert parsed <= counted <= parsed * 4, f"{fmt}: {counted} counted for {parsed} parsed"


def test_every_structural_format_has_a_real_sample():
    assert {fmt for fmt, _f, _n in _REAL} == set(STRUCTURAL) == set(upload_limits._CONTAINER_KEYS)


@pytest.mark.parametrize("fmt", STRUCTURAL)
def test_a_report_without_its_marker_keys_is_still_counted(fmt):
    report = CRAFTED[fmt](12)
    parsed = _parsed(fmt, report)
    assert parsed == 12, f"{fmt}: the crafted report parses to {parsed}, not 12"
    # The gap this closes: M5's marker count saw nothing at all.
    assert _marker_count(report, fmt) == 0
    assert upload_limits.estimated_results(report, fmt) >= parsed


@pytest.mark.parametrize("fmt", STRUCTURAL)
def test_a_crafted_report_far_over_the_cap_is_refused_unparsed(fmt, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", CAP)
    from app.worker.tasks import _parse_file_to_results

    with patch(_PARSERS[fmt], side_effect=AssertionError("the report was parsed")):
        with pytest.raises(TooManyResults):
            _parse_file_to_results(CRAFTED[fmt](21), fmt, "report.json", "run-1")


@pytest.mark.parametrize("fmt", ["cypress", "playwright", "cucumber"])
def test_an_escaped_container_key_is_still_the_key(fmt):
    """json.loads decodes ``"te\\u0073ts"`` to ``tests``; so must the count."""
    key = "elements" if fmt == "cucumber" else "tests"
    report = CRAFTED[fmt](9)
    escaped = report.replace(f'"{key}"', '"' + key[:2] + chr(92) + "u" + format(ord(key[2]), "04x") + key[3:] + '"')
    assert escaped != report
    assert _parsed(fmt, escaped) == 9
    assert upload_limits.estimated_results(escaped, fmt) >= 9


def test_a_decoy_inside_a_string_is_not_counted():
    decoy = 'x\\" , "tests": [{}, {}, {}] '
    report = json.dumps({"results": [{"file": "a", "suites": [], "note": decoy}]})
    assert _parsed("cypress", report) == 0
    assert upload_limits.estimated_results(report, "cypress") == 0


def test_an_allure_root_object_counts_one():
    report = json.dumps({"name": "t", "fullName": "C.t", "status": "passed"})
    assert _parsed("allure", report) == 1
    assert upload_limits.estimated_results(report, "allure") == 1


def test_the_count_stops_once_past_the_threshold():
    """A huge crafted report is refused after scanning a threshold's worth."""
    report = _cypress(50_000)
    assert upload_limits.structural_results(report, "cypress", stop_after=20) == 21
