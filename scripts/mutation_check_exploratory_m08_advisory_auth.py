"""Prove advisory release values cannot bypass the reviewer role boundary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "backend/tests/test_distribution_gates.py::test_release_advisory_override_requires_qa_lead",
    "backend/tests/test_distribution_gates.py::test_release_readiness_uses_exact_pipeline_and_audits_advisory",
    "backend/tests/test_distribution_gates.py::test_release_advisory_route_commits_exact_subject_audit",
]
MUTATIONS = [
    (
        ROOT / "backend/app/routers/release_readiness.py",
        "advisory-skips-role-boundary",
        """    if allow_advisory:\n        role = getattr(current_user.role, \"value\", current_user.role)\n        if role not in {UserRole.QA_LEAD.value, UserRole.ADMIN.value}:\n            raise HTTPException(\n                status_code=status.HTTP_403_FORBIDDEN,\n                detail=\"Advisory release values require the QA Lead role.\",\n            )\n""",
        "",
    ),
    (
        ROOT / "backend/app/services/report_distribution_policy.py",
        "advisory-skips-audit",
        "            if allow_advisory and actor is not None:\n",
        "            if False and allow_advisory and actor is not None:\n",
    ),
    (
        ROOT / "backend/app/routers/release_readiness.py",
        "readiness-drops-exact-pipeline-subject",
        "            pipeline_run_id=pipeline_run_id,\n",
        "            pipeline_run_id=None,\n",
    ),
    (
        ROOT / "backend/app/routers/release_readiness.py",
        "advisory-skips-commit",
        "        if allow_advisory and response.recommendation.startswith(\"ADVISORY_\"):\n            await db.commit()\n",
        "",
    ),
]


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:testlookup",
        "--basetemp",
        ".pytest-tmp-exploratory-m08-advisory-mutation",
        *TESTS,
    ]
    baseline = subprocess.run(
        command, cwd=ROOT, check=False, capture_output=True, text=True
    )
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    for target, name, good, bad in MUTATIONS:
        original = target.read_bytes()
        source = original.decode("utf-8")
        if source.count(good) != 1:
            raise AssertionError(f"{name} mutation must apply exactly once")
        try:
            target.write_text(source.replace(good, bad, 1), encoding="utf-8", newline="")
            mutated = subprocess.run(
                command, cwd=ROOT, check=False, capture_output=True, text=True
            )
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{name} mutation was not killed with pytest exit 1\n"
                    + mutated.stdout
                    + mutated.stderr
                )
        finally:
            target.write_bytes(original)
        if target.read_bytes() != original:
            raise AssertionError(f"{name} mutation did not restore its target")
    print(f"M08 advisory-authorisation mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
