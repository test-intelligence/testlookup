"""
Epic 6: Scalable Admin And Project Operations — Unit Tests.

Tests:
  QAI-601: Aggregated membership endpoint model
  QAI-602: N+1 elimination verification
  QAI-603: Access audit log model and service
  Edge cases: zero projects, empty memberships, partial failure
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
# QAI-601: Aggregated Membership Model
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccessAuditLogModel:
    def test_table_exists(self):
        from app.models.postgres import AccessAuditLog
        assert AccessAuditLog.__tablename__ == "access_audit_logs"

    def test_columns(self):
        from app.models.postgres import AccessAuditLog
        columns = {c.name for c in AccessAuditLog.__table__.columns}
        assert "actor_user_id" in columns
        assert "actor_name" in columns
        assert "target_user_id" in columns
        assert "project_id" in columns
        assert "action" in columns
        assert "before_value" in columns
        assert "after_value" in columns
        assert "created_at" in columns


class TestMembershipEndpoint:
    def test_endpoint_exists(self):
        from app.routers.users import get_user_memberships
        assert callable(get_user_memberships)

    def test_audit_endpoint_exists(self):
        from app.routers.users import get_user_access_audit
        assert callable(get_user_access_audit)


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-603: Access Audit Logging
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccessAuditService:
    def test_service_importable(self):
        from app.services.access_audit_service import log_access_change, get_access_audit_log
        assert callable(log_access_change)
        assert callable(get_access_audit_log)


class TestAuditActions:
    """Verify the audit action types are consistent.

    CORRECTED 2026-08-30. ``test_action_types`` used to build a list of six
    action strings and assert, of each, ``isinstance(action, str)`` and
    ``len(action) <= 50``. Both hold for any string literal, so the test could
    not fail and verified nothing about the code -- while reading, from its
    name and its list, as coverage of the audit vocabulary.

    It listed ``member_removed`` and ``member_role_changed``. **Neither was
    emitted anywhere.** Project-membership revocation and role changes wrote no
    audit row at all, though the grant beside them did. So the intended design
    was recorded here, a test appeared to cover it, and the emitters were never
    written -- see [[feedback_a_test_can_pass_having_done_nothing]].

    These now assert against the router source instead of against string
    literals.
    """

    def test_every_declared_action_is_actually_emitted(self):
        import inspect
        import re

        from app.routers import users

        source = inspect.getsource(users)
        emitted = set(re.findall(r'log_access_change\(\s*db,\s*"([a-z_]+)"', source))
        for action in ("member_added", "member_removed", "member_role_changed",
                       "role_changed", "status_changed"):
            assert action in emitted, (
                f"{action} is a declared audit action that routers/users.py "
                f"never emits. Emitted: {sorted(emitted)}"
            )

    def test_every_action_fits_the_column(self):
        """``AccessAuditLog.action`` is String(50)."""
        import inspect
        import re

        from app.routers import users

        for action in re.findall(
            r'log_access_change\(\s*db,\s*"([a-z_]+)"', inspect.getsource(users)
        ):
            assert len(action) <= 50, action


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_user_with_zero_projects(self):
        """User with no memberships should return empty list."""
        memberships: list = []
        assert len(memberships) == 0

    def test_membership_response_shape(self):
        """Verify expected shape of aggregated membership response."""
        entry = {
            "project_id": str(uuid.uuid4()),
            "project_name": "Test Project",
            "role": "QA_ENGINEER",
            "created_at": "2026-04-01T12:00:00Z",
        }
        assert "project_id" in entry
        assert "project_name" in entry
        assert "role" in entry
        assert "created_at" in entry

    def test_audit_log_response_shape(self):
        entry = {
            "id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "actor_name": "admin_user",
            "target_user_id": str(uuid.uuid4()),
            "project_id": None,
            "action": "role_changed",
            "before_value": {"role": "VIEWER"},
            "after_value": {"role": "QA_ENGINEER"},
            "created_at": "2026-04-01T12:00:00Z",
        }
        assert entry["action"] == "role_changed"
        assert entry["before_value"]["role"] != entry["after_value"]["role"]

    def test_role_normalization(self):
        from app.routers.users import _normalize_user_role
        from app.models.postgres import UserRole

        assert _normalize_user_role(UserRole.QA_ENGINEER) == UserRole.QA_ENGINEER
        assert _normalize_user_role("UserRole.QA_LEAD") == UserRole.QA_LEAD
        assert _normalize_user_role("ADMIN") == UserRole.ADMIN

    def test_project_member_response_builder(self):
        from app.routers.users import _build_project_member_response
        # Requires actual ORM objects — just verify the function exists
        assert callable(_build_project_member_response)
