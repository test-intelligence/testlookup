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
        "stable parent scope lock",
        "backend/app/services/review_request_service.py",
        """    if test_run_id is not None:\n        await db.execute(\n            select(TestRun.id).where(TestRun.id == test_run_id).with_for_update()\n        )\n\n""",
        "",
        ("tests/services/test_review_request_service.py::test_review_creation_locks_the_stable_parent_before_inserting",),
    ),
    Mutation(
        "subject row lock",
        "backend/app/services/review_request_service.py",
        """            .where(\n                ReviewRequest.kind == \"report\",\n                ReviewRequest.subject_type == \"pipeline_run\",\n                ReviewRequest.subject_id == subject_id,\n                ReviewRequest.state != \"superseded\",\n            )\n            .with_for_update()\n""",
        """            .where(\n                ReviewRequest.kind == \"report\",\n                ReviewRequest.subject_type == \"pipeline_run\",\n                ReviewRequest.subject_id == subject_id,\n                ReviewRequest.state != \"superseded\",\n            )\n""",
        ("tests/services/test_review_request_service.py::test_review_creation_locks_live_rows_before_refresh_or_supersession",),
    ),
    Mutation(
        "evidence identity",
        "backend/app/services/review_request_service.py",
        """        live.state = \"superseded\"\n        await db.flush()\n        review_requests_total.labels(state=\"superseded\").inc()\n""",
        """        live.evidence_bundle_sha256 = evidence_bundle_sha256\n        await db.flush()\n        return live\n""",
        ("tests/services/test_review_request_service.py::test_refinalizing_changed_evidence_supersedes_the_stale_pending_request",),
    ),
    Mutation(
        "older scope row lock",
        "backend/app/services/review_request_service.py",
        """                    ReviewRequest.subject_id != subject_id,\n                )\n                .with_for_update()\n""",
        """                    ReviewRequest.subject_id != subject_id,\n                )\n""",
        ("tests/services/test_review_request_service.py::test_newer_run_locks_older_pending_scope_before_supersession",),
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
        ("tests/test_review_envelope.py::test_generic_parent_report_lookup_excludes_investigator_subjects",),
    ),
    Mutation(
        "frozen act-mode separation",
        "backend/app/services/review_request_service.py",
        """        if mode == \"act\":\n            return True\n""",
        """        if mode == \"act\":\n            return False\n""",
        ("tests/test_reviews_api.py::test_frozen_act_proposal_cannot_self_review_after_live_mode_is_lowered",),
    ),
    Mutation(
        "canonical proposer identity",
        "backend/app/services/review_request_service.py",
        "            capability_id = get_capability(agent_id).capability_id\n",
        "            capability_id = agent_id\n",
        ("tests/test_reviews_api.py::test_the_requester_may_review_when_the_run_has_no_act_mode_proposal",),
    ),
    Mutation(
        "invocation accepted snapshot precedence",
        "backend/app/services/review_request_service.py",
        "        if isinstance(invocation_configs, dict):\n",
        "        if False and isinstance(invocation_configs, dict):\n",
        ("tests/test_reviews_api.py::test_invocation_separation_prefers_the_api_accepted_snapshot",),
    ),
    Mutation(
        "settlement refresh",
        "backend/app/routers/reviews.py",
        """            .with_for_update()\n            .execution_options(populate_existing=True)\n""",
        """            .with_for_update()\n""",
        ("tests/test_reviews_api.py::test_settlement_locks_pipeline_before_review_to_match_finalization_order",),
    ),
    Mutation(
        "pipeline-first settlement lock",
        "backend/app/routers/reviews.py",
        """    if review.pipeline_run_id is not None:\n        await db.execute(\n            select(AgentPipelineRun.id)\n            .where(AgentPipelineRun.id == review.pipeline_run_id)\n            .with_for_update()\n        )\n""",
        "",
        ("tests/test_reviews_api.py::test_settlement_locks_pipeline_before_review_to_match_finalization_order",),
    ),
    Mutation(
        "invocation authority survives finalization",
        "backend/app/agents/workflow.py",
        "                    \"resolved_agent_configs\": prior_metadata.get(\"resolved_agent_configs\") or {},\n",
        "",
        ("tests/services/test_pipeline_public_status.py::test_finalize_preserves_the_invocation_config_authority",),
    ),
    Mutation(
        "exact investigator pipeline lookup",
        "backend/app/services/review_envelope.py",
        "                ReviewRequest.pipeline_run_id == pipeline_uuid,\n",
        "                ReviewRequest.test_run_id == pipeline_uuid,\n",
        ("tests/test_review_envelope.py::test_investigator_lookup_is_bound_to_the_exact_pipeline_subject",),
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
        if run.returncode != 1:
            raise AssertionError(
                f"{mutation.name}: pytest did not report an assertion failure "
                f"(exit {run.returncode})\n{run.stdout}{run.stderr}"
            )
    finally:
        target.write_bytes(original)


def main() -> None:
    selectors = tuple(dict.fromkeys(test for mutation in MUTATIONS for test in mutation.tests))
    baseline = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            ".pytest-tmp-exploratory-m07-mutation-baseline",
            *selectors,
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        timeout=180,
    )
    if baseline.returncode != 0 or "passed" not in baseline.stdout:
        raise AssertionError(
            "mutation baseline selectors must all exist and pass\n"
            + baseline.stdout
            + baseline.stderr
        )
    for mutation in MUTATIONS:
        _kill(mutation)
    print(f"M07 review-authority mutation check: {len(MUTATIONS)} mutations killed")


if __name__ == "__main__":
    main()
