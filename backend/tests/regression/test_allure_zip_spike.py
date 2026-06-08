"""MRU-11/12 PoC: Allure-zip parsing + hardened safe_extract_zip security.

Proves the spike's two deliverables work end-to-end:
  1. parse_allure_zip parses a real-shaped Allure results zip — suite enriched
     from a container, retries collapsed to the latest attempt + flaky tagged.
  2. safe_extract_zip extracts a benign archive AND rejects every modelled
     attack: path traversal, symlink, nested zip, entry/total/ratio bombs,
     too-many-entries.
"""
from __future__ import annotations

import io
import json
import stat
import zipfile

import pytest

from app.services.allure_parser import parse_allure_zip
from app.services.safe_archive import UnsafeZipError, looks_like_zip, safe_extract_zip


def _zip(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _result(uuid, name, status, *, labels=None, history_id=None, stop=0):
    doc = {"uuid": uuid, "name": name, "status": status,
           "labels": labels or [], "start": 0, "stop": stop}
    if history_id:
        doc["historyId"] = history_id
    return json.dumps(doc).encode()


# ── parse_allure_zip ─────────────────────────────────────────────────────────


def test_parse_allure_zip_enriches_suite_and_collapses_retries():
    # T1 carries its own suite label; T2 has none (enriched from container c1);
    # T3 is a flaky retry pair (fail@100 then pass@200) under one historyId.
    files = {
        "allure-results/a1-result.json": _result("a1", "T1", "passed",
                                                  labels=[{"name": "suite", "value": "LabelSuite"}]),
        "allure-results/a2-result.json": _result("a2", "T2", "failed"),
        "allure-results/c1-container.json": json.dumps(
            {"uuid": "c1", "name": "ContainerSuite", "children": ["a2"]}).encode(),
        "allure-results/a3-result.json": _result("a3", "T3", "failed", history_id="h3", stop=100),
        "allure-results/a4-result.json": _result("a4", "T3", "passed", history_id="h3", stop=200),
    }
    extracted = safe_extract_zip(_zip(files))
    rows = parse_allure_zip(extracted, "run-1")

    by_name = {r["test_name"]: r for r in rows}
    assert set(by_name) == {"T1", "T2", "T3"}              # retry pair collapsed
    assert by_name["T1"]["suite_name"] == "LabelSuite"      # label kept
    assert by_name["T2"]["suite_name"] == "ContainerSuite"  # enriched from container
    assert by_name["T3"]["status"] == "passed"             # latest attempt won
    assert by_name["T3"]["is_flaky"] is True               # statuses disagreed
    assert by_name["T3"]["retry_count"] == 1


def test_parse_allure_zip_skips_corrupt_entries():
    files = {
        "x-result.json": _result("x", "Good", "passed"),
        "bad-result.json": b"{ not json",
        "notes.txt": b"ignored",
    }
    rows = parse_allure_zip(safe_extract_zip(_zip(files)), "run-1")
    assert [r["test_name"] for r in rows] == ["Good"]


# ── safe_extract_zip: benign ─────────────────────────────────────────────────


def test_safe_extract_happy_path_and_zip_detection():
    raw = _zip({"a-result.json": b'{"uuid":"a","name":"T","status":"passed"}'})
    assert looks_like_zip(raw)
    assert not looks_like_zip(b"<testsuite/>")
    out = safe_extract_zip(raw)
    assert out["a-result.json"].startswith(b'{')


# ── safe_extract_zip: attacks ────────────────────────────────────────────────


def test_rejects_path_traversal():
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip({"../evil.txt": b"x"}))
    assert ei.value.code == "unsafe_path"


def test_rejects_absolute_path():
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip({"/etc/passwd": b"x"}))
    assert ei.value.code == "unsafe_path"


def test_rejects_symlink_entry():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("link")
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16  # symlink mode
        zf.writestr(zi, b"/etc/passwd")
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(buf.getvalue())
    assert ei.value.code == "unsafe_path"


def test_rejects_nested_zip():
    inner = _zip({"a-result.json": b"{}"})
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip({"inner.zip": inner}))
    assert ei.value.code == "nested_zip"


def test_rejects_too_many_entries():
    files = {f"f{i}-result.json": b"{}" for i in range(5)}
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip(files), max_entries=3)
    assert ei.value.code == "too_many_entries"


def test_rejects_oversized_entry():
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip({"big.json": b"A" * 5000}), max_entry=1000, max_ratio=10_000)
    assert ei.value.code == "zip_bomb"


def test_rejects_total_uncompressed_cap():
    files = {f"f{i}.json": b"B" * 4000 for i in range(3)}
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip(files), max_total=5000, max_entry=10_000, max_ratio=100_000)
    assert ei.value.code == "zip_too_large"


def test_rejects_zip_bomb_by_ratio():
    # 1 MB of zeros compresses to ~1 KB → ratio far over the cap.
    bomb = b"\x00" * (1024 * 1024)
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(_zip({"bomb.json": bomb}), max_entry=10 * 1024 * 1024, max_ratio=10)
    assert ei.value.code == "zip_bomb"


def test_rejects_bad_zip():
    with pytest.raises(UnsafeZipError) as ei:
        safe_extract_zip(b"not a zip at all")
    assert ei.value.code == "bad_zip"
