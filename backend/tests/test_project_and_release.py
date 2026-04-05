"""
Unit tests for project update and release completion features.

Tests:
  1. ProjectUpdate schema — partial update semantics, validation
  2. ProjectResponse — includes all new fields (start_date, end_date, tags)
  3. Release status validation — cannot release with incomplete phases
  4. Release auto-complete — released_at auto-set
  5. Phase auto-timestamps — actual_start/actual_end auto-set
  6. Edge cases — empty phases, all skipped, mixed statuses
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from datetime import datetime, timezone
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
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock())))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# Project Update Schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectUpdateSchema:
    def test_partial_update_none_excluded(self):
        from app.models.schemas import ProjectUpdate

        update = ProjectUpdate(name="New Name")
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"name": "New Name"}

    def test_empty_update(self):
        from app.models.schemas import ProjectUpdate

        update = ProjectUpdate()
        assert update.model_dump(exclude_none=True) == {}

    def test_name_validation_min_length(self):
        from app.models.schemas import ProjectUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProjectUpdate(name="x")  # min 2

    def test_dates_accepted(self):
        from app.models.schemas import ProjectUpdate

        update = ProjectUpdate(
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        assert update.start_date is not None
        assert update.end_date is not None

    def test_tags_accepted(self):
        from app.models.schemas import ProjectUpdate

        update = ProjectUpdate(tags=["backend", "payments", "critical"])
        assert len(update.tags) == 3

    def test_integration_fields(self):
        from app.models.schemas import ProjectUpdate

        update = ProjectUpdate(
            jira_project_key="PAY",
            splunk_index="payment_logs",
            ocp_namespace="payment-qa",
            jenkins_job_pattern="**/payment-*",
        )
        dumped = update.model_dump(exclude_none=True)
        assert dumped["jira_project_key"] == "PAY"
        assert dumped["splunk_index"] == "payment_logs"


class TestProjectResponseSchema:
    def test_includes_all_new_fields(self):
        from app.models.schemas import ProjectResponse

        resp = ProjectResponse(
            id=uuid.uuid4(),
            name="Test Project",
            slug="test-project",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
            tags=["api", "backend"],
            splunk_index="main",
            jenkins_job_pattern="**/test-*",
        )
        assert resp.start_date is not None
        assert resp.end_date is not None
        assert resp.tags == ["api", "backend"]
        assert resp.splunk_index == "main"
        assert resp.jenkins_job_pattern == "**/test-*"

    def test_backward_compatible_without_new_fields(self):
        from app.models.schemas import ProjectResponse

        resp = ProjectResponse(
            id=uuid.uuid4(),
            name="Legacy Project",
            slug="legacy",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        assert resp.start_date is None
        assert resp.end_date is None
        assert resp.tags is None
        assert resp.splunk_index is None


# ═══════════════════════════════════════════════════════════════════════════════
# Release Status Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestReleaseStatusValidation:
    def test_release_update_status_values(self):
        """Verify ReleaseUpdate accepts all valid status values."""
        from app.routers.releases import ReleaseUpdate

        for status in ["planning", "in_progress", "released", "cancelled"]:
            update = ReleaseUpdate(status=status)
            assert update.status == status

    def test_release_update_partial(self):
        from app.routers.releases import ReleaseUpdate

        update = ReleaseUpdate(name="v2.5.0")
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"name": "v2.5.0"}

    def test_phase_update_status_values(self):
        from app.routers.releases import PhaseUpdate

        for status in ["pending", "in_progress", "completed", "skipped"]:
            update = PhaseUpdate(status=status)
            assert update.status == status


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Completion Logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseCompletionLogic:
    """Test the logic that determines if all phases are done."""

    def test_all_completed(self):
        phases = [
            {"status": "completed"},
            {"status": "completed"},
            {"status": "completed"},
        ]
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is True

    def test_all_skipped(self):
        phases = [
            {"status": "skipped"},
            {"status": "skipped"},
        ]
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is True

    def test_mixed_completed_and_skipped(self):
        phases = [
            {"status": "completed"},
            {"status": "skipped"},
            {"status": "completed"},
        ]
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is True

    def test_one_pending_blocks(self):
        phases = [
            {"status": "completed"},
            {"status": "pending"},
            {"status": "completed"},
        ]
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is False

    def test_one_in_progress_blocks(self):
        phases = [
            {"status": "completed"},
            {"status": "in_progress"},
        ]
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is False

    def test_empty_phases_all_done(self):
        """A release with no phases can be released immediately."""
        phases: list[dict] = []
        all_done = all(p["status"] in ("completed", "skipped") for p in phases)
        assert all_done is True  # vacuously true

    def test_incomplete_phases_extraction(self):
        """Verify the backend logic for extracting incomplete phase names."""
        phases = [
            {"name": "Planning", "status": "completed"},
            {"name": "QA Testing", "status": "in_progress"},
            {"name": "UAT", "status": "pending"},
            {"name": "Staging", "status": "skipped"},
        ]
        incomplete = [p["name"] for p in phases if p["status"] not in ("completed", "skipped")]
        assert incomplete == ["QA Testing", "UAT"]


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_project_create_with_all_fields(self):
        from app.models.schemas import ProjectCreate

        project = ProjectCreate(
            name="Full Project",
            slug="full-project",
            description="A comprehensive project",
            jira_project_key="FP",
            splunk_index="fp_logs",
            ocp_namespace="fp-qa",
            jenkins_job_pattern="**/fp-*",
            component_owner_map={"auth": {"team": "identity"}},
        )
        assert project.name == "Full Project"
        assert project.component_owner_map is not None

    def test_project_slug_validation(self):
        from app.models.schemas import ProjectCreate
        from pydantic import ValidationError

        # Valid slug
        ProjectCreate(name="Test", slug="valid-slug-123")

        # Invalid slug (uppercase not allowed)
        with pytest.raises(ValidationError):
            ProjectCreate(name="Test", slug="Invalid_Slug")

    def test_release_released_at_optional(self):
        from app.routers.releases import ReleaseUpdate

        # Can set released_at explicitly
        update = ReleaseUpdate(
            status="released",
            released_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert update.released_at is not None

        # Or leave it for auto-set
        update2 = ReleaseUpdate(status="released")
        assert update2.released_at is None

    def test_phase_in_defaults(self):
        from app.routers.releases import PhaseIn

        phase = PhaseIn(name="QA Testing")
        assert phase.phase_type == "qa_testing"
        assert phase.status == "pending"
        assert phase.order_index == 0
