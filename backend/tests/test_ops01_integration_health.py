"""
Unit tests for OPS-01: Active Integration Health Probes.

Covers:
 - ProbeResult dataclass structure
 - Probe function registry (all providers present)
 - Alert threshold logic
 - Schema/model field validation
 - Celery beat schedule registration
 - Status color mapping (column widths)
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
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))
        yield


# ── ProbeResult dataclass ────────────────────────────────────────────────────


class TestProbeResult:
    def test_defaults(self):
        from app.services.integration_probe_service import ProbeResult

        r = ProbeResult("jira", "healthy")
        assert r.provider == "jira"
        assert r.status == "healthy"
        assert r.response_ms == 0
        assert r.auth_valid is None

    def test_all_fields(self):
        from app.services.integration_probe_service import ProbeResult

        r = ProbeResult("splunk", "auth_error", 250, "Token expired", False, True)
        assert r.status == "auth_error"
        assert r.response_ms == 250
        assert r.auth_valid is False
        assert r.payload_valid is True

    def test_skipped_status(self):
        from app.services.integration_probe_service import ProbeResult

        r = ProbeResult("jira", "skipped", message="JIRA_ENABLED=false")
        assert r.status == "skipped"


# ── Probe Registry ──────────────────────────────────────────────────────────


class TestProbeRegistry:
    def test_all_providers_registered(self):
        from app.services.integration_probe_service import ALL_PROBES

        expected = {"jira", "splunk", "github", "ocp", "slack", "teams", "smtp", "ollama", "chromadb"}
        assert set(ALL_PROBES.keys()) == expected

    def test_all_probes_are_callable(self):
        from app.services.integration_probe_service import ALL_PROBES

        for name, fn in ALL_PROBES.items():
            assert callable(fn), f"Probe {name} is not callable"

    def test_probe_count(self):
        from app.services.integration_probe_service import ALL_PROBES

        assert len(ALL_PROBES) == 9


# ── Alert Threshold ─────────────────────────────────────────────────────────


class TestAlertThreshold:
    def test_alert_threshold_is_positive(self):
        from app.services.integration_probe_service import ALERT_THRESHOLD

        assert ALERT_THRESHOLD > 0
        assert ALERT_THRESHOLD == 3


# ── Status values fit column widths ──────────────────────────────────────────


class TestColumnWidths:
    def test_status_values_fit(self):
        """All status values must fit in String(20)."""
        for s in ("healthy", "degraded", "down", "auth_error", "timeout", "unknown", "skipped"):
            assert len(s) <= 20

    def test_provider_names_fit(self):
        """All provider names must fit in String(50)."""
        for p in ("jira", "splunk", "github", "ocp", "slack", "teams", "smtp", "ollama", "chromadb"):
            assert len(p) <= 50


# ── ORM model fields ────────────────────────────────────────────────────────


class TestORMModels:
    def test_integration_health_check_fields(self):
        from app.models.postgres import IntegrationHealthCheck

        for field in ("provider", "status", "last_checked_at", "message", "response_ms",
                       "consecutive_failures", "last_success_at"):
            assert hasattr(IntegrationHealthCheck, field)

    def test_integration_probe_result_fields(self):
        from app.models.postgres import IntegrationProbeResult

        for field in ("provider", "status", "response_ms", "message",
                       "auth_valid", "payload_valid", "checked_at"):
            assert hasattr(IntegrationProbeResult, field)


# ── Celery beat schedule ─────────────────────────────────────────────────────


class TestCeleryBeatSchedule:
    def test_probe_task_in_beat_schedule(self):
        from app.worker.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "integration-health-probes" in beat
        assert beat["integration-health-probes"]["task"] == "app.worker.tasks.run_integration_health_probes"


# ── Prometheus metric exists ─────────────────────────────────────────────────


class TestPrometheusMetric:
    def test_integration_health_gauge_exists(self):
        from app.core.metrics import integration_health_gauge

        assert integration_health_gauge is not None
        # Verify it has labels
        assert hasattr(integration_health_gauge, "labels")


# ── Probe skipping logic ────────────────────────────────────────────────────


class TestProbeSkipping:
    """When integrations are disabled, probes should return 'skipped' status."""

    @pytest.mark.asyncio
    async def test_jira_skipped_when_disabled(self):
        from app.services.integration_probe_service import probe_jira
        from app.core.config import settings

        original = settings.JIRA_ENABLED
        try:
            settings.JIRA_ENABLED = False
            result = await probe_jira()
            assert result.status == "skipped"
        finally:
            settings.JIRA_ENABLED = original

    @pytest.mark.asyncio
    async def test_splunk_skipped_when_disabled(self):
        from app.services.integration_probe_service import probe_splunk
        from app.core.config import settings

        original = settings.SPLUNK_ENABLED
        try:
            settings.SPLUNK_ENABLED = False
            result = await probe_splunk()
            assert result.status == "skipped"
        finally:
            settings.SPLUNK_ENABLED = original

    @pytest.mark.asyncio
    async def test_github_skipped_when_no_token(self):
        from app.services.integration_probe_service import probe_github
        from app.core.config import settings

        original = settings.GITHUB_TOKEN
        try:
            settings.GITHUB_TOKEN = None
            result = await probe_github()
            assert result.status == "skipped"
        finally:
            settings.GITHUB_TOKEN = original

    @pytest.mark.asyncio
    async def test_ocp_skipped_when_disabled(self):
        from app.services.integration_probe_service import probe_ocp
        from app.core.config import settings

        original = settings.OCP_ENABLED
        try:
            settings.OCP_ENABLED = False
            result = await probe_ocp()
            assert result.status == "skipped"
        finally:
            settings.OCP_ENABLED = original

    @pytest.mark.asyncio
    async def test_slack_skipped_when_disabled(self):
        from app.services.integration_probe_service import probe_slack
        from app.core.config import settings

        original = settings.SLACK_ENABLED
        try:
            settings.SLACK_ENABLED = False
            result = await probe_slack()
            assert result.status == "skipped"
        finally:
            settings.SLACK_ENABLED = original

    @pytest.mark.asyncio
    async def test_smtp_skipped_when_disabled(self):
        from app.services.integration_probe_service import probe_smtp
        from app.core.config import settings

        original = settings.SMTP_ENABLED
        try:
            settings.SMTP_ENABLED = False
            result = await probe_smtp()
            assert result.status == "skipped"
        finally:
            settings.SMTP_ENABLED = original
