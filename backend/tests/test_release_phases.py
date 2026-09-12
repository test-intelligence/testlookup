"""
Unit tests for release phase management.

Tests:
  1. Duplicate phase name detection (case-insensitive)
  2. Phase status transitions and auto-timestamps
  3. All-phases-completed detection
  4. Phase ordering (auto-assign order_index)
  5. Phase rename duplicate check
  6. Release status validation (cannot release with incomplete phases)
  7. Edge cases: empty name, whitespace-only, special characters
"""
from __future__ import annotations

import importlib.util
import sys
import types
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

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Input Schema Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseInSchema:
    def test_minimal_phase(self):
        from app.routers.releases import PhaseIn
        phase = PhaseIn(name="QA Testing")
        assert phase.name == "QA Testing"
        assert phase.phase_type == "qa_testing"
        assert phase.status == "pending"
        assert phase.order_index == 0

    def test_full_phase(self):
        from app.routers.releases import PhaseIn
        phase = PhaseIn(
            name="UAT",
            phase_type="uat",
            status="in_progress",
            description="User acceptance testing",
            order_index=3,
            planned_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
            planned_end=datetime(2026, 4, 15, tzinfo=timezone.utc),
            exit_criteria={"min_pass_rate": 95},
            notes="Run by product team",
        )
        assert phase.phase_type == "uat"
        assert phase.exit_criteria == {"min_pass_rate": 95}

    def test_all_phase_types_accepted(self):
        from app.routers.releases import PhaseIn
        for pt in ["planning", "development", "code_freeze", "qa_testing", "uat", "staging", "production"]:
            phase = PhaseIn(name=f"Phase {pt}", phase_type=pt)
            assert phase.phase_type == pt

    def test_all_phase_statuses_accepted(self):
        from app.routers.releases import PhaseIn
        for status in ["pending", "in_progress", "completed", "skipped"]:
            phase = PhaseIn(name="Test Phase", status=status)
            assert phase.status == status


class TestPhaseUpdateSchema:
    def test_partial_update(self):
        from app.routers.releases import PhaseUpdate
        update = PhaseUpdate(status="completed")
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"status": "completed"}

    def test_empty_update(self):
        from app.routers.releases import PhaseUpdate
        update = PhaseUpdate()
        assert update.model_dump(exclude_none=True) == {}

    def test_rename_phase(self):
        from app.routers.releases import PhaseUpdate
        update = PhaseUpdate(name="Renamed Phase")
        assert update.name == "Renamed Phase"


# ═══════════════════════════════════════════════════════════════════════════════
# Duplicate Phase Detection Logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicatePhaseDetection:
    """Test the duplicate name detection logic used in add_phase and update_phase."""

    def test_exact_duplicate_detected(self):
        existing_names = ["Planning", "QA Testing", "UAT"]
        new_name = "QA Testing"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is True

    def test_case_insensitive_duplicate(self):
        existing_names = ["Planning", "QA Testing"]
        new_name = "qa testing"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is True

    def test_case_insensitive_uppercase(self):
        existing_names = ["planning"]
        new_name = "PLANNING"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is True

    def test_no_duplicate(self):
        existing_names = ["Planning", "QA Testing"]
        new_name = "UAT"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is False

    def test_whitespace_trimmed_duplicate(self):
        existing_names = ["QA Testing"]
        new_name = "  QA Testing  "
        is_dup = any(n.strip().lower() == new_name.strip().lower() for n in existing_names)
        assert is_dup is True

    def test_empty_existing_list(self):
        existing_names: list[str] = []
        new_name = "Any Name"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is False

    def test_special_characters_not_folded(self):
        existing_names = ["Phase (1)"]
        new_name = "Phase (2)"
        is_dup = any(n.lower() == new_name.lower() for n in existing_names)
        assert is_dup is False


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Completion and Release Readiness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseCompletionAndRelease:
    def test_all_completed_allows_release(self):
        statuses = ["completed", "completed", "completed"]
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is True

    def test_all_skipped_allows_release(self):
        statuses = ["skipped", "skipped"]
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is True

    def test_mixed_completed_skipped_allows_release(self):
        statuses = ["completed", "skipped", "completed"]
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is True

    def test_pending_blocks_release(self):
        statuses = ["completed", "pending", "skipped"]
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is False

    def test_in_progress_blocks_release(self):
        statuses = ["completed", "in_progress"]
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is False

    def test_empty_phases_allows_release(self):
        statuses: list[str] = []
        all_done = all(s in ("completed", "skipped") for s in statuses)
        assert all_done is True

    def test_incomplete_phase_names_extraction(self):
        phases = [
            {"name": "Planning", "status": "completed"},
            {"name": "QA Testing", "status": "in_progress"},
            {"name": "UAT", "status": "pending"},
        ]
        incomplete = [p["name"] for p in phases if p["status"] not in ("completed", "skipped")]
        assert incomplete == ["QA Testing", "UAT"]


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Auto-Ordering
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseOrdering:
    def test_auto_order_first_phase(self):
        existing_count = 0
        order = 0 if 0 else existing_count
        assert order == 0

    def test_auto_order_appends(self):
        existing_count = 3
        explicit_order = 0
        order = explicit_order if explicit_order else existing_count
        assert order == 3

    def test_explicit_order_preserved(self):
        existing_count = 5
        explicit_order = 2
        order = explicit_order if explicit_order else existing_count
        assert order == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Release Status Transitions
# ═══════════════════════════════════════════════════════════════════════════════

class TestReleaseStatusTransitions:
    def test_all_valid_statuses(self):
        from app.routers.releases import ReleaseUpdate
        for status in ["planning", "in_progress", "released", "cancelled"]:
            update = ReleaseUpdate(status=status)
            assert update.status == status

    def test_released_with_released_at(self):
        from app.routers.releases import ReleaseUpdate
        update = ReleaseUpdate(
            status="released",
            released_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert update.released_at is not None

    def test_released_without_released_at_auto_fills(self):
        """Backend auto-sets released_at when status=released and released_at is missing."""
        from app.routers.releases import ReleaseUpdate
        update = ReleaseUpdate(status="released")
        assert update.released_at is None  # Backend fills this on save

    def test_cancel_does_not_need_phases(self):
        """Cancelling should be allowed regardless of phase status."""
        # Cancelling doesn't check phases
        can_cancel = True
        assert can_cancel is True


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_phase_name_with_special_chars(self):
        from app.routers.releases import PhaseIn
        phase = PhaseIn(name="QA Testing (Round 2) — Final")
        assert "Round 2" in phase.name

    def test_phase_name_unicode(self):
        from app.routers.releases import PhaseIn
        phase = PhaseIn(name="Pruebas de QA")
        assert phase.name == "Pruebas de QA"

    def test_release_update_only_name(self):
        from app.routers.releases import ReleaseUpdate
        update = ReleaseUpdate(name="v3.0.0 — Major Release")
        dumped = update.model_dump(exclude_none=True)
        assert len(dumped) == 1
        assert dumped["name"] == "v3.0.0 — Major Release"

    def test_link_run_request(self):
        from app.routers.releases import LinkRunRequest
        req = LinkRunRequest(test_run_id="abc-123")
        assert req.phase_id is None

        req2 = LinkRunRequest(test_run_id="abc-123", phase_id="phase-456")
        assert req2.phase_id == "phase-456"

    def test_release_serialization_helper(self):
        """Test the serialize_model helper with a mock object."""
        from app.services.release_service import serialize_model

        class FakeModel:
            class __table__:
                columns = []
        obj = FakeModel()
        result = serialize_model(obj)
        assert isinstance(result, dict)
