"""`requires_human_review` must follow the configured confidence gate.

The gate threshold is admin-configurable (`ai_confidence_threshold`, default 80).
`analysis_router` evaluated it, recorded `threshold_check` into
`routing_metadata`, and set `low_confidence` / `confidence_gate_status` on the
result — but left `requires_human_review` at whatever the engine had chosen.

`rules_engine` chooses it from a **hardcoded** `confidence < 70`:

    rules_engine.py:  "requires_human_review": confidence < 70

That matters because `requires_human_review` is the field that actually
survives: `ai_analysis` has **no** `low_confidence` column, so the persisted row
carries only `requires_human_review`; `run_intelligence_service` serves it; and
`analytics_service` counts it as `needs_review`. `services/agent.py` already
derived it from the gate — the router did not, so the same verdict depended on
which path produced the analysis.

**Why this hid.** At default settings the two agree by luck: rules analyses have
no `evidence_references`, so `_validate_confidence` caps them at 50, and 50 is
below both the hardcoded 70 and the default threshold of 80. Verified against the
live deployment — `pattern.oom` and `pattern.connection_refused` both landed at
confidence 50 with `requires_human_review=t` and `gate_passed=false`, in
agreement. The disagreement needs a threshold at or below the capped confidence,
which an admin lowering the bar to surface more AI suggestions would produce.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("app.services.analysis_router")

from app.services import analysis_router  # noqa: E402
from app.services.confidence_gate import build_threshold_check  # noqa: E402

pytestmark = pytest.mark.regression


class TestTheRouterDerivesItFromTheGate:
    def test_router_sets_requires_human_review_from_the_check(self):
        src = inspect.getsource(analysis_router)
        assert 'result["requires_human_review"] = not check["passed"]' in src, (
            "the router records the gate verdict but does not apply it to "
            "requires_human_review — the only field that is persisted, served, "
            "and counted — so the configured threshold does not govern it"
        )

    def test_it_matches_what_the_agent_path_already_did(self):
        """Both paths must agree; a per-path verdict is the defect."""
        from app.services import agent

        assert 'not gate_check["passed"]' in inspect.getsource(agent)


class TestTheDisagreementItPrevents:
    """The rules engine's hardcoded 70 vs the configured threshold."""

    @staticmethod
    def _rules_engine_verdict(confidence: int) -> bool:
        # Mirrors rules_engine.py's literal, which is the thing under test.
        return confidence < 70

    def test_low_threshold_used_to_contradict_the_gate(self):
        """Admin lowers the bar to 40. A rules analysis capped at 50 passes the
        gate, but the hardcoded rule still demands review."""
        check = build_threshold_check(50, 40, "ai_config")
        assert check["passed"] is True, "50 >= 40 should pass the configured gate"
        assert self._rules_engine_verdict(50) is True, (
            "the hardcoded rule still flags it — this is the contradiction"
        )
        # The fix makes the gate authoritative.
        assert (not check["passed"]) is False

    def test_at_default_settings_they_agree(self):
        """Documents why this was invisible rather than asserting a bug that
        does not exist at defaults — verified live: confidence 50, threshold 80,
        both saying review."""
        check = build_threshold_check(50, 80, "env_default")
        assert check["passed"] is False
        assert self._rules_engine_verdict(50) is True
        assert (not check["passed"]) == self._rules_engine_verdict(50)

    def test_the_gate_verdict_tracks_the_threshold(self):
        """Guards the test itself: if build_threshold_check ignored its
        threshold, every assertion above would be vacuous."""
        assert build_threshold_check(50, 40, "ai_config")["passed"] is True
        assert build_threshold_check(50, 80, "ai_config")["passed"] is False


def test_requires_human_review_is_the_field_that_survives():
    """The reason this matters: low_confidence is not persisted, so the UI's
    fallback and the analytics count both land on requires_human_review."""
    from app.models import postgres

    cols = inspect.getsource(postgres.AIAnalysis)
    assert "requires_human_review" in cols
    assert "low_confidence" not in cols, (
        "if ai_analysis grew a low_confidence column, the read paths should "
        "serve it and this test's premise needs revisiting"
    )
