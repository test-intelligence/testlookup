"""Hardened in-memory ZIP extraction for uploaded report archives (MRU-11/12/10).

Real Allure uploads are a ZIP of a results directory. Untrusted ZIPs are an
attack surface — zip-bombs (huge decompression from tiny input), zip-slip
(``../`` path traversal), symlink entries, and nested archives. This module
extracts a ZIP to an in-memory ``{name: bytes}`` map with strict, enforced
limits and NEVER writes to disk.

Design (from the MRU-11 spike):
  * Stream each entry through ``ZipExtFile.read(chunk)`` with a running byte cap
    — never trust ``ZipInfo.file_size`` (the central-directory value can lie).
  * Reject absolute / drive / UNC / ``..`` paths and non-regular (symlink)
    entries before reading a single byte.
  * Reject nested archives (a ``.zip`` entry or one whose first bytes are the
    ZIP magic) — no recursive inflation.
  * Each violation raises ``UnsafeZipError(code, message)`` so the upload path
    can record a structured ``error.code`` in the status record.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import stat
import zipfile
import zlib
from dataclasses import dataclass
from typing import IO, Callable, Optional

# Defaults — the caller (worker) may override from settings. Compressed wire
# size is already capped upstream by _read_upload_bounded (50 MB); these bound
# the *decompressed* footprint.
DEFAULT_MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024   # 200 MB across the archive
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_MAX_ENTRY_UNCOMPRESSED = 50 * 1024 * 1024    # 50 MB per entry
DEFAULT_MAX_RATIO = 100                               # uncompressed/compressed
_READ_CHUNK = 1024 * 1024
_ZIP_MAGIC = b"PK\x03\x04"


class UnsafeZipError(Exception):
    """Raised when an archive violates a safety limit. ``code`` is a stable,
    user-facing slug (e.g. ``zip_bomb``, ``unsafe_path``)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def looks_like_zip(raw: bytes) -> bool:
    """True if the bytes start with any ZIP local/central/spanned signature."""
    return raw[:4] in (_ZIP_MAGIC, b"PK\x05\x06", b"PK\x07\x08")


def _is_unsafe_path(name: str) -> bool:
    """Reject absolute, drive-letter, UNC, and ``..`` traversal entry names."""
    if not name:
        return True
    if name.startswith("/") or name.startswith("\\"):
        return True
    if len(name) >= 2 and name[1] == ":":  # C:\ drive letter
        return True
    if name.startswith("//") or name.startswith("\\\\"):  # UNC
        return True
    norm = posixpath.normpath(name)
    return norm == ".." or norm.startswith("../") or "/../" in norm or norm.startswith("/")


def safe_extract_zip(
    raw: bytes,
    *,
    max_total: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_entry: int = DEFAULT_MAX_ENTRY_UNCOMPRESSED,
    max_ratio: int = DEFAULT_MAX_RATIO,
    skip_name: Optional[Callable[[str], bool]] = None,
) -> dict[str, bytes]:
    """Extract a ZIP from ``raw`` bytes to ``{normalized_name: content}``.

    ``skip_name(name)`` (optional) is consulted per entry BEFORE any bytes are
    read — matching entries (e.g. ``__MACOSX``/dotfile noise) are ignored
    entirely, so they don't decompress or count toward the size budget.

    Raises ``UnsafeZipError`` on any limit/safety violation.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError("bad_zip", "The file is not a valid ZIP archive.") from exc

    infos = zf.infolist()
    if len(infos) > max_entries:
        raise UnsafeZipError(
            "too_many_entries",
            f"Archive has {len(infos)} entries; the limit is {max_entries}.",
        )

    out: dict[str, bytes] = {}
    running_total = 0

    for zi in infos:
        name = zi.filename
        if zi.is_dir():
            continue
        if skip_name is not None and skip_name(name):
            continue  # noise (e.g. __MACOSX/dotfiles) — never read/decompress
        if _is_unsafe_path(name):
            raise UnsafeZipError("unsafe_path", f"Unsafe entry path: {name!r}")

        # Reject symlinks / devices. The unix mode is the high 16 bits of
        # external_attr. Only inspect the file-TYPE bits (S_IFMT): when they're
        # present and not a regular file it's a symlink/device → reject. Entries
        # with no type bits (DOS attrs, e.g. writestr's 0o600) are allowed.
        mode = (zi.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(mode) and not stat.S_ISREG(mode):
            raise UnsafeZipError("unsafe_path", f"Non-regular (symlink?) entry: {name!r}")

        entry_total = 0
        buf = bytearray()
        first = True
        with zf.open(zi) as src:
            while True:
                chunk = src.read(_READ_CHUNK)
                if not chunk:
                    break
                if first:
                    if name.lower().endswith(".zip") or chunk[:4] == _ZIP_MAGIC:
                        raise UnsafeZipError("nested_zip", f"Nested archives are not allowed: {name!r}")
                    first = False
                entry_total += len(chunk)
                running_total += len(chunk)
                if entry_total > max_entry:
                    raise UnsafeZipError("zip_bomb", f"Entry exceeds {max_entry} bytes uncompressed: {name!r}")
                if running_total > max_total:
                    raise UnsafeZipError("zip_too_large", f"Archive exceeds {max_total} bytes uncompressed.")
                buf.extend(chunk)

        comp = zi.compress_size or 0
        if comp > 0 and entry_total / comp > max_ratio:
            raise UnsafeZipError(
                "zip_bomb",
                f"Compression ratio {entry_total // comp}x exceeds {max_ratio}x: {name!r}",
            )

        out[posixpath.normpath(name)] = bytes(buf)

    return out


# ── W24 ADM-05: a streaming scan that keeps no member bytes ──────────────────


@dataclass(frozen=True)
class ScannedMember:
    """One regular member of a scanned archive: its RAW entry name (never
    normalised, never used as a filesystem path), its uncompressed size as
    actually read, and the SHA-256 of those bytes."""

    name: str
    size: int
    sha256: str


def scan_zip(
    fileobj: IO[bytes],
    *,
    max_total: int,
    max_entries: int,
    max_entry: int,
    max_ratio: int,
) -> list[ScannedMember]:
    """Scan a seekable ZIP file object with ``safe_extract_zip``'s checks.

    The SAME checks in the SAME order with the SAME ``UnsafeZipError`` codes
    (``bad_zip``, ``too_many_entries``, ``unsafe_path``, ``nested_zip``,
    ``zip_bomb``, ``zip_too_large``), but each member is streamed ONCE through
    SHA-256 and its bytes are dropped chunk by chunk — nothing is kept, so a
    50 MiB upload holding 1 GiB of JSONL costs one read chunk of memory.

    Directory entries are skipped like ``safe_extract_zip`` skips them (they
    still count toward ``max_entries``). Two additions a caller can only gain
    from: an encrypted member and a member whose compressed stream is corrupt
    raise ``bad_zip`` instead of a bare ``RuntimeError`` / ``zlib.error``.

    The messages carry entry POSITIONS, never entry names: an archive is
    attacker-controlled, and a name may end up in a job's error sentence.
    """
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise UnsafeZipError("bad_zip", "The file is not a valid ZIP archive.") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise UnsafeZipError(
                "too_many_entries",
                f"Archive has {len(infos)} entries; the limit is {max_entries}.",
            )

        scanned: list[ScannedMember] = []
        running_total = 0
        for position, zi in enumerate(infos, start=1):
            name = zi.filename
            if zi.is_dir():
                continue
            if _is_unsafe_path(name):
                raise UnsafeZipError("unsafe_path", f"Entry {position} has an unsafe path.")
            mode = (zi.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) and not stat.S_ISREG(mode):
                raise UnsafeZipError("unsafe_path", f"Entry {position} is not a regular file (symlink?).")
            if zi.flag_bits & 0x1:
                raise UnsafeZipError("bad_zip", f"Entry {position} is encrypted.")

            digest = hashlib.sha256()
            entry_total = 0
            first = True
            try:
                with zf.open(zi) as src:
                    while True:
                        chunk = src.read(_READ_CHUNK)
                        if not chunk:
                            break
                        if first:
                            if name.lower().endswith(".zip") or chunk[:4] == _ZIP_MAGIC:
                                raise UnsafeZipError(
                                    "nested_zip", f"Entry {position} is a nested archive; nested archives are not allowed."
                                )
                            first = False
                        entry_total += len(chunk)
                        running_total += len(chunk)
                        if entry_total > max_entry:
                            raise UnsafeZipError(
                                "zip_bomb", f"Entry {position} exceeds {max_entry} bytes uncompressed."
                            )
                        if running_total > max_total:
                            raise UnsafeZipError("zip_too_large", f"Archive exceeds {max_total} bytes uncompressed.")
                        digest.update(chunk)
            except UnsafeZipError:
                raise
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
                raise UnsafeZipError("bad_zip", f"Entry {position} could not be read.") from exc

            comp = zi.compress_size or 0
            if comp > 0 and entry_total / comp > max_ratio:
                raise UnsafeZipError(
                    "zip_bomb",
                    f"Entry {position} has a compression ratio of {entry_total // comp}x; the limit is {max_ratio}x.",
                )
            scanned.append(ScannedMember(name=name, size=entry_total, sha256=digest.hexdigest()))

    return scanned
