"""Prove M16 lifecycle and plan-integrity regressions kill unsafe behavior."""
from __future__ import annotations

import os
import subprocess
import sys
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
        "case-edit-stale-version",
        "backend/app/services/test_management_service.py",
        "    if test_case.version != payload.expected_version:\n",
        "    if False and test_case.version != payload.expected_version:\n",
        "backend/tests/test_exploratory_m16_test_management.py::test_case_edit_refuses_a_stale_case_version",
    ),
    Mutation(
        "lifecycle-stale-version",
        "backend/app/services/test_case_lifecycle_service.py",
        "    if expected_version is not None and test_case.version != expected_version:\n",
        "    if False and expected_version is not None and test_case.version != expected_version:\n",
        "backend/tests/test_exploratory_m16_test_management.py::test_lifecycle_transition_refuses_a_stale_case_version",
    ),
    Mutation(
        "plan-case-project-scope",
        "backend/app/services/test_management_service.py",
        "            ManagedTestCase.id == payload.test_case_id,\n            ManagedTestCase.project_id == plan.project_id,\n",
        "            ManagedTestCase.id == payload.test_case_id,\n",
        "backend/tests/test_exploratory_m16_test_management.py::test_plan_item_refuses_a_case_from_another_project",
    ),
    Mutation(
        "plan-execution-vocabulary",
        "backend/app/models/schemas.py",
        '    execution_status: Literal["passed", "failed", "blocked", "skipped"]\n',
        "    execution_status: str\n",
        "backend/tests/test_exploratory_m16_test_management.py::test_plan_execution_status_is_a_closed_vocabulary",
    ),
    Mutation(
        "lifecycle-client-version",
        "frontend/src/components/testManagement/LifecyclePanel.tsx",
        "        expected_version: caseItem.version,\n",
        "",
        "frontend/src/components/testManagement/LifecyclePanel.test.tsx",
    ),
    Mutation(
        "plan-item-audit",
        "backend/app/services/test_management_service.py",
        '    await audit_event(\n        db,\n        "test_plan_item",\n        item.id,\n        plan.project_id,\n        "executed",\n',
        '    if False:\n        await audit_event(\n            db,\n            "test_plan_item",\n            item.id,\n            plan.project_id,\n            "executed",\n',
        "backend/tests/test_exploratory_m16_test_management.py::test_plan_membership_and_execution_write_audit_rows",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    if test.startswith("frontend/"):
        npm = "npm.cmd" if os.name == "nt" else "npm"
        return subprocess.run(
            [npm, "run", "test", "--", test.removeprefix("frontend/")],
            cwd=ROOT / "frontend",
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m16-mutation-{suffix}",
            test,
        ],
        cwd=ROOT,
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
        source = originals[path].decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
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
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])
    print(f"M16 mutation check passed: {len(MUTATIONS)} unsafe changes were killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
