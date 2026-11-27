#!/usr/bin/env python3
"""Scan every tracked candidate blob, including secrets unchanged by this push.

The added-history scan also runs: a secret added and removed inside one push
must still fail. This complementary snapshot never reads untracked .env files
or a developer's working-tree changes. Raw Git blobs bypass export attributes,
checkout encodings and executable filters; their inventory and hashes are checked.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile


class SnapshotScanError(RuntimeError):
    """The exact tracked candidate could not be completely checked."""


def _git(repository: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *arguments],
            input=input_data, capture_output=True, timeout=180, check=False,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotScanError("Cannot read the candidate Git tree") from exc
    if result.returncode:
        # Do not echo repository diagnostics or candidate-controlled filenames.
        raise SnapshotScanError("Cannot read the candidate Git tree")
    return result.stdout


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name or path.is_absolute() or "\\" in name or ":" in name
        or any(part in {".", ".."} for part in path.parts)
        or str(path) != name
    ):
        raise SnapshotScanError("Candidate has an unsafe snapshot path")
    return path


def stage_snapshot(repository: Path, source_sha: str, destination: Path) -> int:
    """Create a verified scan-only snapshot without links or Git metadata."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise SnapshotScanError("source_sha must be a lowercase full Git SHA")
    repository = repository.resolve()
    root = _git(repository, "rev-parse", "--show-toplevel").decode().strip()
    if Path(root).resolve() != repository:
        raise SnapshotScanError("repository must name its Git root")
    tree: dict[str, tuple[str, str, int]] = {}
    for entry in _git(repository, "ls-tree", "-r", "-l", "-z", "--full-tree", source_sha).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, oid, size = metadata.decode("ascii").split()
        name = raw_path.decode("utf-8")
        _safe_path(name)
        if kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise SnapshotScanError("Candidate has an unsupported snapshot entry")
        tree[name] = mode, oid, int(size)
    if not tree:
        raise SnapshotScanError("Candidate has no tracked blobs to scan")
    destination.mkdir()
    batch: list[tuple[str, str, int]] = []
    batch_size = 0

    def write_batch() -> None:
        # Bound each response by 1,024 entries / 8 MiB, except a single larger
        # blob. --batch returns RAW bytes: deliberately no --filters/textconv.
        request = b"".join(oid.encode("ascii") + b"\n" for _, oid, _ in batch)
        contents = io.BytesIO(_git(repository, "cat-file", "--batch", input_data=request))
        for name, expected_oid, size in batch:
            header = contents.readline().split()
            if header != [expected_oid.encode("ascii"), b"blob", str(size).encode("ascii")]:
                raise SnapshotScanError("Git blob response does not match the candidate")
            data = contents.read(size)
            if len(data) != size or contents.read(1) != b"\n":
                raise SnapshotScanError("Git blob response is incomplete")
            actual_oid = hashlib.sha1(
                b"blob " + str(len(data)).encode("ascii") + b"\0" + data,
                usedforsecurity=False,  # Git object identity, not a credential hash.
            ).hexdigest()
            if actual_oid != expected_oid:
                raise SnapshotScanError("Git blob bytes do not match the candidate")
            path = _safe_path(name)
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            # A symlink's raw blob becomes text; never follow its target.
            # Exclusive creation also rejects case-colliding paths on Windows.
            with target.open("xb") as output:
                output.write(data)
        if contents.read(1):
            raise SnapshotScanError("Git blob response has unexpected entries")

    for name, (_mode, oid, size) in tree.items():
        if batch and (len(batch) >= 1024 or batch_size + size > 8 * 1024 * 1024):
            write_batch()
            batch, batch_size = [], 0
        batch.append((name, oid, size))
        batch_size += size
    if batch:
        write_batch()
    return len(tree)


def scan_snapshot(repository: Path, source_sha: str, *, gitleaks: str, timeout_seconds: int = 300) -> int:
    """Run the pinned scanner against the verified tree and its tracked policy."""
    if not 0 < timeout_seconds <= 600:
        raise SnapshotScanError("timeout must be between 1 and 600 seconds")
    scanner = shutil.which(gitleaks)
    if not scanner:
        raise SnapshotScanError("The installed gitleaks executable is unavailable")
    scanner = str(Path(scanner).resolve())
    with tempfile.TemporaryDirectory(prefix="secret-snapshot-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        count = stage_snapshot(repository, source_sha, snapshot)
        if not (snapshot / ".gitleaks.toml").is_file():
            raise SnapshotScanError("Candidate has no tracked secret-scanning policy")
        try:
            result = subprocess.run(
                [scanner, "dir", ".", "--config", ".gitleaks.toml", "--redact",
                 "--no-banner", "--no-color", "--exit-code", "1", "--timeout", str(timeout_seconds)],
                cwd=snapshot, timeout=timeout_seconds + 10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SnapshotScanError("Candidate snapshot scanner did not finish") from exc
        if result.returncode:
            raise SnapshotScanError("Candidate snapshot did not pass secret scanning")
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--gitleaks", default="gitleaks")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        count = scan_snapshot(
            args.repository, args.source_sha,
            gitleaks=args.gitleaks, timeout_seconds=args.timeout_seconds,
        )
    except (SnapshotScanError, OSError, ValueError) as exc:
        # Low-level exception text can contain a path or candidate data.
        message = str(exc) if isinstance(exc, SnapshotScanError) else "Cannot stage the complete candidate snapshot"
        print(f"Secret snapshot refused: {message}", file=sys.stderr)
        return 1
    print(f"Candidate snapshot passed secret scanning ({count} tracked blobs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
