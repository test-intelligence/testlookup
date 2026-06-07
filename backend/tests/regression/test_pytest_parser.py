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
    # File folded into class_name so the (class::name) fingerprint is unique per file.
    assert by["test_pass"]["class_name"] == "tests/test_a.py::TestC"
    assert by["test_pass"]["suite_name"] == "tests/test_a.py"
    assert by["test_pass"]["duration_ms"] == 510  # (0.01 + 0.5 + 0.0) * 1000

    assert by["test_fail"]["status"] == "FAILED"
    assert by["test_fail"]["class_name"] == "tests/test_a.py"  # function-level → file
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


def test_same_test_name_in_different_files_does_not_collide():
    """Same leaf name in two files must yield distinct fingerprints (the file is
    folded into class_name) — otherwise one row silently overwrites the other."""
    from app.services.ingestion import make_test_fingerprint

    doc = json.dumps({
        "exitcode": 0, "root": "/r", "summary": {"total": 2},
        "tests": [
            {"nodeid": "tests/test_a.py::test_smoke", "outcome": "passed"},
            {"nodeid": "tests/test_b.py::test_smoke", "outcome": "passed"},
        ],
    })
    rows = parse_pytest_json(doc, "run-1")
    fps = {make_test_fingerprint(r["test_name"], r["class_name"]) for r in rows}
    assert len(fps) == 2  # distinct → both survive the per-(run,fingerprint) upsert


def test_parametrized_nodeid_with_double_colon_in_param():
    """A '::' inside a parametrize id must not corrupt the class/name split."""
    doc = json.dumps({
        "exitcode": 0, "root": "/r", "summary": {"total": 1},
        "tests": [{"nodeid": "tests/test_p.py::TestK::test_q[a::b]", "outcome": "passed"}],
    })
    row = parse_pytest_json(doc, "run-1")[0]
    assert row["class_name"] == "tests/test_p.py::TestK"
    assert row["test_name"] == "test_q[a::b]"


def test_detect_format_classifies_pytest_json():
    assert _detect_format("report.json", _SAMPLE.encode()) == "pytest"


def test_detect_format_pytest_with_large_environment_before_summary():
    """Real CI reports put a big 'environment' block before 'summary', pushing
    'summary' past the 4 KB sniff window — detection must NOT require it."""
    big_env = {f"pkg_{i}": f"1.2.{i}" for i in range(400)}  # > 4 KB
    doc = json.dumps({
        "exitcode": 1, "root": "/repo", "environment": {"Packages": big_env},
        "summary": {"total": 1},
        "tests": [{"nodeid": "tests/t.py::test_x", "outcome": "failed"}],
    }).encode()
    assert doc.index(b'"summary"') > 4096  # summary really is out of the window
    assert _detect_format("report.json", doc) == "pytest"
