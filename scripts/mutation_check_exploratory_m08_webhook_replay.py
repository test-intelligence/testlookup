"""Prove release-decision webhook retries use current, exact review authority."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "backend/tests/test_release_decided_webhook.py::test_an_agent_decision_stages_exactly_one_delivery_after_its_commit",
    "backend/tests/test_release_decided_webhook.py::test_webhook_retry_rechecks_review_and_restores_the_original_decision",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_uses_its_exact_pipeline_review",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_retry_without_evidence_hash_fails_closed",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_honours_the_project_draft_setting",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_reports_would_refuse_while_enforcement_is_off",
    "backend/tests/services/test_webhook_service.py::test_deliver_sends_the_fresh_review_projection",
]
MUTATIONS = [
    (
        ROOT / "backend/app/services/webhook_service.py",
        "release-event-loses-run-id",
        '    if event_type in {"run.completed", "release.decided"}:\n',
        '    if event_type == "run.completed":\n',
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "delivery-sends-stale-projection",
        '            "data": delivery_payload,\n',
        '            "data": delivery.event_payload or {},\n',
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "retry-forgets-original-recommendation",
        "    if draft_recommendation is not None:\n        original[\"recommendation\"] = draft_recommendation\n",
        "    if False and draft_recommendation is not None:\n        original[\"recommendation\"] = draft_recommendation\n",
    ),
    (
        ROOT / "backend/app/services/report_distribution_policy.py",
        "release-uses-newest-run-review",
        """        envelope = await review_envelope_for_pipeline_subject(
            db,
            pipeline_run_id,
            evidence_bundle_sha256=evidence_bundle_sha256,
        )
""",
        '        envelope = await review_envelope_for_run(db, run_id, workflow_type="deep")\n',
    ),
    (
        ROOT / "backend/app/services/report_distribution_policy.py",
        "project-draft-setting-is-ignored",
        '    projected["draft_recommendation"] = None\n    if decision.watermark:\n',
        '    projected["draft_recommendation"] = None\n    if False and decision.watermark:\n',
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "successful-delivery-skips-audit",
        "                if distribution_decision is None:\n                    return\n",
        "                if distribution_decision is not None:\n                    return\n",
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "successful-history-keeps-stale-payload",
        '                    "event_payload": delivery_payload,\n',
        '                    "event_payload": delivery.event_payload,\n',
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "retry-drops-evidence-subject",
        "        evidence_bundle_sha256=evidence_bundle_sha256,\n",
        "        evidence_bundle_sha256=None,\n",
    ),
    (
        ROOT / "backend/app/services/webhook_service.py",
        "legacy-retry-borrows-pipeline-review",
        '        str(raw_evidence_hash) if raw_evidence_hash is not None else ""\n',
        '        str(raw_evidence_hash) if raw_evidence_hash is not None else None\n',
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
            f".pytest-tmp-exploratory-m08-webhook-{label}",
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
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    for target, name, good, bad in MUTATIONS:
        original = target.read_bytes()
        source = original.decode("utf-8")
        if source.count(good) != 1:
            raise AssertionError(f"{name} mutation must apply exactly once")
        try:
            target.write_text(source.replace(good, bad, 1), encoding="utf-8", newline="")
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
    print(f"M08 webhook-replay mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
