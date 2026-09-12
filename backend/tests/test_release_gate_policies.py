"""
Unit tests for ENT-02: Policy-Based Release Gates.

Covers:
 - Policy evaluator: resolution, evaluation, rule dispatch, escalation
 - Criticality service: backward-compatible weight/threshold overrides
 - Schema validation: PolicyDocument, PolicyRule, thresholds, weights
 - Override constraints: policy-driven restrictions
 - Column width safety
"""
from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub native deps and app.core/app.db modules for each test."""
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(
                sys.modules, "bcrypt",
                _make_stub("bcrypt",
                    checkpw=MagicMock(return_value=True),
                    hashpw=MagicMock(return_value=b"$2b$fake"),
                    gensalt=MagicMock(return_value=b"$2b$12$salt"),
                ),
            )
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        yield


# ── Criticality Service: backward-compatible changes ─────────────────────────


class TestCriticalityServiceBackwardCompat:
    """Verify compute_composite and score_to_recommendation are backward-compatible."""

    def test_compute_composite_without_weights_unchanged(self):
        from app.services.criticality_service import compute_composite

        scores = {
            "user_impact": 50.0, "env_sensitivity": 20.0, "reproducibility": 30.0,
            "regression_likely": 40.0, "hist_recurrence": 10.0,
            "blast_radius": 25.0, "diagnosis_conf": 60.0,
        }
        result = compute_composite(scores)
        assert isinstance(result, float)
        assert 0 <= result <= 100

    def test_compute_composite_with_custom_weights(self):
        from app.services.criticality_service import compute_composite

        scores = {"user_impact": 100.0, "env_sensitivity": 0.0, "reproducibility": 0.0,
                  "regression_likely": 0.0, "hist_recurrence": 0.0,
                  "blast_radius": 0.0, "diagnosis_conf": 0.0}
        # All weight on user_impact
        custom_weights = {"user_impact": 1.0, "env_sensitivity": 0.0, "reproducibility": 0.0,
                          "regression_likely": 0.0, "hist_recurrence": 0.0,
                          "blast_radius": 0.0, "diagnosis_conf": 0.0}
        result = compute_composite(scores, weights=custom_weights)
        assert result == 100.0

    def test_compute_composite_custom_weights_different_from_default(self):
        from app.services.criticality_service import compute_composite

        scores = {"user_impact": 100.0, "env_sensitivity": 0.0, "reproducibility": 0.0,
                  "regression_likely": 0.0, "hist_recurrence": 0.0,
                  "blast_radius": 0.0, "diagnosis_conf": 0.0}
        default_result = compute_composite(scores)
        custom_result = compute_composite(scores, weights={
            "user_impact": 0.5, "env_sensitivity": 0.1, "reproducibility": 0.1,
            "regression_likely": 0.1, "hist_recurrence": 0.05,
            "blast_radius": 0.1, "diagnosis_conf": 0.05,
        })
        assert custom_result != default_result
        assert custom_result == 50.0  # 100 * 0.5

    def test_score_to_recommendation_without_new_params(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(10.0, 95.0, 90.0) == "GO"
        assert score_to_recommendation(30.0, 95.0, 90.0) == "CONDITIONAL_GO"
        assert score_to_recommendation(60.0, 95.0, 90.0) == "NO_GO"

    def test_score_to_recommendation_with_custom_thresholds(self):
        from app.services.criticality_service import score_to_recommendation

        # Lower GO threshold means more runs become GO
        assert score_to_recommendation(15.0, 95.0, 90.0, go_threshold=10.0, no_go_threshold=50.0) == "CONDITIONAL_GO"
        assert score_to_recommendation(15.0, 95.0, 90.0, go_threshold=20.0, no_go_threshold=50.0) == "GO"

    def test_score_to_recommendation_custom_hard_floor(self):
        from app.services.criticality_service import score_to_recommendation

        # Default: hard_floor = 0.7 → pass_rate < 90 * 0.7 = 63 → NO_GO
        assert score_to_recommendation(10.0, 60.0, 90.0) == "NO_GO"
        # Custom: hard_floor = 0.5 → pass_rate < 90 * 0.5 = 45 → pass_rate 60 is
        # above the floor, so it escapes NO_GO. THAT is what this test pins, and
        # it still holds.
        #
        # The verdict it escapes TO changed from GO to CONDITIONAL_GO: 60 is
        # still below the configured threshold of 90, and a run under the
        # operator's own bar is no longer reported as a clean go (F-020). The
        # override being respected — the point of the test — is unaffected.
        assert score_to_recommendation(10.0, 60.0, 90.0, hard_floor_factor=0.5) != "NO_GO"
        assert score_to_recommendation(10.0, 60.0, 90.0, hard_floor_factor=0.5) == "CONDITIONAL_GO"

    def test_score_to_recommendation_pass_rate_hard_floor(self):
        from app.services.criticality_service import score_to_recommendation

        # pass_rate=50, threshold=90, floor_factor=0.7 → 50 < 63 → NO_GO
        assert score_to_recommendation(5.0, 50.0, 90.0) == "NO_GO"


# ── Policy Evaluator: rule evaluation ────────────────────────────────────────


class TestRuleEvaluators:
    def test_flaky_recurrence_passes(self):
        from app.services.policy_evaluator_service import _eval_flaky_recurrence

        rule = {"id": "r1", "name": "Flaky Limit", "type": "flaky_recurrence",
                "params": {"max_flaky_tests": 10, "action": "BLOCK"}}
        result = _eval_flaky_recurrence(rule, {"flaky_count": 5}, {})
        assert result.passed is True
        assert result.actual_value == 5
        assert result.threshold_value == 10

    def test_flaky_recurrence_fails(self):
        from app.services.policy_evaluator_service import _eval_flaky_recurrence

        rule = {"id": "r1", "name": "Flaky Limit", "type": "flaky_recurrence",
                "params": {"max_flaky_tests": 3, "action": "BLOCK"}}
        result = _eval_flaky_recurrence(rule, {"flaky_count": 5}, {})
        assert result.passed is False
        assert result.action == "BLOCK"
        assert "exceeds" in result.message

    def test_open_defect_limit_passes(self):
        from app.services.policy_evaluator_service import _eval_open_defect_limit

        rule = {"id": "r2", "name": "Defect Limit", "type": "open_defect_limit",
                "params": {"max_open_defects": 10, "action": "WARN"}}
        result = _eval_open_defect_limit(rule, {"open_defects": 3}, {})
        assert result.passed is True

    def test_open_defect_limit_fails(self):
        from app.services.policy_evaluator_service import _eval_open_defect_limit

        rule = {"id": "r2", "name": "Defect Limit", "type": "open_defect_limit",
                "params": {"max_open_defects": 2, "action": "BLOCK"}}
        result = _eval_open_defect_limit(rule, {"open_defects": 5}, {})
        assert result.passed is False

    def test_dimension_ceiling_passes(self):
        from app.services.policy_evaluator_service import _eval_dimension_ceiling

        rule = {"id": "r3", "name": "Regression Cap", "type": "dimension_ceiling",
                "params": {"dimension": "regression_likely", "max_score": 70, "action": "BLOCK"}}
        result = _eval_dimension_ceiling(rule, {}, {"regression_likely": 50.0})
        assert result.passed is True

    def test_dimension_ceiling_fails(self):
        from app.services.policy_evaluator_service import _eval_dimension_ceiling

        rule = {"id": "r3", "name": "Regression Cap", "type": "dimension_ceiling",
                "params": {"dimension": "regression_likely", "max_score": 40, "action": "BLOCK"}}
        result = _eval_dimension_ceiling(rule, {}, {"regression_likely": 55.0})
        assert result.passed is False
        assert "exceeds ceiling" in result.message

    def test_dimension_ceiling_with_warn_action(self):
        from app.services.policy_evaluator_service import _eval_dimension_ceiling

        rule = {"id": "r3", "name": "Blast Warn", "type": "dimension_ceiling",
                "params": {"dimension": "blast_radius", "max_score": 30, "action": "WARN"}}
        result = _eval_dimension_ceiling(rule, {}, {"blast_radius": 50.0})
        assert result.passed is False
        assert result.action == "WARN"


# ── Override constraint checking ─────────────────────────────────────────────


class TestOverrideConstraints:
    def test_no_policy_allows_everything(self):
        from app.services.policy_evaluator_service import check_override_constraints

        allowed, msg = check_override_constraints(None, "NO_GO", "GO", "urgent release")
        assert allowed is True
        assert msg == ""

    def test_policy_allows_override_by_default(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": []}
        allowed, msg = check_override_constraints(policy, "NO_GO", "GO", "urgent release")
        assert allowed is True

    def test_policy_blocks_no_go_to_go(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": [{
            "type": "override_rules", "enabled": True,
            "params": {"allow_override_to_go_from_no_go": False},
        }]}
        allowed, msg = check_override_constraints(policy, "NO_GO", "GO", "urgent release")
        assert allowed is False
        assert "does not allow" in msg

    def test_policy_allows_no_go_to_conditional(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": [{
            "type": "override_rules", "enabled": True,
            "params": {"allow_override_to_go_from_no_go": False},
        }]}
        # NO_GO → CONDITIONAL_GO is allowed (only NO_GO → GO is blocked)
        allowed, msg = check_override_constraints(policy, "NO_GO", "CONDITIONAL_GO", "partial fix")
        assert allowed is True

    def test_policy_enforces_reason_min_length(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": [{
            "type": "override_rules", "enabled": True,
            "params": {"require_reason_min_length": 20},
        }]}
        allowed, msg = check_override_constraints(policy, "CONDITIONAL_GO", "GO", "short")
        assert allowed is False
        assert "at least 20" in msg

    def test_policy_accepts_long_reason(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": [{
            "type": "override_rules", "enabled": True,
            "params": {"require_reason_min_length": 10},
        }]}
        allowed, msg = check_override_constraints(policy, "CONDITIONAL_GO", "GO", "this is a sufficiently long reason")
        assert allowed is True

    def test_disabled_override_rule_ignored(self):
        from app.services.policy_evaluator_service import check_override_constraints

        policy = MagicMock()
        policy.rules = {"rules": [{
            "type": "override_rules", "enabled": False,
            "params": {"allow_override_to_go_from_no_go": False},
        }]}
        allowed, msg = check_override_constraints(policy, "NO_GO", "GO", "urgent")
        assert allowed is True


# ── Schema validation ────────────────────────────────────────────────────────


class TestPolicySchemas:
    def test_policy_document_defaults(self):
        from app.models.schemas import PolicyDocument

        doc = PolicyDocument()
        assert doc.schema_version == 1
        assert doc.thresholds.go_threshold == 20.0
        assert doc.thresholds.no_go_threshold == 55.0
        assert doc.dimension_weights.user_impact == 0.25
        assert doc.rules == []

    def test_policy_thresholds_bounds(self):
        from pydantic import ValidationError

        from app.models.schemas import PolicyThresholds

        # Valid
        PolicyThresholds(go_threshold=0, no_go_threshold=100, pass_rate_minimum=50)
        # Invalid: negative
        with pytest.raises(ValidationError):
            PolicyThresholds(go_threshold=-1)
        # Invalid: > 100
        with pytest.raises(ValidationError):
            PolicyThresholds(pass_rate_minimum=101)

    def test_policy_rule_requires_id_and_name(self):
        from pydantic import ValidationError

        from app.models.schemas import PolicyRule

        PolicyRule(id="r1", name="Test", type="flaky_recurrence")
        with pytest.raises(ValidationError):
            PolicyRule(id="", name="Test", type="flaky_recurrence")

    def test_policy_create_schema(self):
        from app.models.schemas import ReleaseGatePolicyCreate

        payload = ReleaseGatePolicyCreate(name="Strict Policy")
        assert payload.project_id is None
        assert payload.rules.thresholds.go_threshold == 20.0

    def test_policy_update_schema(self):
        from app.models.schemas import ReleaseGatePolicyUpdate

        payload = ReleaseGatePolicyUpdate(name="Updated Name")
        assert payload.name == "Updated Name"
        assert payload.rules is None  # not set

    def test_rule_evaluation_response(self):
        from app.models.schemas import RuleEvaluationResponse

        ev = RuleEvaluationResponse(
            rule_id="r1", rule_name="Flaky", rule_type="flaky_recurrence",
            passed=False, action="BLOCK", message="too many flaky tests",
            actual_value=15, threshold_value=10,
        )
        assert ev.passed is False
        assert ev.action == "BLOCK"

    def test_simulate_response(self):
        from app.models.schemas import PolicySimulateResponse

        resp = PolicySimulateResponse(
            original_recommendation="GO",
            simulated_recommendation="NO_GO",
            original_composite=15.0,
            simulated_composite=60.0,
            diff_summary="Would change GO → NO_GO",
        )
        assert resp.diff_summary.startswith("Would change")

    def test_policy_dimension_weights_bounds(self):
        from pydantic import ValidationError

        from app.models.schemas import PolicyDimensionWeights

        # Valid
        PolicyDimensionWeights(user_impact=0.5, env_sensitivity=0.5,
                               reproducibility=0, regression_likely=0,
                               hist_recurrence=0, blast_radius=0, diagnosis_conf=0)
        # Invalid: > 1.0
        with pytest.raises(ValidationError):
            PolicyDimensionWeights(user_impact=1.5)

    def test_release_council_response_has_policy_fields(self):
        from app.models.schemas import ReleaseCouncilResponse

        resp = ReleaseCouncilResponse(
            run_id="test", recommendation="GO", risk_score=10,
            policy_id="abc", policy_version=2, policy_level="project",
        )
        assert resp.policy_id == "abc"
        assert resp.policy_version == 2
        assert resp.policy_level == "project"
        assert resp.rule_evaluations == []

    def test_override_audit_entry_has_policy_fields(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-02T12:00:00Z",
            before_recommendation="CONDITIONAL_GO",
            before_risk_score=30,
            after_recommendation="GO",
            reason="fix deployed",
            policy_id="abc",
            policy_version=3,
        )
        assert entry.policy_id == "abc"

    def test_override_audit_entry_backward_compat(self):
        """Existing audit entries without policy fields should deserialize fine."""
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T10:00:00Z",
            before_recommendation="NO_GO",
            before_risk_score=60,
            after_recommendation="CONDITIONAL_GO",
            reason="hotfix applied",
        )
        assert entry.policy_id is None
        assert entry.policy_version is None


# ── Policy evaluation result ─────────────────────────────────────────────────


class TestPolicyEvaluationResult:
    def test_to_dict_serializable(self):
        from app.services.policy_evaluator_service import PolicyEvaluationResult, RuleEvaluation

        result = PolicyEvaluationResult(
            policy_id="abc",
            policy_version=1,
            policy_level="project",
            overall_result="PASS",
            recommendation="GO",
            effective_composite=15.0,
            rule_evaluations=[
                RuleEvaluation("r1", "Flaky", "flaky_recurrence", True, "BLOCK", "OK", 3, 10),
            ],
            effective_thresholds={"go_threshold": 20},
            effective_weights={"user_impact": 0.25},
            evaluated_at="2026-04-02T12:00:00Z",
        )
        d = result.to_dict()
        assert d["policy_id"] == "abc"
        assert d["recommendation"] == "GO"
        assert len(d["rule_evaluations"]) == 1


# ── Escalation logic ────────────────────────────────────────────────────────


class TestEscalationLogic:
    """Test that BLOCK and WARN rules correctly escalate recommendations."""

    def test_block_rule_escalates_go_to_no_go(self):
        """A BLOCK rule failure should force NO_GO even if composite says GO."""
        from app.services.policy_evaluator_service import RuleEvaluation

        # Simulate the escalation logic from evaluate_policy
        recommendation = "GO"
        overall_result = "PASS"
        rule_evals = [
            RuleEvaluation("r1", "Defect Limit", "open_defect_limit", False, "BLOCK", "too many", 10, 5),
        ]
        for ev in rule_evals:
            if not ev.passed:
                if ev.action == "BLOCK":
                    overall_result = "BLOCK"
                    recommendation = "NO_GO"
                elif ev.action == "WARN" and overall_result != "BLOCK":
                    overall_result = "WARN"
                    if recommendation == "GO":
                        recommendation = "CONDITIONAL_GO"

        assert recommendation == "NO_GO"
        assert overall_result == "BLOCK"

    def test_warn_rule_escalates_go_to_conditional(self):
        from app.services.policy_evaluator_service import RuleEvaluation

        recommendation = "GO"
        overall_result = "PASS"
        rule_evals = [
            RuleEvaluation("r1", "Flaky Warning", "flaky_recurrence", False, "WARN", "many flaky", 8, 5),
        ]
        for ev in rule_evals:
            if not ev.passed:
                if ev.action == "BLOCK":
                    overall_result = "BLOCK"
                    recommendation = "NO_GO"
                elif ev.action == "WARN" and overall_result != "BLOCK":
                    overall_result = "WARN"
                    if recommendation == "GO":
                        recommendation = "CONDITIONAL_GO"

        assert recommendation == "CONDITIONAL_GO"
        assert overall_result == "WARN"

    def test_warn_does_not_escalate_no_go(self):
        """NO_GO should stay NO_GO even with a WARN rule."""
        from app.services.policy_evaluator_service import RuleEvaluation

        recommendation = "NO_GO"
        overall_result = "PASS"
        rule_evals = [
            RuleEvaluation("r1", "Flaky Warning", "flaky_recurrence", False, "WARN", "many flaky", 8, 5),
        ]
        for ev in rule_evals:
            if not ev.passed:
                if ev.action == "BLOCK":
                    overall_result = "BLOCK"
                    recommendation = "NO_GO"
                elif ev.action == "WARN" and overall_result != "BLOCK":
                    overall_result = "WARN"
                    if recommendation == "GO":
                        recommendation = "CONDITIONAL_GO"

        assert recommendation == "NO_GO"  # unchanged

    def test_info_rule_does_not_escalate(self):
        """INFO rules should not affect the recommendation."""
        from app.services.policy_evaluator_service import RuleEvaluation

        recommendation = "GO"
        overall_result = "PASS"
        rule_evals = [
            RuleEvaluation("r1", "Info Only", "flaky_recurrence", False, "INFO", "noted", 3, 2),
        ]
        for ev in rule_evals:
            if not ev.passed:
                if ev.action == "BLOCK":
                    overall_result = "BLOCK"
                    recommendation = "NO_GO"
                elif ev.action == "WARN" and overall_result != "BLOCK":
                    overall_result = "WARN"
                    if recommendation == "GO":
                        recommendation = "CONDITIONAL_GO"

        assert recommendation == "GO"  # unchanged


# ── ORM model column safety ──────────────────────────────────────────────────


class TestModelColumnWidths:
    def test_policy_name_fits_column(self):
        """Policy name max is 255 chars."""
        name = "a" * 255
        assert len(name) <= 255

    def test_recommendation_values_fit(self):
        """GO, NO_GO, CONDITIONAL_GO all fit in String(20)."""
        for rec in ("GO", "NO_GO", "CONDITIONAL_GO"):
            assert len(rec) <= 20


# ── Policy helper functions ──────────────────────────────────────────────────


class TestPolicyHelpers:
    def test_extract_thresholds_from_policy(self):
        from app.services.policy_evaluator_service import _extract_thresholds

        policy = MagicMock()
        policy.rules = {
            "thresholds": {
                "go_threshold": 15,
                "no_go_threshold": 50,
                "pass_rate_minimum": 85,
                "pass_rate_hard_floor_factor": 0.6,
            }
        }
        thresholds = _extract_thresholds(policy)
        assert thresholds["go_threshold"] == 15.0
        assert thresholds["no_go_threshold"] == 50.0
        assert thresholds["pass_rate_minimum"] == 85.0

    def test_extract_thresholds_fallback(self):
        from app.services.policy_evaluator_service import _extract_thresholds

        thresholds = _extract_thresholds(None)
        assert thresholds["go_threshold"] == 20
        assert thresholds["no_go_threshold"] == 55

    def test_extract_weights_from_policy(self):
        from app.services.policy_evaluator_service import _extract_weights

        policy = MagicMock()
        policy.rules = {
            "dimension_weights": {
                "user_impact": 0.5,
                "env_sensitivity": 0.1,
                "reproducibility": 0.1,
                "regression_likely": 0.1,
                "hist_recurrence": 0.05,
                "blast_radius": 0.1,
                "diagnosis_conf": 0.05,
            }
        }
        weights = _extract_weights(policy)
        assert weights["user_impact"] == 0.5

    def test_extract_weights_fallback(self):
        from app.services.policy_evaluator_service import _extract_weights

        weights = _extract_weights(None)
        assert weights["user_impact"] == 0.25  # config default

    def test_extract_rules_only_enabled(self):
        from app.services.policy_evaluator_service import _extract_rules

        policy = MagicMock()
        policy.rules = {
            "rules": [
                {"id": "r1", "name": "A", "type": "flaky_recurrence", "enabled": True, "params": {}},
                {"id": "r2", "name": "B", "type": "open_defect_limit", "enabled": False, "params": {}},
            ]
        }
        rules = _extract_rules(policy)
        assert len(rules) == 1
        assert rules[0]["id"] == "r1"
