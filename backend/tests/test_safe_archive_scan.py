"""W24 ADM-05 — ``safe_archive.scan_zip``: the streaming scan an import runs.

``scan_zip`` must refuse every crafted archive with the SAME ``UnsafeZipError``
code ``safe_extract_zip`` raises (one set of rules, two shapes), keep no member
bytes (a digest and a size per member), and leave ``safe_extract_zip`` itself
untouched (its own guard is ``tests/regression/test_allure_zip_spike.py``).
"""
from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
import tracemalloc
import zipfile
from dataclasses import fields
from typing import Callable

import pytest

from app.services import safe_archive
from app.services.safe_archive import (
    ScannedMember,
    UnsafeZipError,
    safe_extract_zip,
    scan_zip,
)

_DEFAULTS = {
    "max_total": safe_archive.DEFAULT_MAX_TOTAL_UNCOMPRESSED,
    "max_entries": safe_archive.DEFAULT_MAX_ENTRIES,
    "max_entry": safe_archive.DEFAULT_MAX_ENTRY_UNCOMPRESSED,
    "max_ratio": safe_archive.DEFAULT_MAX_RATIO,
}


def _zip(entries: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _traversal() -> bytes:
    return _zip({"../x": b"x"})


def _traversal_nested() -> bytes:
    return _zip({"entities/../../x": b"x"})


def _absolute() -> bytes:
    return _zip({"/etc/passwd": b"x"})


def _drive_letter() -> bytes:
    return _zip({"C:/evil.txt": b"x"})


def _symlink() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("link")
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, b"/etc/passwd")
    return buf.getvalue()


def _nested_by_name() -> bytes:
    return _zip({"inner.zip": _zip({"a.json": b"{}"})})


def _nested_by_magic() -> bytes:
    return _zip({"entities/x/part-0001.jsonl": _zip({"a.json": b"{}"})})


def _ratio_bomb() -> bytes:
    return _zip({"bomb.json": b"\x00" * (1024 * 1024)})


def _entry_over_cap() -> bytes:
    return _zip({"big.json": b"A" * 5000})


def _total_over_cap() -> bytes:
    return _zip({f"f{i}.json": b"B" * 4000 for i in range(3)})


def _too_many_entries() -> bytes:
    return _zip({f"f{i}.json": b"{}" for i in range(5)})


def _not_a_zip() -> bytes:
    return b"not a zip at all"


# (builder, caps overriding the defaults, the code BOTH functions must raise)
_CRAFTED: list[tuple[Callable[[], bytes], dict[str, int], str]] = [
    (_traversal, {}, "unsafe_path"),
    (_traversal_nested, {}, "unsafe_path"),
    (_absolute, {}, "unsafe_path"),
    (_drive_letter, {}, "unsafe_path"),
    (_symlink, {}, "unsafe_path"),
    (_nested_by_name, {}, "nested_zip"),
    (_nested_by_magic, {}, "nested_zip"),
    (_ratio_bomb, {"max_entry": 10 * 1024 * 1024, "max_ratio": 10}, "zip_bomb"),
    (_entry_over_cap, {"max_entry": 1000, "max_ratio": 10_000}, "zip_bomb"),
    (_total_over_cap, {"max_total": 5000, "max_entry": 10_000, "max_ratio": 100_000}, "zip_too_large"),
    (_too_many_entries, {"max_entries": 3}, "too_many_entries"),
    (_not_a_zip, {}, "bad_zip"),
]


@pytest.mark.parametrize(
    ("builder", "caps", "code"),
    _CRAFTED,
    ids=[b.__name__.lstrip("_") for b, _c, _k in _CRAFTED],
)
def test_scan_raises_the_same_code_as_safe_extract(builder, caps, code):
    raw = builder()
    with pytest.raises(UnsafeZipError) as extracted:
        safe_extract_zip(raw, **caps)
    with pytest.raises(UnsafeZipError) as scanned:
        scan_zip(io.BytesIO(raw), **{**_DEFAULTS, **caps})
    assert extracted.value.code == code
    assert scanned.value.code == extracted.value.code


def test_scan_reports_name_size_and_digest_and_skips_directories():
    part = b'{"id": "a"}\n{"id": "b"}\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("entities/", b"")  # a directory entry: counted, never returned
        zf.writestr("entities/test_suites/part-0001.jsonl", part)
        zf.writestr("manifest.json", b"{}")
    buf.seek(0)

    members = scan_zip(buf, **_DEFAULTS)

    assert members == [
        ScannedMember("entities/test_suites/part-0001.jsonl", len(part), hashlib.sha256(part).hexdigest()),
        ScannedMember("manifest.json", 2, hashlib.sha256(b"{}").hexdigest()),
    ]
    # The file object stays usable: the caller opens the archive again afterwards.
    with zipfile.ZipFile(buf) as zf:
        assert zf.read("manifest.json") == b"{}"


def test_a_scanned_member_holds_no_bytes():
    assert [f.name for f in fields(ScannedMember)] == ["name", "size", "sha256"]
    members = scan_zip(io.BytesIO(_zip({"a.jsonl": b"payload-sentinel"})), **_DEFAULTS)
    assert all(not isinstance(getattr(m, f.name), (bytes, bytearray)) for m in members for f in fields(m))


def test_scan_streams_without_holding_the_archive_in_memory(tmp_path):
    # 3 x 4 MiB of incompressible bytes = 12 MiB uncompressed, read from a file on
    # disk. safe_extract_zip would hold all 12 MiB; the scan holds one read chunk.
    path = tmp_path / "big.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for i in range(3):
            zf.writestr(f"entities/t/part-000{i + 1}.jsonl", os.urandom(4 * 1024 * 1024))
    with open(path, "rb") as fh:
        tracemalloc.start()
        try:
            members = scan_zip(fh, **{**_DEFAULTS, "max_ratio": 10})
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert [m.size for m in members] == [4 * 1024 * 1024] * 3
    assert peak < 4 * 1024 * 1024, f"peak {peak} bytes: the scan kept member bytes"


def test_scan_messages_never_carry_an_entry_name():
    crafted_name = "../SENTINELmemberNameXyzzy"
    with pytest.raises(UnsafeZipError) as ei:
        scan_zip(io.BytesIO(_zip({crafted_name: b"x"})), **_DEFAULTS)
    assert ei.value.code == "unsafe_path"
    assert "SENTINEL" not in ei.value.message and "SENTINEL" not in str(ei.value)


def test_scan_refuses_a_corrupt_member_as_bad_zip():
    raw = bytearray(_zip({"a.jsonl": b"row-sentinel-" * 2000}))
    # Flip bytes inside the compressed stream (after the 30-byte local header + name).
    start = 30 + len("a.jsonl") + 10
    for i in range(start, start + 16):
        raw[i] ^= 0xFF
    with pytest.raises(UnsafeZipError) as ei:
        scan_zip(io.BytesIO(bytes(raw)), **_DEFAULTS)
    assert ei.value.code == "bad_zip"


def test_scan_refuses_an_encrypted_member_as_bad_zip():
    # zipfile cannot write an encrypted member: set the "encrypted" flag bit in the
    # local header (offset 6) and the central directory record (offset 8) by hand.
    raw = bytearray(_zip({"a.jsonl": b"x"}, compression=zipfile.ZIP_STORED))
    raw[6] |= 0x1
    central = raw.index(b"PK\x01\x02")
    raw[central + 8] |= 0x1
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as check:
        assert check.infolist()[0].flag_bits & 0x1  # the crafted flag is what a reader sees
    with pytest.raises(UnsafeZipError) as ei:
        scan_zip(io.BytesIO(bytes(raw)), **_DEFAULTS)
    assert ei.value.code == "bad_zip"


def test_scan_reads_a_spooled_temporary_file():
    raw = _zip({"manifest.json": b'{"format": "x"}'})
    with tempfile.SpooledTemporaryFile(max_size=16) as spool:
        spool.write(raw)
        spool.seek(0)
        members = scan_zip(spool, **_DEFAULTS)
    assert [m.name for m in members] == ["manifest.json"]
