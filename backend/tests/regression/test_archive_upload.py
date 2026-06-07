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
