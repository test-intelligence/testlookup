"""Prove durable AI-summary retries re-evaluate current review authority."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "backend/tests/services/test_run_downstream_outbox_service.py::test_notification_retry_rechecks_review_before_sending_accepted_narrative",
    "backend/tests/test_notification_distribution_gates.py::test_the_task_withholds_the_ai_summary_from_preferences_and_digests",
    "backend/tests/test_notification_distribution_gates.py::test_notification_relay_refreshes_terminal_and_accepted_review_content",
]
MUTATIONS = [
    (
        ROOT / "backend/app/services/notification/manager.py",
        "relay-skips-review-refresh",
        """        for row in review_gated_rows:\n            delivery_content[row.id] = await _refresh_review_gated_delivery(db, row)\n""",
        """        for row in review_gated_rows:\n            delivery_content[row.id] = (\n                row.title, row.body, dict(row.delivery_metadata or {}), None\n            )\n""",
    ),
    (
        ROOT / "backend/app/services/notification/manager.py",
        "refresh-uses-stale-staged-body",
        """    original_summary = str(context.get(\"original_summary\") or \"\")\n""",
        """    original_summary = row.body\n""",
    ),
    (
        ROOT / "backend/app/services/notification/manager.py",
        "withheld-prefix-invents-release-signal",
        '            "withheld_body_prefix": _withheld_body_prefix(),\n',
        '            "withheld_body_prefix": _body_prefix(None),\n',
    ),
    (
        ROOT / "backend/app/services/notification/manager.py",
        "successful-history-keeps-stale-body",
        '                    "body": sent_body,\n',
        '                    "body": row.body,\n',
    ),
    (
        ROOT / "backend/app/services/notification/manager.py",
        "successful-delivery-skips-audit",
        "                if distribution_decision is not None:\n",
        "                if False and distribution_decision is not None:\n",
    ),
    (
        ROOT / "backend/app/worker/tasks.py",
        "digest-drops-review-context",
        """                                    _REVIEW_GATE_METADATA_KEY: {\n""",
        """                                    \"_removed_review_gate_v1\": {\n""",
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
            f".pytest-tmp-exploratory-m08-notification-{label}",
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
    print(f"M08 notification-replay mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
