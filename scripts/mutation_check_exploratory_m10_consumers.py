"""Prove M10 browser regressions reject unsafe consumer mutations."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test_file: str


MUTATIONS = (
    Mutation(
        "unsafe-source-scheme",
        "frontend/src/components/rag/CitationDrawer.tsx",
        "return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null",
        "return parsed.href",
        "src/components/rag/CitationDrawer.test.tsx",
    ),
    Mutation(
        "missing-unsupported-label",
        "frontend/src/components/rag/GenerationReviewPanel.tsx",
        "{!hasCitations && result.generation_mode === 'grounded' && (",
        "{false && !hasCitations && result.generation_mode === 'grounded' && (",
        "src/components/rag/GenerationReviewPanel.test.tsx",
    ),
    Mutation(
        "cross-project-state",
        "frontend/src/pages/test-management/KnowledgeGenerationTab.tsx",
        "    setPromptText('')",
        "    // prompt retained across projects",
        "src/pages/test-management/KnowledgeGenerationTab.test.tsx",
    ),
)


def run_test(test_file: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["npm.cmd", "run", "test", "--", test_file],
        cwd=FRONTEND,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        text = originals[path].decode("utf-8")
        if text.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test_file)
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                text.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test_file)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with vitest exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])
    for path, original in originals.items():
        if path.read_bytes() != original:
            raise AssertionError(f"mutation harness did not restore {path}")
    print(f"M10 consumer mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
