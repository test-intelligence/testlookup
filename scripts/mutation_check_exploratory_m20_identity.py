"""Prove M20 API-key UX regressions kill unsafe behavior."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    safe: str
    unsafe: str
    test: str


PATH = "frontend/src/pages/settings/ApiKeysPage.tsx"
MUTATIONS = (
    Mutation(
        "api-key-load-error-state",
        ") : error ? (\n          <DataUnavailable",
        ") : false ? (\n          <DataUnavailable",
        "renders a retryable outage",
    ),
    Mutation(
        "one-time-key-project-reset",
        "  useEffect(() => {\n    setShowForm(false)\n    setCreated(null)\n  }, [projectId])",
        "  useEffect(() => {\n    setShowForm(false)\n  }, [projectId])",
        "removes a one-time key secret",
    ),
    Mutation(
        "created-key-dialog-focus-contract",
        '<div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="api-key-generated-title"',
        '<div role="dialog" aria-modal="true" aria-labelledby="api-key-generated-title"',
        "contains keyboard focus",
    ),
)


def run_test(pattern: str) -> subprocess.CompletedProcess[str]:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return subprocess.run(
        [npm, "run", "test", "--", "src/pages/settings/ApiKeysPage.test.tsx", "-t", pattern],
        cwd=ROOT / "frontend",
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def restore(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    path = ROOT / PATH
    original = path.read_bytes()
    source = original.decode("utf-8")
    for mutation in MUTATIONS:
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test)
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            restore(path, original)
    print(f"M20 identity mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
