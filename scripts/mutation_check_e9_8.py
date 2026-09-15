"""Prove E9.8's rule probes kill every scorer mutation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
COMMAND = (
    str(PYTHON),
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=backend/.pytest-tmp-e98-mutation",
    "backend/tests/services/test_eval_scorer_mutations.py",
)

# Each replacement disables or reverses one independent scoring rule.  The
# harness asserts the source fragment occurs exactly once before running tests,
# so a source refactor cannot turn this into a false-green no-op.
MUTATIONS = (
    (
        "backend/app/services/agent_eval_harness.py",
        "if not (0 <= s.confidence_score <= 100):",
        "if False:",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        'if s.verdict == "flaky" and s.confidence_score == 0:',
        "if False:",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "if s.confidence_score > 0 and not s.decision_reason:",
        "if False:",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "s.confidence_score >= CONF_EVIDENCE_FLOOR",
        "s.confidence_score > CONF_EVIDENCE_FLOOR",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "s.evidence_count >= 1",
        "s.evidence_count >= 2",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "non_flaky = [s for s in samples if s.verdict in NON_FLAKY_VERDICTS]",
        "non_flaky = [s for s in samples if False]",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "has_action = len(s.recommended_actions) >= 1",
        "has_action = len(s.recommended_actions) >= 0",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        'has_fix_token = "fix" in s.decision_reason.lower()',
        "has_fix_token = False",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "correct = sum(1 for s in samples if s.verdict == s.ground_truth_verdict)",
        "correct = sum(1 for s in samples if s.verdict != s.ground_truth_verdict)",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "outcomes.append(1 if s.verdict == s.ground_truth_verdict else 0)",
        "outcomes.append(1 if s.verdict != s.ground_truth_verdict else 0)",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n",
        "brier = sum((p - o) for p, o in zip(probs, outcomes)) / n",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "b = min(ECE_BINS - 1, int(p * ECE_BINS))",
        "b = min(ECE_BINS - 2, int(p * ECE_BINS))",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "ece += weight * abs(acc_b - conf_b)",
        "ece += abs(acc_b - conf_b)",
    ),
    (
        "backend/app/services/agent_eval_harness.py",
        "passed = all(per_metric_pass.values()) and sample_count >= MIN_SAMPLES",
        "passed = all(per_metric_pass.values())",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        'correct = sum(1 for item in items if item.get("expected_output", {}).get("correct", False))',
        "correct = len(items)",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        'categories.add(item.get("expected_output", {}).get("failure_category", "UNKNOWN"))',
        'categories.add(item.get("input", {}).get("failure_category", "UNKNOWN"))',
    ),
    (
        "backend/app/services/ai_eval_service.py",
        'and not item.get("expected_output", {}).get("correct", False))',
        "and False)",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        'and item.get("input", {}).get("failure_category") != cat)',
        'and item.get("input", {}).get("failure_category") == cat)',
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "correct = sum(1 for predicted, expected in pairs if predicted == expected)",
        "correct = sum(1 for predicted, expected in pairs if predicted != expected)",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "fp = sum(1 for p, e in pairs if p == kind and e != kind)",
        "fp = sum(1 for p, e in pairs if p == kind and e == kind)",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        'has_rc = bool(inp.get("root_cause_summary") or inp.get("error_message"))',
        "has_rc = True",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "if str(expected_cat).upper() == str(actual_cat).upper():",
        "if str(expected_cat).upper() != str(actual_cat).upper():",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)",
        "overlap = len(words_a | words_b) / max(len(words_a | words_b), 1)",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "predicted_dup = overlap >= threshold and comp_a == comp_b",
        "predicted_dup = overlap <= threshold and comp_a == comp_b",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "predicted_dup = overlap >= threshold and comp_a == comp_b",
        "predicted_dup = overlap >= threshold and True",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "if risk_score < 20:",
        "if risk_score <= 20:",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "elif risk_score >= 55:",
        "elif risk_score > 55:",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "if predicted == expected_rec:",
        "if predicted != expected_rec:",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "fn = dispatch.get(task_type, compute_classification_metrics)",
        "fn = dispatch.get(task_type, compute_release_decision_metrics)",
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad in MUTATIONS:
        path = ROOT / relative
        original = path.read_bytes()
        good_bytes = good.encode("utf-8")
        bad_bytes = bad.encode("utf-8")
        if original.count(good_bytes) != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {relative}: {good!r}"
            )
        path.write_bytes(original.replace(good_bytes, bad_bytes))
        try:
            run = subprocess.run(
                COMMAND,
                cwd=ROOT,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {relative}: {bad!r}")
        finally:
            path.write_bytes(original)
    print(f"E9.8 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
