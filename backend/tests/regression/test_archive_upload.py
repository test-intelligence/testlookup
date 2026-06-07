"""MRU-12: archive (zip) upload wiring — _parse_archive_to_results dispatch.

Proves the worker's archive branch: base64 transport, tier-1 Allure-dir vs
tier-2 heterogeneous (N JUnit XML) dispatch, noise filtering, and that a
safety violation propagates as UnsafeZipError (→ structured failed status).
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from app.services.safe_archive import UnsafeZipError
from app.worker.tasks import _parse_archive_to_results


def _b64_zip(entries: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_archive_tier1_allure_results_dir():
    entries = {
        "allure-results/a1-result.json": json.dumps(
            {"uuid": "a1", "name": "T1", "status": "passed",
             "labels": [{"name": "suite", "value": "Smoke"}]}).encode(),
        "allure-results/a2-result.json": json.dumps(
            {"uuid": "a2", "name": "T2", "status": "failed", "labels": []}).encode(),
    }
    rows = _parse_archive_to_results(_b64_zip(entries), "allure.zip", "run-1")
    by_name = {r["test_name"]: r for r in rows}
    assert set(by_name) == {"T1", "T2"}
    assert by_name["T1"]["suite_name"] == "Smoke"


def test_archive_tier2_heterogeneous_junit():
    junit = b'<testsuite name="S"><testcase name="t1" classname="C"/>' \
            b'<testcase name="t2" classname="C"><failure>boom</failure></testcase></testsuite>'
    rows = _parse_archive_to_results(_b64_zip({"results.xml": junit}), "x.zip", "run-1")
    names = {r["test_name"] for r in rows}
    assert {"t1", "t2"} <= names


def test_archive_skips_macosx_and_dotfiles():
    entries = {
        "a1-result.json": json.dumps({"uuid": "a1", "name": "T1", "status": "passed"}).encode(),
        "__MACOSX/._a1-result.json": b"junk",
        ".DS_Store": b"junk",
    }
    rows = _parse_archive_to_results(_b64_zip(entries), "x.zip", "run-1")
    assert [r["test_name"] for r in rows] == ["T1"]


def test_archive_zip_bomb_propagates_unsafe_error():
    bomb = b"\x00" * (2 * 1024 * 1024)  # compresses tiny → ratio >> 100
    with pytest.raises(UnsafeZipError) as ei:
        _parse_archive_to_results(_b64_zip({"bomb.json": bomb}), "x.zip", "run-1")
    assert ei.value.code == "zip_bomb"


def test_archive_zip_bomb_propagates_through_dispatch():
    """UnsafeZipError must propagate up through _parse_file_to_results (where the
    task's _run catches it → failed status with the specific code)."""
    from app.worker.tasks import _parse_file_to_results

    bomb = b"\x00" * (2 * 1024 * 1024)
    with pytest.raises(UnsafeZipError):
        _parse_file_to_results(_b64_zip({"bomb.json": bomb}), "archive", "x.zip", "run-1")


def test_archive_noise_only_returns_empty():
    rows = _parse_archive_to_results(
        _b64_zip({"__MACOSX/._x": b"junk", ".DS_Store": b"junk"}), "x.zip", "run-1")
    assert rows == []


def test_archive_tier2_gate_skips_disabled_format():
    """A zipped Cypress report must NOT bypass the cypress_ingest flag: with
    'cypress' in disabled_formats the cypress entry is skipped while the allowed
    JUnit entry still parses."""
    from unittest.mock import patch

    # JSON with stats+passes+results → _detect_format classifies it cypress.
    cy = json.dumps({"stats": {"passes": 1, "failures": 0}, "results": []}).encode()
    junit = b'<testsuite name="S"><testcase name="jt" classname="C"/></testsuite>'
    b64 = _b64_zip({"mocha.json": cy, "results.xml": junit})

    with patch("app.services.cypress_parser.parse_cypress_json",
               return_value=[{"test_name": "cy1", "status": "PASSED", "class_name": None}]):
        allowed = _parse_archive_to_results(b64, "x.zip", "run-1")
        gated = _parse_archive_to_results(b64, "x.zip", "run-1", disabled_formats=["cypress"])

    allowed_names = {r["test_name"] for r in allowed}
    gated_names = {r["test_name"] for r in gated}
    assert {"cy1", "jt"} <= allowed_names      # both parse when allowed
    assert "jt" in gated_names                 # allowed entry still parses
    assert "cy1" not in gated_names            # gated entry skipped


def test_archive_gated_only_raises_disabled_message():
    """A zip whose only candidate is an admin-disabled format fails with a
    'disabled' message (matching the single-file 503), not 'unparseable'."""
    cy = json.dumps({"stats": {"passes": 1, "failures": 0}, "results": []}).encode()
    with pytest.raises(ValueError, match="disabled"):
        _parse_archive_to_results(
            _b64_zip({"mocha.json": cy}), "x.zip", "run-1", disabled_formats=["cypress"])


def test_archive_with_files_but_none_parsable_raises():
    """A zip that has candidate files but none parse → ValueError (→ parse_error
    status), distinct from a truly empty/noise-only zip (→ empty_report)."""
    with pytest.raises(ValueError):
        _parse_archive_to_results(_b64_zip({"junk.xml": b"not xml at all"}), "x.zip", "run-1")
