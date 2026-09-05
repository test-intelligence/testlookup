"""End-to-end regression for MCP caller identity at the backend boundary.

The existing MCP transport suite proves that each SSE session forwards its own
bearer token.  This test closes the other half of that contract: the forwarded
token is decoded by the real FastAPI authentication dependencies, tenant scope
is enforced by the real quarantine route, and the successful actor reaches the
real audit constructor.

Postgres is replaced at the dependency/factory boundary, as in the other API
integration tests.  Route dispatch, JWT decoding, role checks, project-scope
resolution, quarantine orchestration, MCP tool serialization, and HTTP error
handling all remain production code.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("jose")
pytest.importorskip("sqlalchemy")

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = REPO_ROOT / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import client as mcp_client  # noqa: E402
from tools import quarantine as quarantine_tools  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.postgres import (  # noqa: E402
    FlakyQuarantineRequest,
    ProjectMember,
    SettingsAuditLog,
    User,
    UserRole,
)
from app.services import flaky_quarantine_service  # noqa: E402


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows


class _RequestSession:
    """Answer the two real auth queries from an in-memory identity directory."""

    def __init__(self, users, memberships):
        self.users = users
        self.memberships = memberships

    async def execute(self, statement, _params=None):
        entity = statement.column_descriptions[0].get("entity")
        values = list(statement.compile().params.values())
        subject = next((value for value in values if isinstance(value, uuid.UUID)), None)
        if entity is User:
            return _Result(scalar=self.users.get(subject))
        if entity is ProjectMember:
            return _Result(rows=[(project_id,) for project_id in self.memberships.get(subject, set())])
        raise AssertionError(f"Unexpected request-session query: {statement}")


class _MutationSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _statement, _params=None):
        # No live proposal exists for the test fingerprint.
        return _Result(scalar=None)

    def add(self, row):
        now = datetime.now(timezone.utc)
        if isinstance(row, FlakyQuarantineRequest):
            row.id = row.id or uuid.uuid4()
            row.created_at = row.created_at or now
            row.updated_at = row.updated_at or now
            # SQLAlchemy applies these defaults during a real INSERT.
            row.consecutive_passes = row.consecutive_passes or 0
            row.ready_to_promote = bool(row.ready_to_promote)
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None

    async def rollback(self):
        return None


class _AuditSession(_MutationSession):
    pass


class _SessionFactory:
    def __init__(self, mutation, audit):
        self._sessions = iter((mutation, audit))

    def __call__(self):
        return next(self._sessions)


class _NoMembershipCache:
    async def get(self, _key):
        return None

    async def set(self, _key, _value, **_kwargs):
        return True


class _ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(function):
            self.tools[function.__name__] = function
            return function

        return decorate


def _user(name: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=name,
        email=f"{name}@example.com",
        role=UserRole.QA_LEAD.value,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_mcp_token_is_enforced_by_backend_tenant_scope_and_attributed_in_audit(monkeypatch):
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    alice, bob = _user("alice"), _user("bob")
    users = {alice.id: alice, bob.id: bob}
    memberships = {alice.id: {project_a}, bob.id: {project_b}}
    request_session = _RequestSession(users, memberships)
    mutation_session = _MutationSession()
    audit_session = _AuditSession()
    session_factory = _SessionFactory(mutation_session, audit_session)

    async def request_db():
        yield request_session

    async def not_revoked(*_args, **_kwargs):
        return False

    async def feature_enabled(*_args, **_kwargs):
        return True

    app.dependency_overrides[get_db] = request_db
    monkeypatch.setattr("app.core.token_revocation.is_jti_revoked", not_revoked)
    monkeypatch.setattr("app.core.token_revocation.is_token_before_cutoff", not_revoked)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _NoMembershipCache())
    monkeypatch.setattr(flaky_quarantine_service, "_feature_enabled", feature_enabled)
    monkeypatch.setattr(flaky_quarantine_service, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", session_factory)

    registry = _ToolRegistry()
    quarantine_tools.register(registry)
    propose = registry.tools["propose_quarantine"]
    presented_token = [create_access_token(alice.id)]
    monkeypatch.setattr(mcp_client, "_request_access_token", lambda: presented_token[0])

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as backend:
            monkeypatch.setattr(mcp_client, "_client", backend)

            denied = await propose(
                str(project_b), "cross-project-fingerprint", "must stay in Bob's project"
            )
            assert denied["ok"] is False
            assert denied["status_code"] == 403
            assert denied["detail"] == "You do not have access to this project"
            assert mutation_session.added == []
            assert audit_session.added == []

            presented_token[0] = create_access_token(bob.id)
            allowed = await propose(
                str(project_b), "owned-project-fingerprint", "Bob reviewed the evidence"
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        mcp_client._client = None

    assert allowed["ok"] is True
    assert allowed["status"] == "PROPOSED"
    assert len(mutation_session.added) == 1
    assert mutation_session.added[0].project_id == project_b
    assert mutation_session.commits == 1

    assert len(audit_session.added) == 1
    audit = audit_session.added[0]
    assert isinstance(audit, SettingsAuditLog)
    assert audit.action == "create"
    assert audit.actor_id == bob.id
    assert audit.actor_name == bob.username
    assert str(project_b) in audit.setting_key
    assert audit_session.commits == 1
