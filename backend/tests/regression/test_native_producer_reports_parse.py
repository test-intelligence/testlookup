"""Real producer reports detect and parse to exactly the producer's tests.

``e2e-runner/fixtures/`` holds report files written by the real tools (pytest,
Robot, TestNG/Surefire, NUnit, VSTest TRX, xUnit.net, Cypress Mochawesome,
Playwright, Cucumber.js and allure-pytest; see ``native/README.md``). Each
producer ran one passing, one failing and one skipped test, and the Allure run
also raised outside an assertion to give a broken one. Until now only the
release harness read these files, so a parser or detector change that broke a
real report was caught at release time; the backend's parser tests use small
hand-written samples, and three past bugs came from real-file shapes those
samples missed.

For every report this checks the upload path's two steps, in-process:

* ``routers.ingest._detect_format`` picks the right format from the file name
  and the first 4 KB, as the "auto" upload does;
* ``worker.tasks._parse_report`` (the dispatch behind the upload task) returns
  exactly the producer's test names, one row each, with each status as it is
  persisted (upper-cased by ``ingestion_pipeline``) and an error message on
  the failing test.

The expected names and outcomes are the independent ones the release spec
uses (``frontend/tests/release/report-upload.spec.ts``). In a full checkout a
missing report fails this test; it skips only where the repository around
``backend/`` is absent (the backend-only container).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "e2e-runner" / "fixtures"
RUN_ID = "00000000-0000-0000-0000-00000000a003"

PYTHON = {
    "test_report_pass": "PASSED",
    "test_report_failure": "FAILED",
    "test_report_skipped": "SKIPPED",
}
LOWER = {"passes": "PASSED", "fails": "FAILED", "skips": "SKIPPED"}
UPPER = {"Passes": "PASSED", "Fails": "FAILED", "Skips": "SKIPPED"}
ROBOT = {
    "Producer observed pass": "PASSED",
    "Producer observed failure": "FAILED",
    "Producer observed skip": "SKIPPED",
}

#: report file (relative to e2e-runner/fixtures) -> (format, {test name: persisted status})
REPORTS = {
    "pytest-junit.xml": ("junit", PYTHON),
    "pytest-report.json": ("pytest", PYTHON),
    "robot.xml": ("robot", ROBOT),
    "native/testng.xml": ("testng", LOWER),
    "native/nunit.xml": ("nunit", UPPER),
    "native/report.trx": ("trx", UPPER),
    "native/xunit.xml": ("xunit", UPPER),
    "native/cypress.json": ("cypress", LOWER),
    "native/playwright.json": ("playwright", LOWER),
    "native/cucumber.json": ("cucumber", UPPER),
}

#: allure-pytest writes one result file per test; keyed by the test's name.
ALLURE = {
    "test_report_pass": "PASSED",
    "test_report_failure": "FAILED",
    "test_report_skipped": "SKIPPED",
    "test_report_broken": "BROKEN",
}


def _require_checkout() -> None:
    if not (REPO_ROOT / ".github").is_dir():
        pytest.skip(
            "not a full repository checkout (backend-only container): no e2e-runner/fixtures here"
        )


def _read(path: Path) -> tuple[bytes, str]:
    assert path.is_file(), f"native producer report missing from the checkout: {path}"
    raw = path.read_bytes()
    # As the upload task decodes a stored report.
    return raw, raw.decode("utf-8", errors="replace")


def _parse(content: str, fmt: str, filename: str) -> list[dict]:
    from app.worker.tasks import _parse_file_to_results, _parse_report

    results, _timings = _parse_report(content, fmt, filename, RUN_ID, with_timings=True)
    # The task's two entry points share one dispatch; the plain one must agree.
    assert _parse_file_to_results(content, fmt, filename, RUN_ID) == results
    return results


def _persisted(results: list[dict]) -> dict[str, str]:
    names = [r.get("test_name") for r in results]
    assert len(names) == len(set(names)), f"a test is parsed more than once: {names}"
    return {r["test_name"]: str(r.get("status") or "").upper() for r in results}


@pytest.mark.parametrize("relative", sorted(REPORTS))
def test_a_native_report_detects_and_parses_to_the_producers_tests(relative):
    from app.routers.ingest import _detect_format

    _require_checkout()
    expected_format, expected = REPORTS[relative]
    path = FIXTURES / relative
    raw, content = _read(path)

    detected = _detect_format(path.name, raw)
    assert detected == expected_format, (
        f"{relative}: auto-detect chose {detected!r}, not {expected_format!r}"
    )

    results = _parse(content, detected, path.name)
    assert _persisted(results) == expected, (
        f"{relative} parsed as {[(r.get('test_name'), r.get('status')) for r in results]}"
    )
    assert Counter(_persisted(results).values()) == Counter(
        {"PASSED": 1, "FAILED": 1, "SKIPPED": 1}
    )

    by_name = {r["test_name"]: r for r in results}
    failing = next(name for name, status in expected.items() if status == "FAILED")
    assert str(by_name[failing].get("error_message") or "").strip(), (
        f"{relative}: the failing test lost its message"
    )
    passing = next(name for name, status in expected.items() if status == "PASSED")
    assert not by_name[passing].get("error_message"), (
        f"{relative}: the passing test carries an error"
    )
    for result in results:
        duration = result.get("duration_ms")
        assert isinstance(duration, int) and duration >= 0, (
            f"{relative}: {result['test_name']} duration {duration!r}"
        )


def test_each_allure_result_record_detects_and_parses_to_its_test():
    from app.routers.ingest import _detect_format

    _require_checkout()
    records = sorted((FIXTURES / "native" / "allure-results").glob("*-result.json"))
    assert len(records) == len(ALLURE), (
        f"expected one allure-pytest record per test, found {records}"
    )

    seen: dict[str, str] = {}
    for path in records:
        raw, content = _read(path)
        assert _detect_format(path.name, raw) == "allure", (
            f"{path.name} not detected as allure"
        )
        results = _parse(content, "allure", path.name)
        assert len(results) == 1, f"{path.name}: {results}"
        seen.update(_persisted(results))
        if results[0]["test_name"] in ("test_report_failure", "test_report_broken"):
            assert str(results[0].get("error_message") or "").strip(), (
                f"{path.name} lost its message"
            )
        assert json.loads(raw)["name"] == results[0]["test_name"]

    assert seen == ALLURE
