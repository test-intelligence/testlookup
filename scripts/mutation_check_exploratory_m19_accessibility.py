"""Prove M19 dialog accessibility regressions kill unsafe behavior."""
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
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "release-dialog-focus-contract",
        "frontend/src/pages/ReleasesPage.tsx",
        '<div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-form-title"',
        '<div role="dialog" aria-modal="true" aria-labelledby="release-form-title"',
        "src/pages/ReleasesPage.a11y.test.tsx#release editor modal accessibility",
    ),
    Mutation(
        "release-dialog-close-name",
        "frontend/src/pages/ReleasesPage.tsx",
        'type="button" aria-label="Close release dialog"',
        'type="button"',
        "src/pages/ReleasesPage.a11y.test.tsx#release editor modal accessibility",
    ),
    Mutation(
        "link-run-dialog-focus-contract",
        "frontend/src/pages/ReleasesPage.tsx",
        '<div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="link-test-run-title"',
        '<div role="dialog" aria-modal="true" aria-labelledby="link-test-run-title"',
        "src/pages/ReleasesPage.a11y.test.tsx#link run modal accessibility",
    ),
    Mutation(
        "link-run-dialog-close-name",
        "frontend/src/pages/ReleasesPage.tsx",
        'type="button" aria-label="Close link test run dialog"',
        'type="button"',
        "src/pages/ReleasesPage.a11y.test.tsx#link run modal accessibility",
    ),
    Mutation(
        "correction-dialog-focus-contract",
        "frontend/src/components/ai/DecisionReportFeedbackControls.tsx",
        '<form ref={correctionDialogRef} role="dialog"',
        '<form role="dialog"',
        "src/components/ai/DecisionReportFeedbackControls.test.tsx#keeps keyboard focus",
    ),
    Mutation(
        "evidence-dialog-focus-contract",
        "frontend/src/components/ai/DecisionIntelligencePanel.tsx",
        '<div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="claim-evidence-heading"',
        '<div role="dialog" aria-modal="true" aria-labelledby="claim-evidence-heading"',
        "src/components/ai/DecisionIntelligencePanel.test.tsx#separates typed claims",
    ),
)


def run_test(test: str) -> subprocess.CompletedProcess[str]:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    path, _, pattern = test.partition("#")
    return subprocess.run(
        [npm, "run", "test", "--", path, "-t", pattern],
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
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        source = originals[path].decode("utf-8")
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
            restore(path, originals[path])
    print(f"M19 accessibility mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
