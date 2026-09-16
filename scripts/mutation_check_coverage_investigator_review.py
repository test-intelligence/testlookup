"""Prove Investigator review-subject and distribution regressions are killed."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "backend/tests/test_investigator_api.py::test_start_investigation_202_with_default_policy",
    "backend/tests/test_investigator_workflow.py::test_completed_investigator_stages_review_for_its_narrative",
    "backend/tests/test_notification_distribution_gates.py::test_pending_investigator_excerpt_is_withheld_by_its_exact_subject",
    "backend/tests/test_proactive_narratives.py::test_collector_withholds_pending_narrative_before_report_render",
)
MUTATIONS = (
    (
        "backend/app/routers/agent_investigations.py",
        "requested_by=current_user.id,",
        "requested_by=None,",
    ),
    (
        "backend/app/agents/investigator/workflow.py",
        'if status == "completed" and pipeline is not None and row.verdict:',
        'if False and status == "completed" and pipeline is not None and row.verdict:',
    ),
    (
        "backend/app/services/report_distribution_policy.py",
        "return INVESTIGATION_REVIEW_PENDING_NOTICE, decision",
        "return excerpt, decision",
    ),
    (
        "backend/app/services/analysis_report_service.py",
        '"narrative_excerpt": gated_excerpt,',
        '"narrative_excerpt": excerpt,',
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    for relative, good, bad in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {relative}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                [
                    str(python),
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:testlookup",
                    "--basetemp=.pytest-tmp-coverage-investigator-mutation",
                    *TESTS,
                ],
                cwd=ROOT,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {relative}: {bad!r}")
        finally:
            path.write_text(original, encoding="utf-8")
    print(f"Investigator review mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
