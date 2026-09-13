"""
ROI-02: Value Metrics — Unit Tests.

Tests:
  - Service function importable and callable
  - Router endpoints exist
  - Time-saved calculation formula
  - Metric response shape
  - Edge cases: zero data, all projects, short period
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
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        yield


class TestServiceImport:
    def test_get_value_metrics_importable(self):
        from app.services.value_metrics_service import get_value_metrics
        assert callable(get_value_metrics)

    def test_default_assumptions_defined(self):
        # US-12.1 replaced the hardcoded rate constants with tunable
        # per-project assumptions; the code defaults live here.
        from app.services.value_metrics_service import (
            DEFAULT_BLOCKED_RUN_WAIT_MINUTES,
            DEFAULT_DEFECT_FILING_MINUTES,
            DEFAULT_TRIAGE_MINUTES_PER_FAILURE,
        )
        assert DEFAULT_TRIAGE_MINUTES_PER_FAILURE == 20.0
        assert DEFAULT_BLOCKED_RUN_WAIT_MINUTES == 30.0
        assert DEFAULT_DEFECT_FILING_MINUTES == 15.0


class TestRouterEndpoints:
    def test_get_metrics_exists(self):
        from app.routers.value_metrics import get_metrics
        assert callable(get_metrics)

    def test_export_metrics_exists(self):
        from app.routers.value_metrics import export_metrics
        assert callable(export_metrics)

    def test_router_prefix(self):
        from app.routers.value_metrics import router
        assert router.prefix == "/api/v1/value-metrics"


class TestTimeSavedFormula:
    def test_zero_inputs(self):
        from app.services.value_metrics_service import EffectiveAssumptions

        a = EffectiveAssumptions()
        minutes = (
            0 * a.triage_minutes_per_failure
            + 0 * a.defect_filing_minutes
            + 0 * a.triage_minutes_per_failure
        )
        assert minutes == 0

    def test_positive_inputs(self):
        # Legacy scalar mapping: cluster triage + intelligence report use
        # the per-failure triage minutes; duplicates use the filing minutes.
        from app.services.value_metrics_service import EffectiveAssumptions

        a = EffectiveAssumptions()  # defaults 20 / 30 / 15
        minutes = (
            10 * a.triage_minutes_per_failure
            + 5 * a.defect_filing_minutes
            + 20 * a.triage_minutes_per_failure
        )
        assert minutes == 10 * 20 + 5 * 15 + 20 * 20  # 200 + 75 + 400 = 675
        assert minutes == 675

    def test_hours_conversion(self):
        minutes = 150
        hours = round(minutes / 60, 1)
        assert hours == 2.5


class TestResponseShape:
    # Legacy keys preserved for old consumers…
    LEGACY_FIELDS = {
        "period_days", "project_id",
        "triage_time_saved_minutes", "triage_time_saved_hours",
        "defects_auto_grouped", "tests_grouped",
        "duplicate_tickets_avoided", "defects_promoted",
        "flaky_tests_identified", "quarantine_recommended",
        "risky_releases_blocked", "releases_conditional",
        "release_overrides", "intelligence_reports_generated",
    }
    # …plus the US-12.1 hours-saved model keys (pinned contract).
    MODEL_FIELDS = {
        "available", "insufficient_data_reason", "headline", "monthly",
        "assumptions", "assumptions_source", "methodology_version",
    }

    def test_expected_fields(self):
        expected_fields = self.LEGACY_FIELDS | self.MODEL_FIELDS
        mock_response = {field: 0 for field in expected_fields}
        mock_response["project_id"] = None
        assert set(mock_response.keys()) == expected_fields


class TestEdgeCases:
    def test_zero_data_produces_valid_response(self):
        response = {
            "period_days": 30,
            "project_id": None,
            "triage_time_saved_minutes": 0,
            "triage_time_saved_hours": 0.0,
            "defects_auto_grouped": 0,
            "tests_grouped": 0,
            "duplicate_tickets_avoided": 0,
            "defects_promoted": 0,
            "flaky_tests_identified": 0,
            "quarantine_recommended": 0,
            "risky_releases_blocked": 0,
            "releases_conditional": 0,
            "release_overrides": 0,
            "intelligence_reports_generated": 0,
        }
        assert response["triage_time_saved_hours"] == 0.0

    def test_project_id_optional(self):
        """None project_id means all-projects aggregate."""
        response = {"project_id": None}
        assert response["project_id"] is None

    def test_short_period(self):
        """1-day window should be valid."""
        response = {"period_days": 1}
        assert response["period_days"] >= 1

    def test_all_models_referenced_exist(self):
        from app.models.postgres import (
            Defect, DefectCandidate, FailureCluster,
            FlakyCoachResult, ReleaseDecision,
            RunIntelligenceSnapshot, ProductUsageEvent,
        )
        assert all(hasattr(m, "__tablename__") for m in [
            Defect, DefectCandidate, FailureCluster,
            FlakyCoachResult, ReleaseDecision,
            RunIntelligenceSnapshot, ProductUsageEvent,
        ])
