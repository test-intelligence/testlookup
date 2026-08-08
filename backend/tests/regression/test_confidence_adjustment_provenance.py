"""Confidence-adjustment reasons must name the engine that actually ran.

``_validate_confidence`` post-processes the output of every analysis engine, but
its adjustment reasons were written as though an LLM had always produced the
result. Observed on the live deployment — a real ``ai_analysis`` row, rules mode,
no model call anywhere in it::

    llm_provider  = none
    llm_model     = rules_engine
    analysis_mode = rules
    confidence_adjustments = [
      {"rule": "no_evidence_references",
       "reason": "LLM returned no evidence_references"},          <-- no LLM ran
      {"rule": "evidence_count_vs_confidence",
       "reason": "raw confidence 60-80 with no evidence and no tools"},
    ]

``confidence_adjustments`` is served by ``decision_trail_service`` and typed in
``frontend/src/types/decisionTrail.ts``, so this is provenance on the AI-trust
contract asserting a model call that never happened — the same family as the
fabricated confidence scores removed earlier.

The router falls through to rules whenever Ollama is unreachable or no ML model
is trained, which on any air-gapped or CPU-only install is the *normal* path, not
an edge case.
"""
from __future__ import annotations

import pytest

pytest.importorskip("app.agents.analysis_agent")

from app.agents.analysis_agent import AnalysisAgent  # noqa: E402

pytestmark = pytest.mark.regression


def _adjust(analysis: dict) -> list[dict]:
    """Run the real post-processor and return its adjustment list.

    The agent stashes them on ``_confidence_adjustments``; the caller later
    pops that into the persisted ``_audit`` block.
    """
    out = AnalysisAgent._validate_confidence(AnalysisAgent, analysis)  # type: ignore[arg-type]
    return out.get("_confidence_adjustments") or []


class TestTheEngineLabel:
    @pytest.mark.parametrize("mode,expected", [
        ("rules", "rules engine"),
        ("ml", "ML model"),
        ("llm", "LLM"),
    ])
    def test_label_follows_the_resolved_mode(self, mode, expected):
        assert AnalysisAgent._engine_label({"_routing": {"mode_resolved": mode}}) == expected

    def test_unknown_mode_does_not_claim_a_model_ran(self):
        """An unrecognised mode must degrade to something neutral rather than
        defaulting back to 'LLM' — that default is the whole bug."""
        for routing in ({}, {"_routing": {}}, {"_routing": {"mode_resolved": "wat"}}):
            label = AnalysisAgent._engine_label(routing)
            assert label == "analysis", label
            assert "LLM" not in label


class TestRulesModeDoesNotClaimAnLLM:
    def _rules_analysis(self) -> dict:
        # Mirrors the shape of the observed live row: high enough raw confidence
        # to trip the no-evidence cap, no evidence, no tools.
        return {
            "confidence_score": 65,
            "evidence_references": [],
            "tools_used": [],
            "root_cause_summary": (
                "Request or operation timed out. Check service latency and "
                "network path between the runner and the service under test."
            ),
            "failure_category": "INFRASTRUCTURE",
            "_routing": {"mode_resolved": "rules"},
        }

    def test_no_adjustment_reason_mentions_an_llm(self):
        adjustments = _adjust(self._rules_analysis())
        assert adjustments, "expected the no-evidence cap to fire on this input"
        offenders = [a for a in adjustments if "LLM" in str(a.get("reason", ""))]
        assert not offenders, (
            "a rules-mode analysis recorded an adjustment reason claiming an LLM "
            f"produced it: {offenders}"
        )

    def test_the_reason_names_the_rules_engine(self):
        adjustments = _adjust(self._rules_analysis())
        reasons = " ".join(str(a.get("reason", "")) for a in adjustments)
        assert "rules engine" in reasons, (
            f"the adjustment should attribute itself to the rules engine; got: {reasons}"
        )


def test_llm_mode_still_says_llm():
    """The fix must not scrub accurate provenance off the LLM path."""
    adjustments = _adjust({
        "confidence_score": 70,
        "evidence_references": [],
        "tools_used": [],
        "root_cause_summary": "The payment service returned 503 for every checkout attempt.",
        "failure_category": "INFRASTRUCTURE",
        "_routing": {"mode_resolved": "llm"},
    })
    reasons = " ".join(str(a.get("reason", "")) for a in adjustments)
    assert "LLM" in reasons, f"LLM-mode provenance was lost; got: {reasons}"
