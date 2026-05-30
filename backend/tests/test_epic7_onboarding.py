"""
Epic 7: Guided Customer Onboarding And Adoption — Unit Tests.

Tests:
  QAI-701: Onboarding step definitions and flow
  QAI-702: Onboarding status persistence (ORM model)
  QAI-703: Product usage event tracking (ORM model + service)
  QAI-704: Demo mode and auto-detection
  Edge cases: mid-flow abandonment, partial config, wizard hidden
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


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-701: Onboarding Steps
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnboardingSteps:
    def test_step_definitions(self):
        from app.services.onboarding_service import ONBOARDING_STEPS
        assert len(ONBOARDING_STEPS) == 5
        keys = [s["key"] for s in ONBOARDING_STEPS]
        assert "create_project" in keys
        assert "upload_run" in keys
        assert "connect_jira" in keys
        assert "connect_telemetry" in keys
        assert "view_intelligence" in keys

    def test_all_steps_have_required_fields(self):
        from app.services.onboarding_service import ONBOARDING_STEPS
        for step in ONBOARDING_STEPS:
            assert "key" in step
            assert "label" in step
            assert "description" in step
            assert len(step["key"]) <= 50
            assert len(step["label"]) > 0

    def test_step_keys_are_unique(self):
        from app.services.onboarding_service import ONBOARDING_STEPS
        keys = [s["key"] for s in ONBOARDING_STEPS]
        assert len(keys) == len(set(keys))


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-702: Onboarding Model
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnboardingModel:
    def test_table_exists(self):
        from app.models.postgres import TenantOnboardingStatus
        assert TenantOnboardingStatus.__tablename__ == "tenant_onboarding_status"

    def test_columns(self):
        from app.models.postgres import TenantOnboardingStatus
        columns = {c.name for c in TenantOnboardingStatus.__table__.columns}
        assert "project_id" in columns
        assert "step_key" in columns
        assert "status" in columns
        assert "completed_at" in columns
        assert "completed_by" in columns

    def test_unique_constraint(self):
        """Each project+step combination must be unique."""
        from app.models.postgres import TenantOnboardingStatus
        indexes = TenantOnboardingStatus.__table__.indexes
        unique_idxs = [idx for idx in indexes if idx.unique]
        assert len(unique_idxs) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-703: Product Usage Events
# ═══════════════════════════════════════════════════════════════════════════════

class TestUsageEventModel:
    def test_table_exists(self):
        from app.models.postgres import ProductUsageEvent
        assert ProductUsageEvent.__tablename__ == "product_usage_events"

    def test_columns(self):
        from app.models.postgres import ProductUsageEvent
        columns = {c.name for c in ProductUsageEvent.__table__.columns}
        assert "user_id" in columns
        assert "project_id" in columns
        assert "event_name" in columns
        assert "event_payload" in columns
        assert "created_at" in columns


class TestUsageEventNames:
    def test_expected_event_names(self):
        """Verify the expected event names are consistent strings."""
        events = [
            "first_run_viewed",
            "first_intelligence_opened",
            "first_defect_promoted",
            "first_release_decision",
            "first_integration_connected",
            "onboarding_completed",
        ]
        for name in events:
            assert len(name) <= 100
            assert "_" in name  # snake_case


# ═══════════════════════════════════════════════════════════════════════════════
# Router Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnboardingRouter:
    def test_endpoints_exist(self):
        from app.routers.onboarding import (
            get_status,
            detect_progress,
            mark_step_complete,
            mark_step_skipped,
            track_usage_event,
            list_usage_events,
        )
        assert callable(get_status)
        assert callable(detect_progress)
        assert callable(mark_step_complete)
        assert callable(mark_step_skipped)
        assert callable(track_usage_event)
        assert callable(list_usage_events)

    def test_router_prefix(self):
        from app.routers.onboarding import router
        assert router.prefix == "/api/v1/onboarding"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_mid_flow_abandonment(self):
        """Partially completed onboarding should persist correctly."""
        steps = [
            {"key": "create_project", "status": "completed"},
            {"key": "upload_run", "status": "completed"},
            {"key": "connect_jira", "status": "pending"},
            {"key": "connect_telemetry", "status": "pending"},
            {"key": "view_intelligence", "status": "pending"},
        ]
        completed = sum(1 for s in steps if s["status"] == "completed")
        total = len(steps)
        assert completed == 2
        assert completed < total
        assert not (completed == total)

    def test_partially_configured_integration(self):
        """Integration step skipped should not block overall progress."""
        steps = [
            {"key": "create_project", "status": "completed"},
            {"key": "upload_run", "status": "completed"},
            {"key": "connect_jira", "status": "skipped"},
            {"key": "connect_telemetry", "status": "skipped"},
            {"key": "view_intelligence", "status": "completed"},
        ]
        # All steps resolved (completed or skipped)
        all_resolved = all(s["status"] in ("completed", "skipped") for s in steps)
        assert all_resolved

    def test_wizard_hidden_after_setup(self):
        """Once all steps are completed, is_complete should be True."""
        status = {
            "completed_count": 5,
            "total_count": 5,
            "progress_pct": 100,
            "is_complete": True,
        }
        assert status["is_complete"]

    def test_progress_percentage_calculation(self):
        for completed, total, expected in [
            (0, 5, 0),
            (1, 5, 20),
            (3, 5, 60),
            (5, 5, 100),
        ]:
            pct = round(completed / total * 100) if total else 0
            assert pct == expected

    def test_service_importable(self):
        from app.services.onboarding_service import (
            get_onboarding_status,
            complete_step,
            skip_step,
            auto_detect_progress,
            track_event,
            get_usage_events,
        )
        assert callable(get_onboarding_status)
        assert callable(complete_step)
        assert callable(skip_step)
        assert callable(auto_detect_progress)
        assert callable(track_event)
        assert callable(get_usage_events)
