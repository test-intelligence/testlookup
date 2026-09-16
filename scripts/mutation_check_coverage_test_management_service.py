"""Prove frontend service-contract coverage kills request-integrity mutations."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend/src/services/testManagementService.ts"
TEST = "src/services/testManagementService.test.ts"
MUTATIONS = (
    (
        "getData('/api/v1/test-management/cases', { params: { ...(projectId ? { project_id: projectId } : {}), ...params } }),",
        "getData('/api/v1/test-management/cases', { params: { ...params } }),",
    ),
    (
        "encodeURIComponent(suiteName)}/trend",
        "suiteName}/trend",
    ),
    (
        "window.URL.revokeObjectURL(blobUrl)",
        "void blobUrl",
    ),
)


def _write_bytes_with_retry(path: Path, content: bytes) -> None:
    """Allow the just-exited Vite worker time to release its Windows handle."""
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    original_bytes = TARGET.read_bytes()
    original = original_bytes.decode("utf-8")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    for good, bad in MUTATIONS:
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {TARGET}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        _write_bytes_with_retry(TARGET, mutated.encode("utf-8"))
        try:
            run = subprocess.run(
                [npm, "run", "test", "--", TEST],
                cwd=ROOT / "frontend",
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {TARGET}: {bad!r}")
        finally:
            _write_bytes_with_retry(TARGET, original_bytes)
    print(f"Test-management service mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
