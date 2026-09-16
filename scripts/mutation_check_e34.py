"""Prove E3.4 workflow-editor tests kill unsafe UI and API mutations."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPM = "npm.cmd" if os.name == "nt" else "npm"
VITEST = (
    NPM,
    "run",
    "test",
    "--",
    "src/pages/WorkflowEditorPage.test.tsx",
    "src/services/workflowService.test.ts",
)

MUTATIONS = (
    (
        "frontend/src/pages/WorkflowEditorPage.tsx",
        "const canEdit = !!selected && isQaLead && !selected.built_in && selectedIsLatest",
        "const canEdit = !!selected && isQaLead && selectedIsLatest",
        1,
    ),
    (
        "frontend/src/pages/WorkflowEditorPage.tsx",
        '<span data-testid="workflow-eval-coverage">{formatCoverage(selected.eval_coverage)}</span>',
        '<span data-testid="workflow-eval-coverage">Unmeasured</span>',
        1,
    ),
    (
        "frontend/src/pages/WorkflowEditorPage.tsx",
        "if (!result.valid) {",
        "if (result.valid) {",
        1,
    ),
    (
        "frontend/src/pages/WorkflowEditorPage.tsx",
        "const sourceList = Array.isArray(edge.from) ? edge.from : [edge.from]",
        "const sourceList = Array.isArray(edge.from) ? edge.from.slice(0, 1) : [edge.from]",
        1,
    ),
    (
        "frontend/src/services/workflowService.ts",
        "return putData(`${base(projectId)}/${workflowId}`, body)",
        "return postData(`${base(projectId)}/${workflowId}`, body)",
        1,
    ),
    (
        "frontend/src/pages/WorkflowEditorPage.tsx",
        "accept_regression: acceptRegression,",
        "accept_regression: false,",
        1,
    ),
)


def main() -> int:
    for relative, good, bad, expected in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        count = original.count(good)
        if count != expected:
            raise AssertionError(
                f"mutation did not apply {expected} time(s): {relative}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                VITEST,
                cwd=ROOT / "frontend",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {relative}: {bad!r}")
    print(f"E3.4 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
