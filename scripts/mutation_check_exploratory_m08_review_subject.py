"""Prove immutable DecisionReport versions keep their exact review subject."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "backend/tests/test_review_envelope.py::test_immutable_pipeline_subject_lookup_retains_superseded_state",
    "backend/tests/test_review_envelope.py::test_decision_report_versions_use_their_exact_pipeline_reviews",
    "backend/tests/test_review_envelope.py::test_selected_decision_report_uses_its_exact_pipeline_review",
    "backend/tests/services/test_decision_report_service.py::test_report_version_selection_is_run_scoped_and_bounded",
]

MUTATIONS = [
    (
        ROOT / "backend/app/services/review_envelope.py",
        "historical-subject-drops-superseded",
        """            .where(\n                ReviewRequest.pipeline_run_id == pipeline_uuid,\n                ReviewRequest.kind == \"report\",\n            )\n            .order_by(ReviewRequest.created_at.desc())\n""",
        """            .where(\n                ReviewRequest.pipeline_run_id == pipeline_uuid,\n                ReviewRequest.kind == \"report\",\n                ReviewRequest.state != \"superseded\",\n            )\n            .order_by(ReviewRequest.created_at.desc())\n""",
    ),
    (
        ROOT / "backend/app/services/decision_report_service.py",
        "version-metadata-drops-pipeline-subject",
        """            \"pipeline_run_id\": row.get(\"pipeline_run_id\"),\n""",
        "",
    ),
    (
        ROOT / "backend/app/routers/run_intelligence.py",
        "version-list-inherits-newest-run-review",
        """        pipeline_subject = public_version.pop(\"pipeline_run_id\", None)\n        envelope = await review_envelope_for_pipeline_subject(db, pipeline_subject)\n""",
        """        pipeline_subject = public_version.pop(\"pipeline_run_id\", None)\n        envelope = await review_envelope_for_run(db, run_id, workflow_type=\"deep\")\n""",
    ),
    (
        ROOT / "backend/app/routers/run_intelligence.py",
        "selected-version-inherits-newest-run-review",
        """        if report_version is not None and pipeline_subject is not None:\n            envelope = await review_envelope_for_pipeline_subject(db, pipeline_subject)\n""",
        """        if report_version is not None and pipeline_subject is not None:\n            envelope = await review_envelope_for_run(db, run_id, workflow_type=\"deep\")\n""",
    ),
]


def _run(label: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m08-review-{label}",
            *TESTS,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    baseline = _run("baseline")
    if baseline.returncode != 0:
        raise AssertionError(
            "mutation baseline failed\n" + baseline.stdout + baseline.stderr
        )

    for target, name, good, bad in MUTATIONS:
        original = target.read_bytes()
        source = original.decode("utf-8")
        if source.count(good) != 1:
            raise AssertionError(f"{name} mutation must apply exactly once")
        try:
            target.write_text(
                source.replace(good, bad, 1), encoding="utf-8", newline=""
            )
            mutated = _run(name)
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

    print(f"M08 review-subject mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
