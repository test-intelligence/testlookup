"""Prove release-decision webhook retries use current, exact review authority."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "backend/tests/test_release_decided_webhook.py::test_release_risk_write_waits_for_immutable_report_publication",
    "backend/tests/test_release_decided_webhook.py::test_an_agent_decision_stages_exactly_one_delivery_after_its_commit",
    "backend/tests/test_release_decided_webhook.py::test_critic_emitter_refuses_a_concurrent_pipeline_replacement",
    "backend/tests/test_release_decided_webhook.py::test_delayed_agent_event_cannot_follow_a_human_override",
    "backend/tests/test_release_decided_webhook.py::test_agent_webhook_uses_immutable_report_decision_bytes",
    "backend/tests/test_release_decided_webhook.py::test_agent_webhook_refuses_a_report_hash_mismatch",
    "backend/tests/test_release_decided_webhook.py::test_an_override_is_announced_after_the_route_commits",
    "backend/tests/test_release_decided_webhook.py::test_override_webhook_does_not_require_an_immutable_agent_report",
    "backend/tests/test_release_decided_webhook.py::test_concurrent_override_emitters_keep_each_committed_snapshot",
    "backend/tests/test_release_decided_webhook.py::test_override_emitter_refuses_a_mismatched_audit_timestamp",
    "backend/tests/test_release_decided_webhook.py::test_webhook_retry_rechecks_review_and_restores_the_original_decision",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_uses_its_exact_pipeline_review",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_retry_without_evidence_hash_fails_closed",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_honours_the_project_draft_setting",
    "backend/tests/test_release_decided_webhook.py::test_release_webhook_reports_would_refuse_while_enforcement_is_off",
    "backend/tests/services/test_webhook_service.py::test_deliver_sends_the_fresh_review_projection",
    "backend/tests/test_decision_report_critic_agent.py::test_published_report_emits_release_webhook_with_exact_evidence",
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
        """    elif evidence_bundle_sha256 is None:
        envelope = await review_envelope_for_pipeline_subject(db, pipeline_run_id)
    else:
        envelope = await review_envelope_for_pipeline_subject(
            db,
            pipeline_run_id,
            evidence_bundle_sha256=evidence_bundle_sha256,
        )
""",
        """    elif evidence_bundle_sha256 is None:
        envelope = await review_envelope_for_pipeline_subject(db, pipeline_run_id)
    else:
        envelope = await review_envelope_for_run(db, run_id, workflow_type="deep")
""",
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
    (
        ROOT / "backend/app/agents/release_risk_agent.py",
        "release-risk-emits-before-report-publication",
        "        # AI-1 auto-trigger (shadow): a recorded NO_GO gate decision enqueues\n",
        """        from app.services.release_decision_webhook import (
            TRIGGER_AGENT,
            emit_release_decided,
        )
        await emit_release_decided(test_run_id, trigger=TRIGGER_AGENT)

        # AI-1 auto-trigger (shadow): a recorded NO_GO gate decision enqueues
""",
    ),
    (
        ROOT / "backend/app/agents/decision_report_critic_agent.py",
        "critic-drops-pipeline-binding",
        '                pipeline_run_id=state["pipeline_run_id"],\n',
        "                pipeline_run_id=None,\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "emitter-allows-concurrent-pipeline-replacement",
        """            if pipeline_run_id is not None and str(decision.pipeline_run_id) != str(
                pipeline_run_id
            ):
""",
        """            if False and pipeline_run_id is not None and str(decision.pipeline_run_id) != str(
                pipeline_run_id
            ):
""",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "override-requires-agent-report",
        """    if (
        trigger == TRIGGER_AGENT
        and decision.pipeline_run_id is not None
        and evidence_bundle_sha256 is None
    ):
""",
        """    if (
        trigger in {TRIGGER_AGENT, TRIGGER_OVERRIDE}
        and decision.pipeline_run_id is not None
        and evidence_bundle_sha256 is None
    ):
""",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "agent-uses-mutable-decision-bytes",
        '        recommendation = source.get("recommendation")\n',
        "        recommendation = decision.recommendation\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "agent-accepts-report-hash-mismatch",
        "            and str(evidence_bundle_sha256) != str(report_hash)\n",
        "            and False\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "agent-publishes-after-human-override",
        "            if trigger == TRIGGER_AGENT and decision.human_override is not None:\n",
        "            if False and trigger == TRIGGER_AGENT and decision.human_override is not None:\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "override-scope-uses-current-audit-length",
        '        marker = f"override:{override_ordinal}"\n',
        '        marker = f"override:{len(decision.override_audit or [])}"\n',
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "override-ignores-committed-snapshot",
        "    elif trigger == TRIGGER_OVERRIDE and override_snapshot is not None:\n",
        "    elif False and trigger == TRIGGER_OVERRIDE and override_snapshot is not None:\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "emitter-drops-decision-lock",
        "                    .where(ReleaseDecision.test_run_id == run_uuid)\n                    .with_for_update()\n",
        "                    .where(ReleaseDecision.test_run_id == run_uuid)\n",
    ),
    (
        ROOT / "backend/app/routers/release_readiness.py",
        "override-route-drops-committed-ordinal",
        "            override_ordinal=len(council.override_audit),\n",
        "            override_ordinal=None,\n",
    ),
    (
        ROOT / "backend/app/services/release_decision_webhook.py",
        "override-accepts-mismatched-audit-timestamp",
        """                if str(committed_entry.get("timestamp")) != str(
                    override_audit_timestamp
                ):
                    return 0
""",
        """                if False:
                    return 0
""",
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
