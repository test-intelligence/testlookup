"""Kind-aware release-gate policy — opt-in failure-kind weighting (US-9.3).

Pins the contract of ``PolicyKindRules`` / ``_apply_kind_rules`` /
``evaluate_policy``:

 - STRICTLY OPT-IN: a policy with the ``kind_rules`` block absent evaluates
   byte-identically to one with the block present but ``enabled: false``
   (and both match the plain threshold verdict) — the identical-when-disabled
   pin.
 - Budget matrix: under / at / over budget; kinds without a budget count in
   full; exceeding a budget restores full counting.
 - Hard rule: a NO_GO downgrades at most to CONDITIONAL_GO — never to GO —
   and GO / CONDITIONAL_GO base verdicts are never touched.
 - Product failures can never be excluded; unknown-kind failures count as
   product (conservative).
 - Decision trail: kind breakdown, which budget fired, and the
   counterfactual ("Would have been NO_GO; downgraded ...") are recorded.
 - Explicit BLOCK rules still force NO_GO over a kind-budget downgrade.
 - Schema: ``downgrade_to`` only admits CONDITIONAL_GO; JSON round-trip
   through the API request/response schemas preserves the block.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from types import SimpleNamespace
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

        m.setitem(sys.modules, "app.core.security", _make_stub(
            "app.core.security",
            verify_password=MagicMock(return_value=True),
            get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"}),
        ))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub(
            "app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(),
            AsyncSessionLocal=MagicMock(), Base=_Base,
        ))
        m.setitem(sys.modules, "app.db.mongo", _make_stub(
            "app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock(),
        ))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub(
            "app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock(),
        ))
        yield


# All seven dimensions maxed → composite 100 → base NO_GO with any weights.
_NO_GO_DIMS = {
    "user_impact": 100.0, "env_sensitivity": 100.0, "reproducibility": 100.0,
    "regression_likely": 100.0, "hist_recurrence": 100.0,
    "blast_radius": 100.0, "diagnosis_conf": 100.0,
}
_GO_DIMS = {k: 0.0 for k in _NO_GO_DIMS}


def _policy(rules_doc: dict) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), version=1, rules=rules_doc)


def _kind_rules(enabled: bool = True, infrastructure: dict | None = None, test_code: dict | None = None) -> dict:
    block: dict = {"enabled": enabled}
    if infrastructure is not None:
        block["infrastructure"] = infrastructure
    if test_code is not None:
        block["test_code"] = test_code
    return block


def _counts(product: int = 0, test_code: int = 0, infrastructure: int = 0, unknown: int = 0) -> dict:
    return {
        "product": product, "test_code": test_code,
        "infrastructure": infrastructure, "unknown": unknown,
    }


# ── Schema (Pydantic) ─────────────────────────────────────────────────────────


class TestKindRulesSchema:
    def test_default_is_disabled(self):
        from app.models.schemas import PolicyDocument

        doc = PolicyDocument()
        assert doc.kind_rules.enabled is False
        assert doc.kind_rules.infrastructure is None
        assert doc.kind_rules.test_code is None

    def test_absent_block_parses_as_disabled(self):
        """Pre-feature policy JSON (no kind_rules key) reads as disabled."""
        from app.models.schemas import PolicyDocument

        doc = PolicyDocument(**{"schema_version": 1, "rules": []})
        assert doc.kind_rules.enabled is False

    def test_downgrade_to_only_admits_conditional_go(self):
        """Hard rule made unrepresentable: never GO (and no other value)."""
        from pydantic import ValidationError

        from app.models.schemas import PolicyKindBudget

        assert PolicyKindBudget(max_failures=5).downgrade_to == "CONDITIONAL_GO"
        for bad in ("GO", "NO_GO", "go", ""):
            with pytest.raises(ValidationError):
                PolicyKindBudget(max_failures=5, downgrade_to=bad)

    def test_max_failures_non_negative(self):
        from pydantic import ValidationError

        from app.models.schemas import PolicyKindBudget

        with pytest.raises(ValidationError):
            PolicyKindBudget(max_failures=-1)

    def test_json_round_trip_through_api_schemas(self):
        """kind_rules survives create-payload parse → model_dump → re-parse,
        the same path the router uses to persist and serve policy JSON."""
        from app.models.schemas import PolicyDocument, ReleaseGatePolicyCreate

        payload = ReleaseGatePolicyCreate(
            name="Kind-aware policy",
            rules={
                "kind_rules": {
                    "enabled": True,
                    "infrastructure": {"max_failures": 5, "downgrade_to": "CONDITIONAL_GO"},
                },
            },
        )
        stored = payload.rules.model_dump()  # what create_policy persists
        assert stored["kind_rules"]["enabled"] is True
        assert stored["kind_rules"]["infrastructure"]["max_failures"] == 5
        assert stored["kind_rules"]["test_code"] is None

        reread = PolicyDocument(**stored)  # what update_policy re-validates
        assert reread.kind_rules.enabled is True
        assert reread.kind_rules.infrastructure.max_failures == 5
        assert reread.model_dump()["kind_rules"] == stored["kind_rules"]


# ── _apply_kind_rules matrix ─────────────────────────────────────────────────


class TestApplyKindRulesMatrix:
    def _apply(self, kind_rules: dict, counts: dict | None, base: str = "NO_GO"):
        from app.services.policy_evaluator_service import _apply_kind_rules

        context = {} if counts is None else {"failure_kind_counts": counts}
        return _apply_kind_rules(kind_rules, context, base)

    def test_under_budget_downgrades(self):
        rec, evals, breakdown, applied, cf = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=3),
        )
        assert rec == "CONDITIONAL_GO"
        assert applied is True
        assert breakdown == _counts(infrastructure=3)
        assert "Would have been NO_GO" in cf
        assert "3 infrastructure failure(s) <= budget 5" in cf

    def test_at_budget_downgrades(self):
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=5),
        )
        assert (rec, applied) == ("CONDITIONAL_GO", True)

    def test_over_budget_restores_full_counting(self):
        rec, evals, _, applied, cf = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=6),
        )
        assert (rec, applied, cf) == ("NO_GO", False, None)
        over = [e for e in evals if e.rule_id == "kind_budget_infrastructure"]
        assert len(over) == 1 and over[0].passed is False
        assert "exceed budget" in over[0].message

    def test_product_failures_can_never_be_excluded(self):
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(product=1, infrastructure=3),
        )
        assert (rec, applied) == ("NO_GO", False)

    def test_unknown_counts_as_product(self):
        """Conservative: an unclassified failure must never soften the gate."""
        rec, evals, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(unknown=1, infrastructure=3),
        )
        assert (rec, applied) == ("NO_GO", False)
        summary = [e for e in evals if e.rule_id == "kind_rules"][0]
        assert "unknown-kind counted as product" in summary.message

    def test_unbudgeted_test_code_counts_in_full(self):
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(test_code=2, infrastructure=3),
        )
        assert (rec, applied) == ("NO_GO", False)

    def test_both_kinds_budgeted_and_within(self):
        rec, _, _, applied, cf = self._apply(
            _kind_rules(
                infrastructure={"max_failures": 5},
                test_code={"max_failures": 4},
            ),
            _counts(test_code=2, infrastructure=3),
        )
        assert (rec, applied) == ("CONDITIONAL_GO", True)
        assert "3 infrastructure failure(s) <= budget 5" in cf
        assert "2 test_code failure(s) <= budget 4" in cf

    def test_never_downgrades_to_go(self):
        """Hard rule: even with everything excused, the result is exactly
        CONDITIONAL_GO — never GO."""
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 1000}),
            _counts(infrastructure=1),
        )
        assert rec == "CONDITIONAL_GO"
        assert rec != "GO"

    def test_go_base_untouched(self):
        rec, _, _, applied, cf = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=3),
            base="GO",
        )
        assert (rec, applied, cf) == ("GO", False, None)

    def test_conditional_go_base_untouched(self):
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=3),
            base="CONDITIONAL_GO",
        )
        assert (rec, applied) == ("CONDITIONAL_GO", False)

    def test_zero_failures_no_downgrade(self):
        """Nothing was excused, so nothing justifies softening a NO_GO
        (e.g. one produced by the pass-rate hard floor with zero failures)."""
        rec, _, _, applied, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(),
        )
        assert (rec, applied) == ("NO_GO", False)

    def test_missing_kind_counts_is_conservative(self):
        """Older snapshots / simulator without kind data → no downgrade,
        and the trail says why."""
        rec, evals, breakdown, applied, cf = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            None,
        )
        assert (rec, applied, breakdown, cf) == ("NO_GO", False, None, None)
        assert len(evals) == 1
        assert "no kind breakdown was available" in evals[0].message

    def test_excluded_failures_are_still_reported(self):
        _, evals, _, _, _ = self._apply(
            _kind_rules(infrastructure={"max_failures": 5}),
            _counts(infrastructure=3),
        )
        infra = [e for e in evals if e.rule_id == "kind_budget_infrastructure"][0]
        assert infra.actual_value == 3
        assert infra.threshold_value == 5
        assert "still reported" in infra.message
        # budgets never escalate on their own
        assert all(e.action == "INFO" for e in evals)


# ── evaluate_policy integration (policy_override avoids the DB) ─────────────


class TestEvaluatePolicyKindAware:
    @staticmethod
    async def _evaluate(rules_doc: dict, context: dict, dim_scores: dict | None = None, pass_rate: float = 100.0):
        from app.services.policy_evaluator_service import evaluate_policy

        return await evaluate_policy(
            project_id=None,
            dim_scores=dim_scores or dict(_NO_GO_DIMS),
            pass_rate=pass_rate,
            context=context,
            db=None,  # unused when policy_override is given
            policy_override=_policy(rules_doc),
        )

    @pytest.mark.asyncio
    async def test_identical_when_disabled_pin(self):
        """THE opt-in pin: block absent vs block disabled (with budgets
        configured!) produce identical evaluations, matching the plain
        threshold verdict, even when kind data would have allowed a
        downgrade."""
        context = {"failure_kind_counts": _counts(infrastructure=3)}

        absent = await self._evaluate({"rules": []}, context)
        disabled = await self._evaluate(
            {"rules": [], "kind_rules": _kind_rules(
                enabled=False, infrastructure={"max_failures": 5},
            )},
            context,
        )

        for res in (absent, disabled):
            assert res.recommendation == "NO_GO"  # plain threshold verdict
            assert res.kind_breakdown is None
            assert res.kind_rule_applied is False
            assert res.kind_counterfactual is None
            assert res.rule_evaluations == []
        assert absent.overall_result == disabled.overall_result
        assert absent.effective_composite == disabled.effective_composite
        # Full structural equality of the persisted trail dicts, modulo the
        # evaluation timestamp and the (random) mock policy identity.
        a, d = absent.to_dict(), disabled.to_dict()
        for key in ("evaluated_at", "policy_id"):
            a.pop(key), d.pop(key)
        # The behavioral trail remains identical. The new replay snapshot is
        # intentionally configuration-faithful, so it preserves the disabled
        # block even though that block has no verdict effect.
        a.pop("policy_snapshot")
        d.pop("policy_snapshot")
        assert a == d

    @pytest.mark.asyncio
    async def test_enabled_downgrades_no_go_with_counterfactual(self):
        res = await self._evaluate(
            {"rules": [], "kind_rules": _kind_rules(infrastructure={"max_failures": 5})},
            {"failure_kind_counts": _counts(infrastructure=3)},
        )
        assert res.recommendation == "CONDITIONAL_GO"
        assert res.kind_rule_applied is True
        assert res.kind_breakdown == _counts(infrastructure=3)
        assert "Would have been NO_GO" in res.kind_counterfactual
        assert "downgraded to CONDITIONAL_GO" in res.kind_counterfactual
        # trail carries the budget evaluation + summary
        ids = {e.rule_id for e in res.rule_evaluations}
        assert {"kind_budget_infrastructure", "kind_rules"} <= ids
        # persisted policy_evaluation dict carries the kind trail
        d = res.to_dict()
        assert d["kind_rule_applied"] is True
        assert d["kind_breakdown"]["infrastructure"] == 3

    @pytest.mark.asyncio
    async def test_block_rule_still_forces_no_go_over_downgrade(self):
        """An explicit failing BLOCK rule outranks the kind-budget
        downgrade — kind weighting can never bypass a hard policy rule."""
        res = await self._evaluate(
            {
                "rules": [{
                    "id": "r1", "name": "Defect cap", "type": "open_defect_limit",
                    "enabled": True, "params": {"max_open_defects": 0, "action": "BLOCK"},
                }],
                "kind_rules": _kind_rules(infrastructure={"max_failures": 5}),
            },
            {"open_defects": 3, "failure_kind_counts": _counts(infrastructure=3)},
        )
        assert res.recommendation == "NO_GO"
        assert res.overall_result == "BLOCK"
        # trail must not claim a downgrade that did not survive the BLOCK
        assert res.kind_rule_applied is False
        assert "BLOCK rule" in res.kind_counterfactual

    @pytest.mark.asyncio
    async def test_enabled_never_touches_go(self):
        res = await self._evaluate(
            {"rules": [], "kind_rules": _kind_rules(infrastructure={"max_failures": 5})},
            {"failure_kind_counts": _counts(infrastructure=3)},
            dim_scores=dict(_GO_DIMS),
        )
        assert res.recommendation == "GO"
        assert res.kind_rule_applied is False

    @pytest.mark.asyncio
    async def test_enabled_with_product_failures_stays_no_go(self):
        res = await self._evaluate(
            {"rules": [], "kind_rules": _kind_rules(infrastructure={"max_failures": 5})},
            {"failure_kind_counts": _counts(product=2, infrastructure=3)},
        )
        assert res.recommendation == "NO_GO"
        assert res.kind_rule_applied is False
        summary = [e for e in res.rule_evaluations if e.rule_id == "kind_rules"][0]
        assert "2 product failure(s) always count" in summary.message


# ── Agent-side kind counting ─────────────────────────────────────────────────


class TestAgentFailureKindCounts:
    def test_counts_from_analyses(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        analyses = {
            "tc1": {"failure_category": "PRODUCT_BUG"},
            "tc2": {"failure_category": "INFRASTRUCTURE"},
            "tc3": {"failure_category": "AUTOMATION_DEFECT"},
            "tc4": {"failure_category": "FLAKY"},
            "tc5": {"error": "analysis blew up", "confidence_score": 0},  # → unknown
            "tc6": {},  # no category → unknown
        }
        counts = ReleaseRiskAgent._failure_kind_counts(analyses)
        assert counts == {
            "product": 1, "test_code": 2, "infrastructure": 1, "unknown": 2,
        }

    def test_empty_analyses(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        assert ReleaseRiskAgent._failure_kind_counts({}) == {
            "product": 0, "test_code": 0, "infrastructure": 0, "unknown": 0,
        }

    @pytest.mark.asyncio
    async def test_snapshot_freezes_kind_counts(self):
        """input_snapshot carries the breakdown so decisions are reproducible
        and the policy simulator can replay kind-aware policies."""
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1", "project_id": "p-1", "test_run_id": "r-1",
            "analyses": {
                "tc1": {"failure_category": "INFRASTRUCTURE"},
                "tc2": {"failure_category": "PRODUCT_BUG"},
            },
        }
        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)
        assert snapshot["failure_kind_counts"] == {
            "product": 1, "test_code": 0, "infrastructure": 1, "unknown": 0,
        }
