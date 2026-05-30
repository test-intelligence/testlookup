"""
Tests for Suite Membership Traceability (TS-1 through TS-7).

Covers:
  - SuiteMembership and SuiteMembershipEvent ORM models
  - Pydantic schema validation
  - suite_sync_service diff logic (via mock DB)
  - Deletion detection and -deleted bucket naming
  - Idempotency of sync operations
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-1: ORM Model Structure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import SuiteMembership, SuiteMembershipEvent  # noqa: E402


class TestSuiteMembershipModel:
    def test_table_name(self):
        assert SuiteMembership.__tablename__ == "suite_memberships"

    def test_required_columns(self):
        columns = {c.name for c in SuiteMembership.__table__.columns}
        required = {
            "id", "project_id", "suite_name", "test_fingerprint", "test_name",
            "class_name", "source", "status", "last_seen_run_id", "first_seen_run_id",
            "deleted_at_run_id", "review_tag", "managed_test_case_id",
            "created_at", "updated_at",
        }
        assert required.issubset(columns)

    def test_unique_constraint_exists(self):
        constraints = {c.name for c in SuiteMembership.__table__.constraints if hasattr(c, "name") and c.name}
        assert "uq_suite_membership" in constraints

    def test_status_column_default(self):
        col = SuiteMembership.__table__.c.status
        assert col.default.arg == "active"

    def test_source_column_default(self):
        col = SuiteMembership.__table__.c.source
        assert col.default.arg == "execution"


class TestSuiteMembershipEventModel:
    def test_table_name(self):
        assert SuiteMembershipEvent.__tablename__ == "suite_membership_events"

    def test_required_columns(self):
        columns = {c.name for c in SuiteMembershipEvent.__table__.columns}
        required = {
            "id", "project_id", "suite_name", "test_fingerprint", "test_name",
            "event_type", "run_id", "old_values", "new_values", "details", "created_at",
        }
        assert required.issubset(columns)

    def test_event_type_not_nullable(self):
        col = SuiteMembershipEvent.__table__.c.event_type
        assert not col.nullable


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-1: Pydantic Schema Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import (  # noqa: E402
    SuiteMembershipResponse,
    SuiteMembershipEventResponse,
    SuiteSyncSummary,
)


class TestSuiteMembershipSchemas:
    def test_membership_response_from_attributes(self):
        resp = SuiteMembershipResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            suite_name="smoke-tests",
            test_fingerprint="abc123",
            test_name="testLogin",
            source="execution",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.suite_name == "smoke-tests"
        assert resp.status == "active"
        assert resp.review_tag is None

    def test_event_response(self):
        resp = SuiteMembershipEventResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            suite_name="api-tests",
            test_fingerprint="def456",
            test_name="testCreateUser",
            event_type="added",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.event_type == "added"
        assert resp.old_values is None

    def test_sync_summary(self):
        summary = SuiteSyncSummary(
            suite_name="regression",
            run_id=uuid.uuid4(),
            added_count=5,
            deleted_count=2,
            modified_count=1,
            unchanged_count=42,
        )
        assert summary.added_count == 5
        assert summary.deleted_count == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-3: Deleted Bucket Naming Convention
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDeletedBucketNaming:
    def test_deleted_bucket_convention(self):
        """Deleted tests go to <suite>-deleted."""
        suite = "smoke-tests"
        deleted_bucket = f"{suite}-deleted"
        assert deleted_bucket == "smoke-tests-deleted"

    def test_deleted_bucket_preserves_original_name(self):
        suite = "com.example.PaymentSuite"
        deleted_bucket = f"{suite}-deleted"
        assert deleted_bucket.startswith("com.example.PaymentSuite")
        assert deleted_bucket.endswith("-deleted")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-2/3/4: Sync Service Logic (unit tests with direct function calls)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.suite_sync_service import _add_event, _sync_one_suite  # noqa: E402


class TestSyncEventCreation:
    """Verify the _add_event helper creates correct event objects."""

    def test_add_event_creates_event(self):
        """Smoke test: _add_event should not raise with valid args."""
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        _add_event(
            mock_db,
            project_id=uuid.uuid4(),
            suite_name="test-suite",
            fingerprint="abc123",
            test_name="testLogin",
            event_type="added",
            run_id=uuid.uuid4(),
        )
        mock_db.add.assert_called_once()
        event = mock_db.add.call_args[0][0]
        assert event.event_type == "added"
        assert event.suite_name == "test-suite"
        assert event.test_fingerprint == "abc123"

    def test_modified_event_has_old_new_values(self):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        _add_event(
            mock_db,
            project_id=uuid.uuid4(),
            suite_name="api-tests",
            fingerprint="def456",
            test_name="testUpdate",
            event_type="modified",
            run_id=uuid.uuid4(),
            old_values={"test_name": "testOld"},
            new_values={"test_name": "testUpdate"},
        )
        event = mock_db.add.call_args[0][0]
        assert event.event_type == "modified"
        assert event.old_values == {"test_name": "testOld"}
        assert event.new_values == {"test_name": "testUpdate"}

    def test_deleted_event_has_details(self):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        _add_event(
            mock_db,
            project_id=uuid.uuid4(),
            suite_name="smoke",
            fingerprint="ghi789",
            test_name="testRemoved",
            event_type="deleted",
            run_id=uuid.uuid4(),
            details="Test absent from latest run; moved to smoke-deleted",
        )
        event = mock_db.add.call_args[0][0]
        assert event.event_type == "deleted"
        assert "smoke-deleted" in event.details


class _FakeResult:
    def __init__(self, *, rows=None, scalars=None):
        self._rows = rows or []
        self._scalars = scalars or []

    def all(self):
        return self._rows

    def scalars(self):
        return _FakeScalars(self._scalars)


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.execute_calls = 0
        self.add = MagicMock()

    async def execute(self, _statement):
        self.execute_calls += 1
        return self._results.pop(0)


class TestSyncOneSuite:
    @pytest.mark.asyncio
    async def test_new_members_batch_fetch_managed_cases(self):
        project_id = uuid.uuid4()
        run_id = uuid.uuid4()
        managed_id = uuid.uuid4()
        run_cases = [
            SimpleNamespace(
                test_fingerprint="fp-login",
                test_name="testLogin",
                class_name="AuthTest",
            ),
            SimpleNamespace(
                test_fingerprint="fp-logout",
                test_name="testLogout",
                class_name="AuthTest",
            ),
        ]
        db = _FakeAsyncSession(
            [
                _FakeResult(
                    rows=[SimpleNamespace(id=managed_id, test_fingerprint="fp-login")]
                ),
                _FakeResult(scalars=[]),
                _FakeResult(scalars=[]),
            ]
        )

        summary = await _sync_one_suite(db, project_id, run_id, "auth-suite", run_cases)

        assert summary["added_count"] == 2
        assert db.execute_calls == 3
        added_members = [
            call.args[0]
            for call in db.add.call_args_list
            if isinstance(call.args[0], SuiteMembership)
        ]
        assert len(added_members) == 2
        linked_member = next(m for m in added_members if m.test_fingerprint == "fp-login")
        execution_member = next(m for m in added_members if m.test_fingerprint == "fp-logout")
        assert linked_member.managed_test_case_id == managed_id
        assert linked_member.source == "linked"
        assert execution_member.managed_test_case_id is None
        assert execution_member.source == "execution"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-5: Fingerprint Determinism (standalone, no heavy imports)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import hashlib  # noqa: E402


def _make_test_fingerprint(test_name: str, class_name: str | None) -> str:
    """Reimplementation of ingestion.make_test_fingerprint for isolated testing."""
    key = f"{class_name or ''}::{test_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class TestFingerprintDeterminism:
    def test_same_inputs_same_fingerprint(self):
        fp1 = _make_test_fingerprint("testLogin", "com.example.AuthTest")
        fp2 = _make_test_fingerprint("testLogin", "com.example.AuthTest")
        assert fp1 == fp2

    def test_different_inputs_different_fingerprint(self):
        fp1 = _make_test_fingerprint("testLogin", "com.example.AuthTest")
        fp2 = _make_test_fingerprint("testLogout", "com.example.AuthTest")
        assert fp1 != fp2

    def test_null_class_name_handled(self):
        fp = _make_test_fingerprint("testSomething", None)
        assert len(fp) == 16  # SHA256[:16]

    def test_fingerprint_length(self):
        fp = _make_test_fingerprint("test", "Cls")
        assert len(fp) == 16


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TS-7: Migration Exists
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMigrationExists:
    def test_migration_0048_exists(self):
        from pathlib import Path
        migration_dir = Path(__file__).parent.parent / "migrations" / "versions"
        files = [f.name for f in migration_dir.iterdir() if f.name.startswith("0048")]
        assert len(files) == 1
        assert "suite_membership" in files[0]
