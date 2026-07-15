"""Hypothesis sub-agent evidence matrix + synthesis precedence (AI-1).

Pure-function tests: every scenario drives the deterministic
``evaluate(bundle)`` classmethods and the synthesis precedence rules with
seeded fixture bundles — no DB, no graph, no LLM (offline-deterministic by
construction). Pins:

* the five fixture scenarios required by the plan (pure-infra run,
  commit-onset regression, env-drift, all-flaky, mixed → unknown),
* the validation thresholds documented in ``hypotheses.py``'s evidence
  matrix,
* the synthesis precedence rules (highest validated confidence, the
  cross-family conflict margin → unknown, same-family tie-breaks),
* the recommended-actions mapping (text only — never actions taken).
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.investigator.hypotheses import (  # noqa: E402
    CommitHypothesisAgent,
    EnvironmentHypothesisAgent,
    InfraHypothesisAgent,
    KnownFlakyHypothesisAgent,
    RegressionHypothesisAgent,
)
from app.agents.investigator.synthesis import (  # noqa: E402
    CONFLICT_MARGIN,
    RECOMMENDED_ACTIONS,
    UNKNOWN_CONFIDENCE,
    deterministic_narrative,
    pick_primary_cause,
)
from app.services.agent_investigation_service import HYPOTHESIS_IDS  # noqa: E402


# ─────────────────────────── Fixture bundles ───────────────────────────


def _failure(fp, name, status="FAILED", category=None, suite="checkout"):
    return {
        "fingerprint": fp,
        "test_name": name,
        "suite_name": suite,
        "status": status,
        "category": category,
    }


def _run(**over):
    base = {
        "id": "run-1",
        "build_number": "b-100",
        "branch": "feature/x",
        "commit_hash": "aaaa1111aaaa1111",
        "ci_repo": "org/repo",
        "pr_number": 42,
        "ci_run_url": "https://ci.example/run/1",
        "duration_ms": 60_000,
        "total_tests": 100,
        "failed_tests": 8,
        "broken_tests": 0,
        "ocp_namespace": "qa-1",
        "ocp_node": "node-a",
        "suite_count": 4,
    }
    base.update(over)
    return base


def _baseline(**over):
    base = _run(
        id="run-0", build_number="b-099", branch="main",
        commit_hash="bbbb2222bbbb2222", duration_ms=58_000,
    )
    base.update(over)
    return base


def pure_infra_bundle():
    """Scenario 1: BROKEN-heavy run, infra rule-pack matches, wide blast."""
    failures = [
        _failure(f"fp{i}", f"test_{i}", status="BROKEN", category="INFRASTRUCTURE",
                 suite=f"suite{i % 4}")
        for i in range(8)
    ]
    return {
        "run": _run(broken_tests=8, commit_hash="bbbb2222bbbb2222"),
        "baseline": _baseline(),
        "compare": {"new_failures": 0, "fixed": 0, "still_failing": 8,
                    "newly_failed_tests": []},
        "failures": failures,
        "failure_counts": {"total": 8, "failed": 0, "broken": 8,
                           "distinct_failing_suites": 4, "total_suites": 4},
        "infra_keyword_matches": 6,
        "infra_rule_hits": [{"rule_id": "pattern.timeout", "test_name": "test_0"}],
        "flaky_fingerprints": [],
        "clusters": [],
        "recall_lines": {},
    }


def commit_onset_bundle():
    """Scenario 2: commit differs, newly-failed concentration 100%."""
    newly = [_failure(f"fp{i}", f"test_{i}") for i in range(6)]
    return {
        "run": _run(),
        "baseline": _baseline(),
        "compare": {"new_failures": 6, "fixed": 0, "still_failing": 0,
                    "newly_failed_tests": newly},
        "failures": newly,
        "failure_counts": {"total": 6, "failed": 6, "broken": 0,
                           "distinct_failing_suites": 1, "total_suites": 4},
        "infra_keyword_matches": 0,
        "infra_rule_hits": [],
        "flaky_fingerprints": [],
        "clusters": [{"cluster_id": "cl_1", "label": "NullPointer in checkout", "size": 5}],
        "recall_lines": {},
    }


def env_drift_bundle():
    """Scenario 3: same commit, ocp fields drifted, 2.5x slowdown."""
    failures = [_failure(f"fp{i}", f"test_{i}") for i in range(4)]
    return {
        "run": _run(commit_hash="bbbb2222bbbb2222", ocp_namespace="qa-2",
                    ocp_node="node-z", duration_ms=150_000),
        "baseline": _baseline(),
        "compare": {"new_failures": 1, "fixed": 0, "still_failing": 3,
                    "newly_failed_tests": [failures[0]]},
        "failures": failures,
        "failure_counts": {"total": 4, "failed": 4, "broken": 0,
                           "distinct_failing_suites": 1, "total_suites": 4},
        "infra_keyword_matches": 0,
        "infra_rule_hits": [],
        "flaky_fingerprints": [],
        "clusters": [],
        "recall_lines": {},
    }


def all_flaky_bundle():
    """Scenario 4: every newly-failed test is in the known-flaky set."""
    newly = [_failure(f"fp{i}", f"test_{i}") for i in range(5)]
    return {
        "run": _run(commit_hash="bbbb2222bbbb2222"),
        "baseline": _baseline(),
        "compare": {"new_failures": 5, "fixed": 0, "still_failing": 0,
                    "newly_failed_tests": newly},
        "failures": newly,
        "failure_counts": {"total": 5, "failed": 5, "broken": 0,
                           "distinct_failing_suites": 2, "total_suites": 4},
        "infra_keyword_matches": 0,
        "infra_rule_hits": [],
        "flaky_fingerprints": [f"fp{i}" for i in range(5)],
        "clusters": [],
        "recall_lines": {"fp0": ["2026-06-14: human corrected to FLAKY [HUMAN CORRECTION — authoritative]"]},
        }


def mixed_conflict_bundle():
    """Scenario 5: strong infra AND a clean regression core at once —
    cross-family conflict that must resolve to unknown."""
    broken = [
        _failure(f"ifp{i}", f"infra_test_{i}", status="BROKEN",
                 category="INFRASTRUCTURE", suite=f"s{i % 4}")
        for i in range(5)
    ]
    newly = [_failure(f"rfp{i}", f"reg_test_{i}") for i in range(5)]
    return {
        "run": _run(broken_tests=5, failed_tests=10),
        "baseline": _baseline(),
        "compare": {"new_failures": 5, "fixed": 0, "still_failing": 5,
                    "newly_failed_tests": newly},
        "failures": broken + newly,
        "failure_counts": {"total": 10, "failed": 5, "broken": 5,
                           "distinct_failing_suites": 4, "total_suites": 4},
        "infra_keyword_matches": 5,
        "infra_rule_hits": [{"rule_id": "pattern.oom", "test_name": "infra_test_0"}],
        "flaky_fingerprints": [],
        "clusters": [{"cluster_id": "cl_9", "label": "checkout regression", "size": 4}],
        "recall_lines": {},
    }


def _evaluate_all(bundle):
    return {
        "infra": InfraHypothesisAgent.evaluate(bundle),
        "commit": CommitHypothesisAgent.evaluate(bundle),
        "environment": EnvironmentHypothesisAgent.evaluate(bundle),
        "known_flaky": KnownFlakyHypothesisAgent.evaluate(bundle),
        "regression": RegressionHypothesisAgent.evaluate(bundle),
    }


def _as_hypotheses(results):
    return [
        {"id": hyp_id, "status": r["status"], "confidence": r["confidence"],
         "summary": r["summary"], "title": hyp_id}
        for hyp_id, r in results.items()
    ]


# ─────────────────────────── Scenario 1: pure infra ───────────────────────────


def test_pure_infra_scenario():
    results = _evaluate_all(pure_infra_bundle())
    assert results["infra"]["status"] == "validated"
    assert results["infra"]["confidence"] >= 80
    # Same commit as baseline → commit invalidated.
    assert results["commit"]["status"] == "invalidated"
    # No newly-failed tests → regression invalidated.
    assert results["regression"]["status"] == "invalidated"
    assert results["known_flaky"]["status"] == "invalidated"

    cause, confidence, note = pick_primary_cause(_as_hypotheses(results))
    assert cause == "infra"
    assert confidence == results["infra"]["confidence"]
    assert note is None


def test_infra_evidence_cites_rule_pack_and_blast_radius():
    result = InfraHypothesisAgent.evaluate(pure_infra_bundle())
    kinds = {e["kind"] for e in result["evidence"]}
    assert "rule_pack" in kinds
    assert "blast_radius" in kinds
    # Every evidence item carries the pinned wire keys.
    for item in result["evidence"]:
        assert set(item.keys()) == {"kind", "label", "url_path", "detail"}


def test_infra_no_failures_is_invalidated():
    result = InfraHypothesisAgent.evaluate({"failure_counts": {"total": 0}})
    assert result["status"] == "invalidated"


# ─────────────────────── Scenario 2: commit-onset regression ──────────────────


def test_commit_onset_scenario():
    results = _evaluate_all(commit_onset_bundle())
    assert results["commit"]["status"] == "validated"
    assert results["regression"]["status"] == "validated"
    assert results["infra"]["status"] == "invalidated"
    assert results["known_flaky"]["status"] == "invalidated"

    # commit and regression are the SAME family (code) — near-ties are not
    # conflicts; the family tie-break prefers the onset-aligned commit.
    cause, _confidence, note = pick_primary_cause(_as_hypotheses(results))
    assert cause in ("commit", "regression")
    assert note is None


def test_commit_evidence_states_no_git_access():
    result = CommitHypothesisAgent.evaluate(commit_onset_bundle())
    details = " ".join(e["detail"] for e in result["evidence"])
    assert "commit-range contents unavailable; onset alignment only" in details


def test_commit_without_baseline_is_inconclusive():
    result = CommitHypothesisAgent.evaluate({"run": _run(), "baseline": None})
    assert result["status"] == "inconclusive"


def test_commit_same_hash_is_invalidated():
    bundle = commit_onset_bundle()
    bundle["run"]["commit_hash"] = bundle["baseline"]["commit_hash"]
    result = CommitHypothesisAgent.evaluate(bundle)
    assert result["status"] == "invalidated"


# ─────────────────────────── Scenario 3: env drift ────────────────────────────


def test_env_drift_scenario():
    results = _evaluate_all(env_drift_bundle())
    assert results["environment"]["status"] == "validated"
    # Drifted fields are cited in the evidence.
    fields = " ".join(e["detail"] for e in results["environment"]["evidence"])
    assert "ocp_namespace" in fields and "ocp_node" in fields
    # Same commit → commit invalidated; env should win.
    cause, _conf, _note = pick_primary_cause(_as_hypotheses(results))
    assert cause == "environment"


def test_environment_branch_drift_alone_is_weak():
    bundle = env_drift_bundle()
    run = dict(bundle["run"], ocp_namespace="qa-1", ocp_node="node-a",
               duration_ms=59_000, branch="feature/x")
    result = EnvironmentHypothesisAgent.evaluate({**bundle, "run": run})
    assert result["status"] == "inconclusive"


def test_environment_no_baseline_is_inconclusive():
    result = EnvironmentHypothesisAgent.evaluate({"run": _run(), "baseline": None})
    assert result["status"] == "inconclusive"


# ─────────────────────────── Scenario 4: all flaky ────────────────────────────


def test_all_flaky_scenario():
    results = _evaluate_all(all_flaky_bundle())
    assert results["known_flaky"]["status"] == "validated"
    assert results["known_flaky"]["confidence"] >= 85
    # A fully-flaky newly-failed set leaves no regression core.
    assert results["regression"]["status"] == "invalidated"

    cause, _conf, _note = pick_primary_cause(_as_hypotheses(results))
    assert cause == "known_flaky"


def test_flaky_evidence_cites_memory_recall():
    result = KnownFlakyHypothesisAgent.evaluate(all_flaky_bundle())
    memory_items = [e for e in result["evidence"] if e["kind"] == "memory"]
    assert memory_items, "recall lines for top offenders must appear as evidence"
    assert "HUMAN CORRECTION" in memory_items[0]["detail"]


def test_flaky_partial_coverage_is_inconclusive():
    bundle = all_flaky_bundle()
    bundle["flaky_fingerprints"] = ["fp0", "fp1"]  # 2/5 = 40%
    result = KnownFlakyHypothesisAgent.evaluate(bundle)
    assert result["status"] == "inconclusive"


# ─────────────────────────── Scenario 5: mixed → unknown ──────────────────────


def test_mixed_conflict_scenario_resolves_to_unknown():
    results = _evaluate_all(mixed_conflict_bundle())
    assert results["infra"]["status"] == "validated"
    assert results["regression"]["status"] == "validated"
    # Force the confidences within the conflict margin to pin the rule.
    hyps = _as_hypotheses(results)
    for h in hyps:
        if h["id"] in ("infra", "regression"):
            h["confidence"] = 80
    cause, confidence, note = pick_primary_cause(hyps)
    assert cause == "unknown"
    assert confidence == UNKNOWN_CONFIDENCE
    assert note is not None and "Conflicting" in note

    narrative = deterministic_narrative("unknown", hyps, note, None)
    assert "Conflicting" in narrative


# ─────────────────────────── Synthesis precedence ─────────────────────────────


def _h(hyp_id, status, confidence):
    return {"id": hyp_id, "status": status, "confidence": confidence,
            "summary": f"{hyp_id} summary", "title": hyp_id}


def test_highest_validated_confidence_wins():
    cause, conf, note = pick_primary_cause([
        _h("infra", "validated", 60),
        _h("commit", "validated", 85),
        _h("environment", "invalidated", 90),
    ])
    assert cause == "commit" and conf == 85 and note is None


def test_cross_family_near_tie_is_unknown():
    cause, conf, note = pick_primary_cause([
        _h("infra", "validated", 80),
        _h("regression", "validated", 80 - CONFLICT_MARGIN + 1),
    ])
    assert cause == "unknown"
    assert note is not None


def test_cross_family_clear_gap_is_not_a_conflict():
    cause, _conf, note = pick_primary_cause([
        _h("infra", "validated", 85),
        _h("regression", "validated", 85 - CONFLICT_MARGIN),
    ])
    assert cause == "infra" and note is None


def test_same_family_tie_breaks_infra_over_environment():
    cause, _conf, note = pick_primary_cause([
        _h("environment", "validated", 70),
        _h("infra", "validated", 70),
    ])
    assert cause == "infra" and note is None


def test_same_family_tie_breaks_commit_over_regression():
    cause, _conf, note = pick_primary_cause([
        _h("regression", "validated", 75),
        _h("commit", "validated", 75),
    ])
    assert cause == "commit" and note is None


def test_no_validated_hypotheses_is_unknown():
    cause, conf, _note = pick_primary_cause([
        _h("infra", "invalidated", 65),
        _h("commit", "inconclusive", 30),
    ])
    assert cause == "unknown" and conf == UNKNOWN_CONFIDENCE


def test_recommended_actions_cover_every_cause_and_unknown():
    for cause in (*HYPOTHESIS_IDS, "unknown"):
        actions = RECOMMENDED_ACTIONS[cause]
        assert actions and all(isinstance(a, str) for a in actions)
    # Flaky verdict proposes the quarantine workflow (text only).
    assert any("quarantine" in a.lower() for a in RECOMMENDED_ACTIONS["known_flaky"])
    # Infra verdict points at the runbook.
    assert any("runbook" in a.lower() for a in RECOMMENDED_ACTIONS["infra"])


def test_budget_note_lands_in_deterministic_narrative():
    hyps = [_h("infra", "validated", 80)]
    narrative = deterministic_narrative(
        "infra", hyps, None,
        "Note: the investigation hit its budget — 2 hypothesis(es) were left inconclusive.",
    )
    assert "hit its budget" in narrative
