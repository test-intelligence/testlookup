"""Prove project-scope isolation and outage recovery regressions are killed."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPED_SWR = ROOT / "frontend/src/hooks/useProjectScopedSWR.ts"
RUNS_PAGE = ROOT / "frontend/src/pages/RunsPage.tsx"
MUTATIONS = (
    (
        SCOPED_SWR,
        "src/hooks/useProjectScopedSWR.test.ts",
        "[baseKey, projectId, ...deps]",
        "[baseKey, ...deps]",
    ),
    (
        RUNS_PAGE,
        "src/pages/outageRendersAsNoData.lists.test.tsx",
        "if (runsError && !data) {",
        "if (false) {",
    ),
    (
        RUNS_PAGE,
        "src/pages/outageRendersAsNoData.lists.test.tsx",
        "onRetry={() => void retryRuns()}",
        "onRetry={() => undefined}",
    ),
)


def _write_bytes_with_retry(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    originals: dict[Path, bytes] = {}
    for target, test_file, good, bad in MUTATIONS:
        original_bytes = originals.setdefault(target, target.read_bytes())
        original = original_bytes.decode("utf-8")
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {target}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        _write_bytes_with_retry(target, mutated.encode("utf-8"))
        try:
            run = subprocess.run(
                [npm, "run", "test", "--", test_file],
                cwd=ROOT / "frontend",
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {target}: {bad!r}")
        finally:
            _write_bytes_with_retry(target, original_bytes)
    print(f"Project isolation mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
