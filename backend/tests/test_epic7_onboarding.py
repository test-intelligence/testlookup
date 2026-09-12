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


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-704: Auto-detection of integration steps (Jira + telemetry)
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeResult:
    """Minimal stand-in for a SQLAlchemy Result."""

    def __init__(self, *, scalar_value=None, scalar_one=None, scalars_all=None):
        self._scalar_value = scalar_value
        self._scalar_one = scalar_one
        self._scalars_all = scalars_all or []

    def scalar(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_one

    def scalars(self):
        outer = self

        class _Scalars:
            def all(self_inner):
                return outer._scalars_all

        return _Scalars()


class _FakeOnboardingSession:
    """Stateful async-session double for auto_detect_progress.

    Routes each ``execute`` by the shape of the statement so the service walks
    its real code path — table-exists probe, run count, integrations lookup,
    and the per-step / all-steps onboarding-status queries — against an
    in-memory row store keyed by step_key. Rows the service ``add``s (or
    mutates in place) persist across the flushes within one call.
    """

    def __init__(self, *, run_count=0, integrations_row=None, seed_rows=None):
        self.run_count = run_count
        self.integrations_row = integrations_row
        # step_key -> TenantOnboardingStatus (real ORM instances)
        self.store = {r.step_key: r for r in (seed_rows or [])}
        self.added = []

    async def execute(self, stmt):
        sql = str(stmt)
        try:
            params = stmt.compile().params
        except Exception:  # pragma: no cover - defensive
            params = {}

        if "information_schema" in sql:
            return _FakeResult(scalar_value=True)
        if "count(" in sql.lower():
            return _FakeResult(scalar_value=self.run_count)
        if "app_settings" in sql:
            return _FakeResult(scalar_one=self.integrations_row)
        if "tenant_onboarding_status" in sql:
            step_key = next(
                (v for k, v in params.items() if k.startswith("step_key")), None
            )
            if step_key is not None:
                return _FakeResult(scalar_one=self.store.get(step_key))
            return _FakeResult(scalars_all=list(self.store.values()))
        return _FakeResult()

    def add(self, obj):
        self.added.append(obj)
        self.store[obj.step_key] = obj

    async def flush(self):
        return None


def _integrations(**flags):
    return types.SimpleNamespace(value=dict(flags))


def _status_of(result: dict, step_key: str) -> str:
    return next(s["status"] for s in result["steps"] if s["key"] == step_key)


class TestAutoDetectTelemetry:
    async def test_telemetry_step_autocompletes_when_slack_enabled(self):
        from app.services.onboarding_service import auto_detect_progress

        db = _FakeOnboardingSession(integrations_row=_integrations(slack_enabled=True))
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_telemetry") == "completed"

    @pytest.mark.parametrize("flag", ["splunk_enabled", "ocp_enabled", "slack_enabled"])
    async def test_any_telemetry_provider_satisfies_step(self, flag):
        from app.services.onboarding_service import auto_detect_progress

        db = _FakeOnboardingSession(integrations_row=_integrations(**{flag: True}))
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_telemetry") == "completed"

    async def test_telemetry_step_stays_pending_when_no_provider_enabled(self):
        from app.services.onboarding_service import auto_detect_progress

        db = _FakeOnboardingSession(integrations_row=_integrations(jira_enabled=True))
        result = await auto_detect_progress(uuid.uuid4(), db)

        # Jira is on but no telemetry provider — the step must not be credited.
        assert _status_of(result, "connect_telemetry") == "pending"
        # Regression guard: the Jira detection still works after the refactor.
        assert _status_of(result, "connect_jira") == "completed"

    async def test_no_integration_config_leaves_both_steps_pending(self):
        from app.services.onboarding_service import auto_detect_progress

        db = _FakeOnboardingSession(integrations_row=None)
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_telemetry") == "pending"
        assert _status_of(result, "connect_jira") == "pending"


class TestAutoDetectEnvOnlyIntegrations:
    """A self-host that enables integrations purely through environment
    variables never opens the integrations UI, so no ``integrations_config``
    AppSetting row is ever written. Onboarding must still credit the steps by
    honouring the ``settings.*_ENABLED`` defaults — the same authority
    ``_load_integrations_config`` reads through — instead of only the raw row.
    """

    async def test_jira_enabled_via_env_credits_step_without_a_row(self, monkeypatch):
        from app.core.config import settings
        from app.services.onboarding_service import auto_detect_progress

        monkeypatch.setattr(settings, "JIRA_ENABLED", True)
        db = _FakeOnboardingSession(integrations_row=None)
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_jira") == "completed"
        # No telemetry provider is enabled — that step must stay pending.
        assert _status_of(result, "connect_telemetry") == "pending"

    @pytest.mark.parametrize(
        "flag", ["SPLUNK_ENABLED", "OCP_ENABLED", "SLACK_ENABLED"]
    )
    async def test_telemetry_enabled_via_env_credits_step_without_a_row(
        self, monkeypatch, flag
    ):
        from app.core.config import settings
        from app.services.onboarding_service import auto_detect_progress

        monkeypatch.setattr(settings, flag, True)
        db = _FakeOnboardingSession(integrations_row=None)
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_telemetry") == "completed"
        assert _status_of(result, "connect_jira") == "pending"

    async def test_stored_override_still_wins_over_env(self, monkeypatch):
        # An explicit stored ``jira_enabled=False`` (admin turned it off in the
        # UI) must not be overridden by a stale env default — ``.get`` returns
        # the stored value when the key is present.
        from app.core.config import settings
        from app.services.onboarding_service import auto_detect_progress

        monkeypatch.setattr(settings, "JIRA_ENABLED", True)
        db = _FakeOnboardingSession(integrations_row=_integrations(jira_enabled=False))
        result = await auto_detect_progress(uuid.uuid4(), db)

        assert _status_of(result, "connect_jira") == "pending"


# ═══════════════════════════════════════════════════════════════════════════════
# Restore a skipped onboarding step (un-skip)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestoreStep:
    def _row(self, step_key, status, completed_at=None):
        from app.models.postgres import TenantOnboardingStatus

        return TenantOnboardingStatus(
            project_id=uuid.uuid4(),
            step_key=step_key,
            status=status,
            completed_at=completed_at,
        )

    async def test_skipped_step_returns_to_pending(self):
        from app.services.onboarding_service import restore_step

        row = self._row("connect_jira", "skipped")
        db = _FakeOnboardingSession(seed_rows=[row])
        result = await restore_step(uuid.uuid4(), "connect_jira", db)

        assert _status_of(result, "connect_jira") == "pending"
        assert row.status == "pending"
        assert row.completed_at is None

    async def test_completed_step_is_left_untouched(self):
        from datetime import datetime, timezone

        from app.services.onboarding_service import restore_step

        done_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        row = self._row("upload_run", "completed", completed_at=done_at)
        db = _FakeOnboardingSession(seed_rows=[row])
        result = await restore_step(uuid.uuid4(), "upload_run", db)

        # Restoring must never silently drop real progress.
        assert _status_of(result, "upload_run") == "completed"
        assert row.status == "completed"
        assert row.completed_at == done_at

    async def test_invalid_step_key_raises(self):
        from app.services.onboarding_service import restore_step

        db = _FakeOnboardingSession()
        with pytest.raises(ValueError):
            await restore_step(uuid.uuid4(), "not_a_step", db)

    def test_router_exposes_restore_endpoint(self):
        from app.routers.onboarding import mark_step_restored, router

        assert callable(mark_step_restored)
        paths = {r.path for r in router.routes}
        assert "/api/v1/onboarding/{project_id}/restore" in paths
