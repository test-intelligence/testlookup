"""MRU-14: pytest-json-report parser + format detection."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")

from app.routers.ingest import _detect_format  # noqa: E402
from app.services.pytest_parser import parse_pytest_json  # noqa: E402

_SAMPLE = json.dumps({
    "exitcode": 1,
    "root": "/repo",
    "summary": {"passed": 1, "failed": 1, "error": 1, "skipped": 2, "total": 5},
    "tests": [
        {"nodeid": "tests/test_a.py::TestC::test_pass", "outcome": "passed",
         "setup": {"duration": 0.01, "outcome": "passed"},
         "call": {"duration": 0.5, "outcome": "passed"},
         "teardown": {"duration": 0.0, "outcome": "passed"}},
        {"nodeid": "tests/test_a.py::test_fail", "outcome": "failed",
         "call": {"duration": 0.2, "outcome": "failed", "longrepr": "assert 1 == 2"}},
        {"nodeid": "tests/test_b.py::test_err", "outcome": "error",
         "setup": {"duration": 0.0, "outcome": "error", "longrepr": "fixture boom"}},
        {"nodeid": "tests/test_b.py::test_skip", "outcome": "skipped"},
        {"nodeid": "tests/test_b.py::test_xfail", "outcome": "xfailed"},
    ],
})


def test_parse_pytest_json_statuses_nodeid_and_duration():
    rows = parse_pytest_json(_SAMPLE, "run-1")
    by = {r["test_name"]: r for r in rows}
    assert set(by) == {"test_pass", "test_fail", "test_err", "test_skip", "test_xfail"}

    assert by["test_pass"]["status"] == "PASSED"
    assert by["test_pass"]["class_name"] == "TestC"
    assert by["test_pass"]["suite_name"] == "tests/test_a.py"
    assert by["test_pass"]["duration_ms"] == 510  # (0.01 + 0.5 + 0.0) * 1000

    assert by["test_fail"]["status"] == "FAILED"
    assert by["test_fail"]["class_name"] is None        # function-level test
    assert "assert 1 == 2" in by["test_fail"]["error_message"]

    assert by["test_err"]["status"] == "BROKEN"          # setup error
    assert "fixture boom" in by["test_err"]["error_message"]
    assert by["test_skip"]["status"] == "SKIPPED"
    assert by["test_xfail"]["status"] == "SKIPPED"       # expected failure
    assert by["test_pass"]["framework"] == "pytest"


def test_parse_pytest_json_malformed_returns_empty():
    assert parse_pytest_json("not json", "r") == []
    assert parse_pytest_json("{}", "r") == []            # no 'tests' key
    assert parse_pytest_json("[]", "r") == []            # not an object


def test_detect_format_classifies_pytest_json():
    assert _detect_format("report.json", _SAMPLE.encode()) == "pytest"
