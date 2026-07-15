"""Kind-budget confidence floor (AI-4) — ``min_confidence_to_excuse``.

Pins:
 - NULL FLOOR = BYTE-IDENTICAL: a budget without the floor (or with it
   explicitly null) evaluates exactly like the US-9.3 fixtures — same
   recommendation, same trail, same breakdown shape (no ``floor_rejected``
   key) — even when per-failure confidences are present in the context.
 - Floor semantics: only failures with kind confidence >= floor are
   eligible for the budget; below-floor and unknown-confidence failures
   count as product (conservative); missing confidence data excuses nothing.
 - Trail: the kind breakdown records per-kind floor-rejected counts, the
   budget row message names the floor, and the counterfactual mentions the
   floor when a downgrade survives.
 - Schema: floor defaults to None, bounded 0-100.
 - Agent: ``_failure_kind_confidences`` prefers the evidence-checklist
   confidence and is frozen into the input snapshot for simulator replay.
"""
from __future__ import annotations

import pytest


def _kind_rules(**kinds) -> dict:
    return {"enabled": True, **kinds}


def _counts(product: int = 0, test_code: int = 0, infrastructure: int = 0, unknown: int = 0) -> dict:
    return {
        "product": product, "test_code": test_code,
        "infrastructure": infrastructure, "unknown": unknown,
    }


def _apply(kind_rules: dict, context: dict, base: str = "NO_GO"):
    from app.services.policy_evaluator_service import _apply_kind_rules

    return _apply_kind_rules(kind_rules, context, base)


# ── Schema ───────────────────────────────────────────────────────────────────


class TestFloorSchema:
    def test_defaults_to_none(self):
        from app.models.schemas import PolicyKindBudget

        assert PolicyKindBudget(max_failures=5).min_confidence_to_excuse is None

    def test_bounds(self):
        from pydantic import ValidationError

        from app.models.schemas import PolicyKindBudget

        assert PolicyKindBudget(max_failures=5, min_confidence_to_excuse=0).min_confidence_to_excuse == 0
        assert PolicyKindBudget(max_failures=5, min_confidence_to_excuse=100).min_confidence_to_excuse == 100
        for bad in (-1, 101):
            with pytest.raises(ValidationError):
                PolicyKindBudget(max_failures=5, min_confidence_to_excuse=bad)

    def test_round_trip_preserves_floor_and_null(self):
        from app.models.schemas import PolicyDocument

        doc = PolicyDocument(**{
            "kind_rules": {
                "enabled": True,
                "infrastructure": {"max_failures": 5, "min_confidence_to_excuse": 70},
                "test_code": {"max_failures": 3},
            },
        })
        dumped = doc.model_dump()
        assert dumped["kind_rules"]["infrastructure"]["min_confidence_to_excuse"] == 70
        assert dumped["kind_rules"]["test_code"]["min_confidence_to_excuse"] is None


# ── Null floor = byte-identical to US-9.3 ────────────────────────────────────


class TestNullFloorByteIdenticalPin:
    def test_absent_and_explicit_null_match_us93_semantics(self):
        """The US-9.3 under-budget fixture, replayed with (a) no floor key
        and (b) an explicit null floor, with confidences present in the
        context — identical downgrade, identical trail, identical
        breakdown shape (no floor_rejected key)."""
        context = {
            "failure_kind_counts": _counts(infrastructure=3),
            # Confidences present but must be IGNORED without a floor —
            # including ones that would fail any floor.
            "failure_kind_confidences": {"infrastructure": [1, 1, 1]},
        }
        results = [
            _apply(_kind_rules(infrastructure={"max_failures": 5}), context),
            _apply(_kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": None}), context),
        ]
        for rec, evals, breakdown, applied, cf in results:
            assert rec == "CONDITIONAL_GO"
            assert applied is True
            assert breakdown == _counts(infrastructure=3)   # flat US-9.3 shape
            assert "floor_rejected" not in breakdown
            assert "3 infrastructure failure(s) <= budget 5" in cf
            assert "confidence" not in cf                    # floor never mentioned
            infra = [e for e in evals if e.rule_id == "kind_budget_infrastructure"][0]
            assert "floor" not in infra.message

        # Full structural equality between the two runs.
        (rec_a, evals_a, bd_a, app_a, cf_a), (rec_b, evals_b, bd_b, app_b, cf_b) = results
        assert (rec_a, bd_a, app_a, cf_a) == (rec_b, bd_b, app_b, cf_b)
        assert [e.__dict__ for e in evals_a] == [e.__dict__ for e in evals_b]

    def test_null_floor_over_budget_identical(self):
        rec, evals, breakdown, applied, cf = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": None}),
            {
                "failure_kind_counts": _counts(infrastructure=6),
                "failure_kind_confidences": {"infrastructure": [90] * 6},
            },
        )
        assert (rec, applied, cf) == ("NO_GO", False, None)
        assert breakdown == _counts(infrastructure=6)


# ── Floor semantics ──────────────────────────────────────────────────────────


class TestFloorSemantics:
    def test_all_above_floor_downgrades(self):
        rec, evals, breakdown, applied, cf = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70}),
            {
                "failure_kind_counts": _counts(infrastructure=3),
                "failure_kind_confidences": {"infrastructure": [90, 75, 70]},
            },
        )
        assert (rec, applied) == ("CONDITIONAL_GO", True)
        assert breakdown["floor_rejected"] == {"infrastructure": 0}
        assert "kind confidence >= 70" in cf
        assert "floor-rejected" in cf  # counterfactual mentions the floor

    def test_below_floor_counts_as_product(self):
        """One failure below the floor blocks the downgrade even though the
        eligible ones fit the budget — conservative."""
        rec, evals, breakdown, applied, cf = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70}),
            {
                "failure_kind_counts": _counts(infrastructure=3),
                "failure_kind_confidences": {"infrastructure": [90, 75, 40]},
            },
        )
        assert (rec, applied, cf) == ("NO_GO", False, None)
        assert breakdown["floor_rejected"] == {"infrastructure": 1}
        summary = [e for e in evals if e.rule_id == "kind_rules"][0]
        assert "below their kind-confidence floor" in summary.message
        infra = [e for e in evals if e.rule_id == "kind_budget_infrastructure"][0]
        assert "1 below the 70% confidence floor counted as product" in infra.message

    def test_unknown_confidence_counts_as_below_floor(self):
        rec, _, breakdown, applied, _ = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70}),
            {
                "failure_kind_counts": _counts(infrastructure=2),
                "failure_kind_confidences": {"infrastructure": [90, None]},
            },
        )
        assert (rec, applied) == ("NO_GO", False)
        assert breakdown["floor_rejected"] == {"infrastructure": 1}

    def test_missing_confidence_data_excuses_nothing(self):
        """Pre-AI-4 snapshot (counts but no confidences) + a floor →
        nothing is eligible; all count as product."""
        rec, _, breakdown, applied, _ = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70}),
            {"failure_kind_counts": _counts(infrastructure=3)},
        )
        assert (rec, applied) == ("NO_GO", False)
        assert breakdown["floor_rejected"] == {"infrastructure": 3}

    def test_eligible_over_budget_restores_full_counting(self):
        rec, evals, _, applied, _ = _apply(
            _kind_rules(infrastructure={"max_failures": 1, "min_confidence_to_excuse": 70}),
            {
                "failure_kind_counts": _counts(infrastructure=3),
                "failure_kind_confidences": {"infrastructure": [90, 90, 40]},
            },
        )
        assert (rec, applied) == ("NO_GO", False)
        infra = [e for e in evals if e.rule_id == "kind_budget_infrastructure"][0]
        assert infra.passed is False
        assert "exceed budget" in infra.message

    def test_confidence_list_longer_than_count_is_capped(self):
        """Defensive: snapshot drift can't excuse more failures than exist."""
        rec, _, breakdown, applied, _ = _apply(
            _kind_rules(infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70}),
            {
                "failure_kind_counts": _counts(infrastructure=1),
                "failure_kind_confidences": {"infrastructure": [90, 90, 90]},
            },
        )
        assert (rec, applied) == ("CONDITIONAL_GO", True)
        assert breakdown["floor_rejected"] == {"infrastructure": 0}

    def test_mixed_floored_and_unfloored_budgets(self):
        """test_code budget has no floor (US-9.3 semantics) while the
        infrastructure budget is floored — both apply independently."""
        rec, _, breakdown, applied, cf = _apply(
            _kind_rules(
                infrastructure={"max_failures": 5, "min_confidence_to_excuse": 70},
                test_code={"max_failures": 4},
            ),
            {
                "failure_kind_counts": _counts(infrastructure=2, test_code=2),
                "failure_kind_confidences": {
                    "infrastructure": [80, 75],
                    "test_code": [5, 5],  # ignored — no floor on test_code
                },
            },
        )
        assert (rec, applied) == ("CONDITIONAL_GO", True)
        assert "2 test_code failure(s) <= budget 4" in cf
        assert breakdown["floor_rejected"] == {"infrastructure": 0}


# ── Agent-side confidences + snapshot ────────────────────────────────────────


class TestAgentKindConfidences:
    def test_prefers_evidence_checklist_confidence(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        analyses = {
            "tc1": {
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 40,
                "_audit": {"kind_evidence": {"confidence": 85}},
            },
            "tc2": {"failure_category": "INFRASTRUCTURE", "confidence_score": 55},
            "tc3": {"failure_category": "PRODUCT_BUG", "confidence_score": 70},
            "tc4": {},  # unknown kind, unknown confidence
        }
        confidences = ReleaseRiskAgent._failure_kind_confidences(analyses)
        assert sorted(confidences["infrastructure"]) == [55, 85]
        assert confidences["product"] == [70]
        # kind unknown with no confidence_score → confidence 0 (from the
        # empty-dict fallback) or None — conservative either way; pin the
        # actual behaviour: confidence_score missing → None.
        assert confidences["unknown"] == [None]
        assert confidences["test_code"] == []

    @pytest.mark.asyncio
    async def test_snapshot_freezes_confidences(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1", "project_id": "p-1", "test_run_id": "r-1",
            "analyses": {
                "tc1": {
                    "failure_category": "INFRASTRUCTURE",
                    "confidence_score": 40,
                    "_audit": {"kind_evidence": {"confidence": 82}},
                },
            },
        }
        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)
        assert snapshot["failure_kind_confidences"]["infrastructure"] == [82]
