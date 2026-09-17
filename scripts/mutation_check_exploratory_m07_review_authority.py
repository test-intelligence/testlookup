"""Prove the M07 review-authority regressions reject unsafe mutations."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    target: str
    good: str
    bad: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation(
        "subject row lock",
        "backend/app/services/review_request_service.py",
        """            .where(\n                ReviewRequest.kind == \"report\",\n                ReviewRequest.subject_type == \"pipeline_run\",\n                ReviewRequest.subject_id == subject_id,\n                ReviewRequest.state != \"superseded\",\n            )\n            .with_for_update()\n""",
        """            .where(\n                ReviewRequest.kind == \"report\",\n                ReviewRequest.subject_type == \"pipeline_run\",\n                ReviewRequest.subject_id == subject_id,\n                ReviewRequest.state != \"superseded\",\n            )\n""",
        ("tests/services/test_review_request_service.py::test_live_subject_lookup_is_locked",),
    ),
    Mutation(
        "evidence identity",
        "backend/app/services/review_request_service.py",
        """        live.state = \"superseded\"\n        await db.flush()\n        review_requests_total.labels(state=\"superseded\").inc()\n""",
        """        live.evidence_bundle_sha256 = evidence_bundle_sha256\n        await db.flush()\n        return live\n""",
        ("tests/services/test_review_request_service.py::test_changed_pending_evidence_mints_a_new_review_subject",),
    ),
    Mutation(
        "investigator subject isolation",
        "backend/app/services/review_request_service.py",
        "if test_run_id is not None and workflow_type and workflow_type != \"investigation\":",
        "if test_run_id is not None and workflow_type:",
        ("tests/services/test_review_request_service.py::test_distinct_investigations_keep_distinct_pending_reviews",),
    ),
    Mutation(
        "parent envelope scope",
        "backend/app/services/review_envelope.py",
        """    else:\n        # Investigator narratives have exact pipeline-scoped reviews. They\n        # share their parent test_run_id, so they must never authorize a parent\n        # summary/export that asks for the run's ordinary report envelope.\n        stmt = stmt.where(\n            or_(\n                ReviewRequest.workflow_type.is_(None),\n                ReviewRequest.workflow_type != \"investigation\",\n            )\n        )\n""",
        "",
        ("tests/test_review_envelope.py::test_a_generic_parent_lookup_excludes_investigator_subjects",),
    ),
    Mutation(
        "frozen act-mode separation",
        "backend/app/services/review_request_service.py",
        """        if mode == \"act\":\n            return True\n""",
        """        if mode == \"act\":\n            return False\n""",
        ("tests/services/test_review_request_service.py::test_self_review_uses_frozen_act_mode_after_live_config_changes",),
    ),
    Mutation(
        "settlement refresh",
        "backend/app/routers/reviews.py",
        """            .with_for_update()\n            .execution_options(populate_existing=True)\n""",
        """            .with_for_update()\n""",
        ("tests/test_reviews_api.py::test_the_review_and_its_pipeline_are_locked_in_finalize_order",),
    ),
    Mutation(
        "pipeline-first settlement lock",
        "backend/app/routers/reviews.py",
        """    if review.pipeline_run_id is not None:\n        await db.execute(\n            select(AgentPipelineRun.id)\n            .where(AgentPipelineRun.id == review.pipeline_run_id)\n            .with_for_update()\n        )\n""",
        "",
        ("tests/test_reviews_api.py::test_the_review_and_its_pipeline_are_locked_in_finalize_order",),
    ),
    Mutation(
        "rejected worker resume refusal",
        "backend/app/agents/workflow.py",
        """        if str(getattr(pipeline, \"error\", \"\") or \"\").startswith(\n            REVIEW_REJECTED_ERROR_PREFIX\n        ):\n            return None\n""",
        "",
        ("tests/test_decision_evidence_checkpoint_resume.py::test_queued_resume_cannot_resurrect_a_review_rejected_pipeline",),
    ),
)


def _kill(mutation: Mutation) -> None:
    target = ROOT / mutation.target
    original = target.read_bytes()
    source = original.decode("utf-8")
    try:
        count = source.count(mutation.good)
        if count != 1:
            raise AssertionError(
                f"{mutation.name}: mutation must apply exactly once; found {count}"
            )
        mutated = source.replace(mutation.good, mutation.bad, 1)
        if mutated == source:
            raise AssertionError(f"{mutation.name}: mutation did not change source")
        target.write_text(mutated, encoding="utf-8", newline="")
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:testlookup",
                "--basetemp",
                f".pytest-tmp-exploratory-m07-mutation-{MUTATIONS.index(mutation)}",
                *mutation.tests,
            ],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            timeout=90,
        )
        target.write_bytes(original)
        if target.read_bytes() != original:
            raise AssertionError(f"{mutation.name}: source restoration failed")
        if run.returncode == 0:
            raise AssertionError(
                f"{mutation.name}: mutation survived\n{run.stdout}{run.stderr}"
            )
    finally:
        target.write_bytes(original)


def main() -> None:
    for mutation in MUTATIONS:
        _kill(mutation)
    print(f"M07 review-authority mutation check: {len(MUTATIONS)} mutations killed")


if __name__ == "__main__":
    main()
