"""AI trust framing — provenance on the analysis contract + the confidence gate
(US-15.1 / US-15.2).

Pins:
 - PROVENANCE PRESENT: when routing_metadata carries a routing decision, the
   AnalysisResponse exposes it structurally (mode_used / mode_requested /
   fallback_from / fallback_reason / fallback_occurred).
 - PROVENANCE ABSENT-NOT-FABRICATED: no routing_metadata (or a blob with no
   routing keys) → ``provenance is None``. Never a block of nulls.
 - THRESHOLD PRECEDENCE: ai_config override → "ai_config"; nothing stored (or
   junk stored) → env default → "env_default".
 - BOUNDARY: ``>=``. A score exactly at the threshold PASSES. This mirrors the
   pre-existing ``confidence < THRESHOLD -> needs review`` gates.
 - BELOW THRESHOLD → deterministic fallback (defect promotion holds for human
   review) AND the explicit low-confidence marker on the contract.
 - THRESHOLD CHECK RECORDED: classify_test lands all four keys in ``_routing``
   (and thence routing_metadata), and the decision trail surfaces it.
"""
from __future__ import annotations

import uuid

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _routing_meta(**overrides) -> dict:
    base = {
        "analysis_mode": "rules",
        "mode_used": "rules",
        "mode_requested": "llm",
        "mode_resolved": "llm",
        "fallback_from": "llm",
        "fallback_reason": "ollama_unreachable",
        "prompt_versions": {},
        "confidence_basis": "heuristic_estimate",
    }
    base.update(overrides)
    return base


class _Row:
    """Minimal AIAnalysis stand-in for the response-shaping helpers."""

    def __init__(self, routing_metadata=None, llm_provider="ollama", llm_model="llama3"):
        self.routing_metadata = routing_metadata
        self.llm_provider = llm_provider
        self.llm_model = llm_model


# ── US-15.1: provenance on AnalysisResponse ──────────────────────────────────


class TestProvenanceBlock:
    def test_present_when_routing_metadata_exists(self):
        from app.routers.analyze import _build_provenance

        prov = _build_provenance(_routing_meta(), llm_provider="ollama", llm_model="llama3")

        assert prov is not None
        assert prov.mode_used == "rules"
        assert prov.mode_requested == "llm"
        assert prov.mode_resolved == "llm"
        assert prov.llm_provider == "ollama"
        assert prov.llm_model == "llama3"
        assert prov.confidence_basis == "heuristic_estimate"

    def test_fallback_surfaces(self):
        """The whole point: the card can say 'the LLM was unavailable'."""
        from app.routers.analyze import _build_provenance

        prov = _build_provenance(_routing_meta())

        assert prov.fallback_from == "llm"
        assert prov.fallback_reason == "ollama_unreachable"
        assert prov.fallback_occurred is True

    def test_no_fallback_leaves_marker_false(self):
        from app.routers.analyze import _build_provenance

        prov = _build_provenance(
            _routing_meta(mode_used="llm", fallback_from=None, fallback_reason=None)
        )

        assert prov.fallback_occurred is False
        assert prov.fallback_from is None

    @pytest.mark.parametrize("meta", [None, {}, {"some_unrelated_key": 1}])
    def test_absent_not_fabricated(self, meta):
        """Older rows carry no routing decision — say nothing, do not invent."""
        from app.routers.analyze import _build_provenance

        assert _build_provenance(meta) is None

    def test_blob_with_only_kind_evidence_yields_none(self):
        """A pre-US-15.1 row that only ever stored kind_evidence is not provenance."""
        from app.routers.analyze import _build_provenance

        assert _build_provenance({"kind_evidence": {"kind": "product"}}) is None

    def test_analysis_mode_key_is_accepted_as_mode_used(self):
        """The pipeline persists the engine under ``analysis_mode``."""
        from app.routers.analyze import _build_provenance

        prov = _build_provenance({"analysis_mode": "ml"})
        assert prov is not None
        assert prov.mode_used == "ml"

    def test_threshold_check_is_carried_through(self):
        from app.routers.analyze import _build_provenance

        prov = _build_provenance(
            _routing_meta(threshold_check={
                "threshold": 80,
                "observed_confidence": 55,
                "passed": False,
                "source": "ai_config",
            })
        )
        assert prov.threshold_check is not None
        assert prov.threshold_check.threshold == 80
        assert prov.threshold_check.passed is False
        assert prov.threshold_check.source == "ai_config"

    def test_malformed_threshold_check_does_not_break_provenance(self):
        from app.routers.analyze import _build_provenance

        prov = _build_provenance(_routing_meta(threshold_check={"nonsense": True}))
        assert prov is not None
        assert prov.threshold_check is None


class TestAnalysisResponseContract:
    def test_new_fields_are_optional_so_existing_consumers_keep_working(self):
        """Every pre-existing field still works with no provenance supplied."""
        from app.models.schemas import AnalysisResponse

        resp = AnalysisResponse(
            test_case_id=uuid.uuid4(),
            root_cause_summary="x",
            failure_category="UNKNOWN",
            backend_error_found=False,
            pod_issue_found=False,
            is_flaky=False,
            confidence_score=42,
            recommended_actions=[],
            evidence_references=[],
            llm_provider="ollama",
            llm_model="llama3",
            requires_human_review=True,
        )

        assert resp.provenance is None
        assert resp.low_confidence is False
        assert resp.confidence_gate_status == "not_evaluated"
        assert resp.confidence_gate is None


# ── US-15.2: threshold resolution ────────────────────────────────────────────


class TestThresholdResolution:
    @pytest.mark.asyncio
    async def test_ai_config_override_wins(self, monkeypatch):
        from app.services import confidence_gate

        async def _cfg():
            return {"confidence_threshold": 65, "confidence_threshold_source": "ai_config"}

        monkeypatch.setattr(
            "app.services.ai_config_resolver.get_effective_ai_config", _cfg
        )
        threshold, source = await confidence_gate.resolve_confidence_threshold()

        assert threshold == 65
        assert source == "ai_config"

    @pytest.mark.asyncio
    async def test_env_default_when_nothing_stored(self, monkeypatch):
        from app.core.config import settings
        from app.services import confidence_gate

        async def _cfg():
            return {
                "confidence_threshold": settings.AI_CONFIDENCE_THRESHOLD,
                "confidence_threshold_source": "env_default",
            }

        monkeypatch.setattr(
            "app.services.ai_config_resolver.get_effective_ai_config", _cfg
        )
        threshold, source = await confidence_gate.resolve_confidence_threshold()

        assert threshold == settings.AI_CONFIDENCE_THRESHOLD
        assert source == "env_default"

    @pytest.mark.asyncio
    async def test_resolver_outage_degrades_to_env_default(self, monkeypatch):
        from app.core.config import settings
        from app.services import confidence_gate

        async def _boom():
            raise RuntimeError("redis and postgres both down")

        monkeypatch.setattr(
            "app.services.ai_config_resolver.get_effective_ai_config", _boom
        )
        threshold, source = await confidence_gate.resolve_confidence_threshold()

        assert threshold == settings.AI_CONFIDENCE_THRESHOLD
        assert source == "env_default"

    @pytest.mark.parametrize(
        "raw", [None, True, False, "abc", -1, 101, object()],
    )
    def test_junk_override_is_dropped(self, raw):
        from app.services.confidence_gate import normalize_threshold

        assert normalize_threshold(raw) is None

    @pytest.mark.parametrize("raw,expected", [(0, 0), (100, 100), ("75", 75), (60, 60)])
    def test_valid_override_is_accepted(self, raw, expected):
        from app.services.confidence_gate import normalize_threshold

        assert normalize_threshold(raw) == expected

    def test_source_constants_agree_across_modules(self):
        """confidence_gate duplicates these literals to avoid a circular import."""
        from app.services import ai_config_resolver, confidence_gate

        assert ai_config_resolver.CONFIDENCE_SOURCE_AI_CONFIG == confidence_gate.SOURCE_AI_CONFIG
        assert (
            ai_config_resolver.CONFIDENCE_SOURCE_ENV_DEFAULT
            == confidence_gate.SOURCE_ENV_DEFAULT
        )


# ── US-15.2: the boundary ────────────────────────────────────────────────────


class TestBoundarySemantics:
    def test_exactly_at_threshold_passes(self):
        """PINNED: >= not >. Matches the pre-existing `confidence < T` gates."""
        from app.services.confidence_gate import passes

        assert passes(80, 80) is True

    def test_one_below_fails(self):
        from app.services.confidence_gate import passes

        assert passes(79, 80) is False

    def test_one_above_passes(self):
        from app.services.confidence_gate import passes

        assert passes(81, 80) is True

    def test_absent_confidence_never_passes(self):
        from app.services.confidence_gate import passes

        assert passes(None, 0) is False

    def test_absent_confidence_is_not_evaluated_not_low(self):
        """'we did not judge' is a distinct answer from 'we judged it low'."""
        from app.services.confidence_gate import (
            build_threshold_check,
            gate_status,
            is_low_confidence,
        )

        check = build_threshold_check(None, 80, "env_default")
        assert check["observed_confidence"] is None
        assert gate_status(check) == "not_evaluated"
        assert is_low_confidence(check) is False

    def test_no_check_at_all_is_not_evaluated(self):
        from app.services.confidence_gate import gate_status, is_low_confidence

        assert gate_status(None) == "not_evaluated"
        assert is_low_confidence(None) is False


class TestThresholdCheckShape:
    def test_exactly_four_keys(self):
        from app.services.confidence_gate import build_threshold_check

        check = build_threshold_check(55, 80, "ai_config")
        assert set(check) == {"threshold", "observed_confidence", "passed", "source"}
        assert check == {
            "threshold": 80,
            "observed_confidence": 55,
            "passed": False,
            "source": "ai_config",
        }

    def test_validates_against_the_pydantic_schema(self):
        from app.models.schemas import ThresholdCheck
        from app.services.confidence_gate import build_threshold_check

        model = ThresholdCheck(**build_threshold_check(90, 80, "env_default"))
        assert model.passed is True


# ── US-15.2: automations read the configured threshold ───────────────────────


class TestDefectPromotionGate:
    """action_policy no longer hard-codes 60. BEHAVIOUR CHANGE: the default is
    now ai_confidence_threshold (80), so 60-79 no longer auto-approves."""

    async def _check(self, monkeypatch, confidence, threshold, source="ai_config"):
        from app.services import action_policy

        async def _resolve():
            return threshold, source

        monkeypatch.setattr(
            "app.services.confidence_gate.resolve_confidence_threshold", _resolve
        )
        return await action_policy.check_defect_promotion_policy(
            None,
            project_id=str(uuid.uuid4()),
            severity="MEDIUM",
            has_jira_key=False,
            confidence_score=confidence,
        )

    @pytest.mark.asyncio
    async def test_below_threshold_falls_back_to_human_review(self, monkeypatch):
        from app.services.action_policy import ActionStatus

        result = await self._check(monkeypatch, confidence=55, threshold=80)

        assert result["requires_approval"] is True
        assert result["initial_status"] == ActionStatus.PENDING_REVIEW
        assert any("below the configured threshold" in r for r in result["policy_reasons"])

    @pytest.mark.asyncio
    async def test_at_threshold_auto_approves(self, monkeypatch):
        """Boundary pin: exactly at the threshold is ABOVE the gate."""
        from app.services.action_policy import ActionStatus

        result = await self._check(monkeypatch, confidence=80, threshold=80)

        assert result["requires_approval"] is False
        assert result["initial_status"] == ActionStatus.APPROVED

    @pytest.mark.asyncio
    async def test_one_below_threshold_holds(self, monkeypatch):
        result = await self._check(monkeypatch, confidence=79, threshold=80)
        assert result["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_reads_the_configured_threshold_not_the_old_hardcoded_60(
        self, monkeypatch
    ):
        """65 used to auto-approve under the hard-coded `< 60`; with the gate at
        80 it now holds, and with the gate lowered to 50 it approves again."""
        held = await self._check(monkeypatch, confidence=65, threshold=80)
        approved = await self._check(monkeypatch, confidence=65, threshold=50)

        assert held["requires_approval"] is True
        assert approved["requires_approval"] is False

    @pytest.mark.asyncio
    async def test_records_the_threshold_check(self, monkeypatch):
        result = await self._check(monkeypatch, confidence=55, threshold=80)

        assert result["threshold_check"] == {
            "threshold": 80,
            "observed_confidence": 55,
            "passed": False,
            "source": "ai_config",
        }

    @pytest.mark.asyncio
    async def test_duplicate_short_circuit_still_records_the_check(self, monkeypatch):
        from app.services import action_policy

        async def _resolve():
            return 80, "env_default"

        monkeypatch.setattr(
            "app.services.confidence_gate.resolve_confidence_threshold", _resolve
        )
        result = await action_policy.check_defect_promotion_policy(
            None,
            project_id=str(uuid.uuid4()),
            severity="LOW",
            has_jira_key=False,
            confidence_score=95,
            is_duplicate=True,
        )

        assert result["initial_status"] == action_policy.ActionStatus.REJECTED
        assert result["threshold_check"]["passed"] is True

    def test_requires_approval_honours_an_explicit_threshold(self):
        from app.services.action_policy import ActionType, requires_approval

        assert requires_approval(
            ActionType.DEFECT_PROMOTION,
            severity="LOW",
            confidence_score=70,
            confidence_threshold=90,
        ) is True
        assert requires_approval(
            ActionType.DEFECT_PROMOTION,
            severity="LOW",
            confidence_score=70,
            confidence_threshold=70,
        ) is False


# ── US-15.2: the threshold check lands in the decision record ────────────────


class TestClassifyTestRecordsThresholdCheck:
    @pytest.mark.asyncio
    async def test_all_four_keys_land_in_routing(self, monkeypatch):
        from app.services import analysis_router

        async def _resolve():
            return 80, "ai_config"

        monkeypatch.setattr(
            "app.services.confidence_gate.resolve_confidence_threshold", _resolve
        )
        monkeypatch.setattr(analysis_router, "get_analysis_mode", lambda: "rules")
        monkeypatch.setattr(
            analysis_router,
            "_classify_rules",
            lambda tc, h, rc: {"confidence_score": 55, "failure_category": "UNKNOWN"},
        )

        result = await analysis_router.classify_test({"test_name": "t"})

        check = result["_routing"]["threshold_check"]
        assert set(check) == {"threshold", "observed_confidence", "passed", "source"}
        assert check == {
            "threshold": 80,
            "observed_confidence": 55,
            "passed": False,
            "source": "ai_config",
        }

    @pytest.mark.asyncio
    async def test_below_threshold_sets_the_explicit_markers(self, monkeypatch):
        from app.services import analysis_router

        async def _resolve():
            return 80, "env_default"

        monkeypatch.setattr(
            "app.services.confidence_gate.resolve_confidence_threshold", _resolve
        )
        monkeypatch.setattr(analysis_router, "get_analysis_mode", lambda: "rules")
        monkeypatch.setattr(
            analysis_router,
            "_classify_rules",
            lambda tc, h, rc: {"confidence_score": 30},
        )

        result = await analysis_router.classify_test({"test_name": "t"})

        assert result["low_confidence"] is True
        assert result["confidence_gate_status"] == "below_threshold"

    @pytest.mark.asyncio
    async def test_above_threshold_markers(self, monkeypatch):
        from app.services import analysis_router

        async def _resolve():
            return 80, "env_default"

        monkeypatch.setattr(
            "app.services.confidence_gate.resolve_confidence_threshold", _resolve
        )
        monkeypatch.setattr(analysis_router, "get_analysis_mode", lambda: "rules")
        monkeypatch.setattr(
            analysis_router,
            "_classify_rules",
            lambda tc, h, rc: {"confidence_score": 95},
        )

        result = await analysis_router.classify_test({"test_name": "t"})

        assert result["low_confidence"] is False
        assert result["confidence_gate_status"] == "above_threshold"

    @pytest.mark.asyncio
    async def test_gate_failure_never_breaks_routing(self, monkeypatch):
        """A settings outage must not lose the analysis."""
        from app.services import analysis_router

        async def _boom(_):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr("app.services.confidence_gate.check_confidence", _boom)
        monkeypatch.setattr(analysis_router, "get_analysis_mode", lambda: "rules")
        monkeypatch.setattr(
            analysis_router,
            "_classify_rules",
            lambda tc, h, rc: {"confidence_score": 70},
        )

        result = await analysis_router.classify_test({"test_name": "t"})

        assert result["confidence_score"] == 70
        assert result["_routing"]["threshold_check"] is None


def _agent():
    """AnalysisAgent without running __init__ — _validate_confidence only needs
    the class-level helpers (_stringify_value, UNKNOWN_CATEGORY)."""
    from app.agents.analysis_agent import AnalysisAgent

    return AnalysisAgent.__new__(AnalysisAgent)


class TestAnalysisAgentRefreshesTheCheck:
    def test_gate_is_re_evaluated_against_the_final_confidence(self):
        """_validate_confidence caps scores; the recorded check must observe the
        FINAL number, reusing the threshold the router already resolved."""
        analysis = {
            # raw 75, no evidence, no tools → capped to 50 by the evidence rules
            "confidence_score": 75,
            "failure_category": "TEST_SCRIPT_ISSUE",
            "root_cause_summary": "A sufficiently long root cause summary string.",
            "evidence_references": [],
            "tools_used": [],
            "_routing": {
                "threshold_check": {
                    "threshold": 70,
                    "observed_confidence": 75,
                    "passed": True,
                    "source": "ai_config",
                }
            },
        }
        out = _agent()._validate_confidence(analysis)

        check = out["_routing"]["threshold_check"]
        assert out["confidence_score"] == 50
        assert check["observed_confidence"] == 50   # the FINAL number, not 75
        assert check["threshold"] == 70             # threshold NOT re-resolved
        assert check["source"] == "ai_config"       # provenance preserved
        assert check["passed"] is False
        assert out["requires_human_review"] is True
        assert out["low_confidence"] is True
        assert out["confidence_gate_status"] == "below_threshold"

    def test_without_a_prior_check_falls_back_to_env_default(self):
        from app.core.config import settings

        out = _agent()._validate_confidence({
            "confidence_score": 95,
            "failure_category": "TEST_SCRIPT_ISSUE",
            "root_cause_summary": "A sufficiently long root cause summary string.",
            "evidence_references": [{"source": "stacktrace"}],
            "tools_used": ["a"],
        })

        check = out["_routing"] if "_routing" in out else None
        assert check is None  # no routing dict to write back into — must not crash
        assert out["requires_human_review"] is (
            out["confidence_score"] < settings.AI_CONFIDENCE_THRESHOLD
        )
        assert out["confidence_gate_status"] in ("above_threshold", "below_threshold")

    def test_matches_the_previous_settings_comparison_at_the_boundary(self):
        """Regression pin: replacing `confidence < AI_CONFIDENCE_THRESHOLD` with
        the gate must not move the boundary."""
        from app.core.config import settings

        threshold = settings.AI_CONFIDENCE_THRESHOLD
        for score in (threshold - 1, threshold, threshold + 1):
            out = _agent()._validate_confidence({
                "confidence_score": score,
                "failure_category": "TEST_SCRIPT_ISSUE",
                "root_cause_summary": "A sufficiently long root cause summary string.",
                "evidence_references": [{"source": "a"}, {"source": "b"}],
                "tools_used": ["t"],
            })
            assert out["requires_human_review"] is (
                out["confidence_score"] < threshold
            ), f"boundary moved at {score}"


# ── US-15.2: the decision trail surfaces the check ───────────────────────────


class TestDecisionTrailExposesThresholdCheck:
    def test_per_test_routing_schema_carries_it(self):
        from app.models.schemas import PerTestRouting

        row = PerTestRouting(
            test_case_id=uuid.uuid4(),
            threshold_check={
                "threshold": 80,
                "observed_confidence": 40,
                "passed": False,
                "source": "ai_config",
            },
        )
        assert row.threshold_check.passed is False

    def test_schema_defaults_to_none_for_legacy_rows(self):
        from app.models.schemas import PerTestRouting

        assert PerTestRouting(test_case_id=uuid.uuid4()).threshold_check is None

    def test_below_threshold_count_defaults_to_zero(self):
        from app.models.schemas import DecisionTrailResponse

        assert DecisionTrailResponse(run_id=uuid.uuid4()).below_threshold_count == 0

    def test_rollup_counts_only_recorded_failed_checks(self):
        """Legacy rows with no check are neither passed nor below."""
        per_test = [
            {"threshold_check": {"passed": False}},
            {"threshold_check": {"passed": True}},
            {"threshold_check": None},
            {},
        ]
        below = sum(
            1
            for row in per_test
            if isinstance(row.get("threshold_check"), dict)
            and row["threshold_check"].get("passed") is False
        )
        assert below == 1


# ── Settings surface ─────────────────────────────────────────────────────────


class TestAIConfigReadSource:
    def test_threshold_source_defaults_to_env_default(self):
        from app.models.schemas import AIConfigRead

        cfg = AIConfigRead(
            llm_provider="ollama",
            llm_model="llama3",
            llm_temperature=0.1,
            llm_max_tokens=4096,
            ai_offline_mode=True,
            embedding_provider="ollama",
            embedding_model="nomic",
            ai_confidence_threshold=80,
            ai_timeout_seconds=300,
            deep_investigation_enabled=False,
            finetune_enabled=False,
            openai_key_set=False,
            google_key_set=False,
            analysis_mode="auto",
        )
        assert cfg.ai_confidence_threshold_source == "env_default"


# ── US-15.1: analysis_id must reach the UI or confirm/correct is inert ──────


def test_analysis_response_carries_analysis_id_field():
    """The UI gates confirm/correct on ``analysis_id``.

    Without it the AI card can never reach POST /api/v1/feedback/{analysis_id},
    so the human-correction loop — a headline US-15.1 acceptance criterion —
    renders no buttons at all. Caught exactly that at merge time.
    """
    from app.models.schemas import AnalysisResponse

    assert "analysis_id" in AnalysisResponse.model_fields
    field = AnalysisResponse.model_fields["analysis_id"]
    # Optional: a freshly computed, unpersisted result has no row to correct,
    # and the UI must omit the buttons rather than render ones that 404.
    assert field.default is None


def test_both_analysis_response_sites_populate_analysis_id():
    """Both construction sites in routers/analyze.py must pass analysis_id."""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "app" / "routers" / "analyze.py"
    ).read_text(encoding="utf-8")
    constructions = re.findall(
        r"return AnalysisResponse\((.*?)\n    \)", src, re.S
    )
    assert len(constructions) >= 2, "expected both AnalysisResponse sites"
    for block in constructions:
        assert "analysis_id=" in block, (
            "an AnalysisResponse site does not pass analysis_id — the UI's "
            "confirm/correct buttons would silently never render"
        )
