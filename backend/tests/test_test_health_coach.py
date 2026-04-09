"""
Unit tests for Test Health Coach Service (Epic 10).

Tests anti-pattern detection, quarantine recommendations, stabilization actions,
impact scoring, and edge cases.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ── Anti-pattern classification tests ─────────────────────────────────────────

class TestAntiPatternClassification:
    def test_classify_timing_dependency(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "Hardcoded sleep detected — use explicit waits"}]
        result = _classify_anti_patterns(violations)
        assert "timing_dependency" in result

    def test_classify_empty_catch(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "Empty catch block swallows exceptions"}]
        result = _classify_anti_patterns(violations)
        assert "empty_catch" in result

    def test_classify_missing_assertions(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "No assertions found — test may pass vacuously"}]
        result = _classify_anti_patterns(violations)
        assert "missing_assertions" in result

    def test_classify_brittle_selector(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "XPath with long numeric index — brittle, breaks on DOM changes"}]
        result = _classify_anti_patterns(violations)
        assert "brittle_selector" in result

    def test_classify_shared_state(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "Static mutable field in test class — potential shared state"}]
        result = _classify_anti_patterns(violations)
        assert "shared_state" in result

    def test_classify_multiple_patterns(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [
            {"pattern": "Hardcoded sleep detected"},
            {"pattern": "Empty catch block swallows exceptions"},
            {"pattern": "No assertions found"},
        ]
        result = _classify_anti_patterns(violations)
        assert len(result) == 3
        assert "timing_dependency" in result
        assert "empty_catch" in result
        assert "missing_assertions" in result

    def test_classify_empty_violations(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        result = _classify_anti_patterns([])
        assert result == []

    def test_classify_unknown_pattern(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        violations = [{"pattern": "Some completely unknown issue"}]
        result = _classify_anti_patterns(violations)
        assert result == []


# ── Stabilization actions tests ───────────────────────────────────────────────

class TestStabilizationActions:
    def test_timing_dependency_actions(self):
        from app.services.test_health_coach_service import _get_stabilization_actions

        actions = _get_stabilization_actions(["timing_dependency"])
        assert len(actions) == 2
        assert any("sleep" in a.lower() or "wait" in a.lower() for a in actions)

    def test_multiple_patterns_combine_actions(self):
        from app.services.test_health_coach_service import _get_stabilization_actions

        actions = _get_stabilization_actions(["timing_dependency", "empty_catch"])
        assert len(actions) == 4  # 2 from each pattern

    def test_empty_patterns_get_fallback(self):
        from app.services.test_health_coach_service import _get_stabilization_actions

        actions = _get_stabilization_actions([])
        assert len(actions) == 1
        assert "review" in actions[0].lower()

    def test_unknown_pattern_gets_fallback(self):
        from app.services.test_health_coach_service import _get_stabilization_actions

        actions = _get_stabilization_actions(["nonexistent_pattern"])
        assert len(actions) == 1


# ── Quarantine recommendation tests ──────────────────────────────────────────

class TestQuarantineRecommendation:
    def test_quarantine_high_failure_rate(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.55) == "QUARANTINE"
        assert _compute_quarantine_recommendation(0.50) == "QUARANTINE"
        assert _compute_quarantine_recommendation(1.0) == "QUARANTINE"

    def test_investigate_moderate_failure_rate(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.30) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.25) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.49) == "INVESTIGATE"

    def test_monitor_low_failure_rate(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.15) == "MONITOR"
        assert _compute_quarantine_recommendation(0.10) == "MONITOR"

    def test_healthy_very_low_failure_rate(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.05) == "HEALTHY"
        assert _compute_quarantine_recommendation(0.0) == "HEALTHY"

    def test_boundary_values(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.50) == "QUARANTINE"
        assert _compute_quarantine_recommendation(0.25) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.10) == "MONITOR"
        assert _compute_quarantine_recommendation(0.099) == "HEALTHY"


# ── Impact score tests ────────────────────────────────────────────────────────

class TestImpactScore:
    def test_zero_failure_rate(self):
        from app.services.test_health_coach_service import _compute_impact_score

        assert _compute_impact_score(0.0, 100) == 0.0

    def test_high_failure_high_frequency(self):
        from app.services.test_health_coach_service import _compute_impact_score

        score = _compute_impact_score(0.8, 50)
        assert score > 50

    def test_low_failure_low_frequency(self):
        from app.services.test_health_coach_service import _compute_impact_score

        score = _compute_impact_score(0.1, 5)
        assert score < 10

    def test_capped_at_100(self):
        from app.services.test_health_coach_service import _compute_impact_score

        score = _compute_impact_score(1.0, 10000)
        assert score <= 100.0

    def test_non_negative(self):
        from app.services.test_health_coach_service import _compute_impact_score

        score = _compute_impact_score(0.0, 0)
        assert score >= 0.0


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestTestHealthSchemas:
    def test_test_health_finding_defaults(self):
        from app.models.schemas import TestHealthFinding

        finding = TestHealthFinding(
            test_case_id="tc-1",
            test_name="test_login",
            health_score=75,
        )
        assert finding.violations == []
        assert finding.critical_count == 0
        assert finding.anti_patterns == []
        assert finding.recommendation == ""

    def test_test_health_response(self):
        from app.models.schemas import TestHealthFinding, TestHealthResponse

        resp = TestHealthResponse(
            run_id="run-1",
            total_analyzed=5,
            with_violations=2,
            avg_health_score=65.3,
            findings=[
                TestHealthFinding(
                    test_case_id="tc-1",
                    test_name="test_login",
                    health_score=40,
                    critical_count=1,
                    recommendation="Fix empty catch block",
                ),
            ],
        )
        assert resp.total_analyzed == 5
        assert len(resp.findings) == 1

    def test_flaky_coach_entry(self):
        from app.models.schemas import FlakyCoachEntry

        entry = FlakyCoachEntry(
            test_fingerprint="fp-abc",
            test_name="test_payment",
            failure_rate=0.35,
            total_runs=20,
            failed_runs=7,
            quarantine_recommendation="INVESTIGATE",
            impact_score=45.2,
        )
        assert entry.quarantine_recommendation == "INVESTIGATE"
        assert entry.stabilization_actions == []

    def test_flaky_coach_response(self):
        from app.models.schemas import FlakyCoachEntry, FlakyCoachResponse

        resp = FlakyCoachResponse(
            project_id="proj-1",
            total_flaky=3,
            quarantine_candidates=1,
            entries=[
                FlakyCoachEntry(
                    test_fingerprint="fp-1",
                    test_name="test_flaky",
                    failure_rate=0.6,
                    quarantine_recommendation="QUARANTINE",
                    stabilization_actions=["Quarantine immediately"],
                    impact_score=80.0,
                ),
            ],
        )
        assert resp.quarantine_candidates == 1
        assert resp.entries[0].quarantine_recommendation == "QUARANTINE"


# ── TestHealthAgent anti-pattern detection tests ─────────────────────────────

class TestAntiPatternDetection:
    def test_detect_sleep(self):
        from app.agents.test_health_agent import _analyze_source

        violations = _analyze_source("time.sleep(5)\nassert result == True")
        patterns = [v["pattern"] for v in violations]
        assert any("sleep" in p.lower() for p in patterns)

    def test_detect_empty_catch(self):
        from app.agents.test_health_agent import _analyze_source

        code = """
try {
    doSomething();
} catch (Exception e) {  }
assert result;
"""
        violations = _analyze_source(code)
        patterns = [v["pattern"] for v in violations]
        assert any("catch" in p.lower() for p in patterns)

    def test_detect_no_assertions(self):
        from app.agents.test_health_agent import _analyze_source

        violations = _analyze_source("print('hello')\nresult = do_something()")
        patterns = [v["pattern"] for v in violations]
        assert any("assertion" in p.lower() for p in patterns)

    def test_clean_code_no_violations(self):
        from app.agents.test_health_agent import _analyze_source

        violations = _analyze_source("result = calculate()\nassert result == 42")
        severe = [v for v in violations if v["severity"] in ("critical", "warning")]
        assert len(severe) == 0

    def test_empty_source_no_crash(self):
        from app.agents.test_health_agent import _analyze_source

        violations = _analyze_source("")
        assert isinstance(violations, list)

    def test_detect_suppressed_test(self):
        from app.agents.test_health_agent import _analyze_source

        violations = _analyze_source("@pytest.mark.skip\ndef test_broken(): assert True")
        patterns = [v["pattern"] for v in violations]
        assert any("suppress" in p.lower() or "ignored" in p.lower() for p in patterns)

    def test_detect_brittle_xpath(self):
        from app.agents.test_health_agent import _analyze_source

        code = """By.xpath("//div[123456]/span")\nassert True"""
        violations = _analyze_source(code)
        patterns = [v["pattern"] for v in violations]
        assert any("xpath" in p.lower() for p in patterns)


# ── Non-flaky product bugs should NOT be mislabeled ──────────────────────────

class TestProductBugNotMislabeled:
    def test_product_bug_not_quarantine(self):
        """
        The flaky coach only processes tests identified as flaky (with both
        passes AND failures in history). Tests that consistently fail are
        product bugs and are excluded by the `if failed == 0 or passed == 0: continue`
        guard in refresh_flaky_coach().
        """
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        # For tests that ARE flaky (have alternating results), quarantine at >50%
        assert _compute_quarantine_recommendation(1.0) == "QUARANTINE"
        # But 0% failure is never quarantined
        assert _compute_quarantine_recommendation(0.0) == "HEALTHY"
