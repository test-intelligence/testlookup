"""D-141: a release-gate policy DRAFT that was never published can be deleted.

Published versions stay (past verdicts and the version history name them, so
they are deactivated instead), and so does a draft a stored decision points
at. The route is ``DELETE /api/v1/release-gate-policies/{policy_id}``: ADMIN,
the same project-bound key confinement as publish / deactivate, and one
``policy.updated`` activity row (``changed: draft deleted``) committed with
the delete.

The handler is driven directly against a fake session that answers by
statement text (the pattern of ``test_release_gate_policy_single_active.py``);
the role floor goes through the real app's dependency tree.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm.exc import StaleDataError

from app.core import deps
from app.models.postgres import ReleaseGatePolicy, UserRole
from app.routers import release_gate_policies as router

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Db:
    """Answers by statement text and records the order of everything."""

    def __init__(self, *, policy, decision_refs=(), gate_refs=()):
        self.policy = policy
        self.decision_refs = list(decision_refs)
        self.gate_refs = list(gate_refs)
        self.log: list[str] = []
        self.deleted: list = []

    async def execute(self, stmt, params=None):
        sql = (
            " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())
            if hasattr(stmt, "compile") else str(stmt)
        )
        if "pg_advisory_xact_lock" in sql:
            self.log.append(f"lock:{(params or {}).get('key')}")
            return _Result()
        if "FROM release_gate_policies WHERE release_gate_policies.id =" in sql:
            self.log.append("load-policy")
            return _Result([self.policy] if self.policy is not None else [])
        if "FROM release_decisions" in sql:
            self.log.append("decision-refs")
            return _Result(self.decision_refs)
        if "FROM release_gate_decisions" in sql:
            self.log.append("gate-refs")
            return _Result(self.gate_refs)
        raise AssertionError(f"unexpected statement: {sql}")

    async def delete(self, obj):
        self.log.append("delete")
        self.deleted.append(obj)

    async def commit(self):
        self.log.append("commit")

    async def rollback(self):
        self.log.append("rollback")


class _RaceDb(_Db):
    """A publish / PATCH session whose draft another admin deletes mid-request.

    ``deleted_after_lock``: the DELETE committed while this request waited for
    the scope lock, so the re-read (``refresh``) finds no row -- SQLAlchemy
    raises ``InvalidRequestError`` ("Could not refresh instance").
    ``deleted_before_commit``: the DELETE committed between PATCH's read and
    its commit, so the UPDATE matches 0 rows -- ``StaleDataError``. (Both
    exception types checked against a real SQLite session while writing this.)
    """

    def __init__(self, *, policy, deleted_after_lock=False, deleted_before_commit=False):
        super().__init__(policy=policy)
        self.deleted_after_lock = deleted_after_lock
        self.deleted_before_commit = deleted_before_commit

    async def execute(self, stmt, params=None):
        sql = (
            " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())
            if hasattr(stmt, "compile") else str(stmt)
        )
        if sql.startswith("SELECT release_gate_policies.id FROM"):
            self.log.append("active-ids")
            return _Result()
        if "FROM projects" in sql:  # a system-default publish bumps every project
            return _Result()
        return await super().execute(stmt, params)

    def add(self, obj):
        self.log.append("add")

    async def flush(self):
        self.log.append("flush")

    async def refresh(self, obj):
        self.log.append("refresh")
        if self.deleted_after_lock:
            raise InvalidRequestError(f"Could not refresh instance '{obj!r}'")

    async def commit(self):
        if self.deleted_before_commit:
            self.log.append("commit-refused")
            raise StaleDataError(
                "UPDATE statement on table 'release_gate_policies' expected to update 1 row(s); 0 were matched."
            )
        self.log.append("commit")


ADMIN = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="admin", is_active=True)


def _policy(project_id, *, draft=True, active=False, activated_at=None):
    return ReleaseGatePolicy(
        id=uuid.uuid4(), project_id=project_id, version=3, name="Checkout gate", rules={},
        is_draft=draft, is_active=active, activated_at=activated_at, created_by=ADMIN.id,
    )


@pytest.fixture
def events(monkeypatch):
    """Every activity row the route stages, instead of the ledger write."""
    recorded: list[dict] = []

    async def _record(db, **kwargs):
        db.log.append("activity")
        recorded.append(kwargs)

    monkeypatch.setattr(router, "record_activity", _record)
    return recorded


@pytest.fixture(autouse=True)
def _admin_scope():
    # An ADMIN resolves to every project: no membership read.
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)):
        yield


def _delete(policy_id, db, user=ADMIN):
    return asyncio.run(router.delete_policy(policy_id, current_user=user, db=db))


@pytest.mark.parametrize("scope", ["project", "system"])
def test_a_never_published_draft_is_deleted_with_one_activity_row(scope, events):
    pid = uuid.uuid4() if scope == "project" else None
    draft = _policy(pid)
    db = _Db(policy=draft)

    response = _delete(draft.id, db)

    assert response.status_code == 204
    assert db.deleted == [draft]
    key = f"lock:release_gate_policy:{pid if pid else 'system'}"
    # Locked, then re-read under the lock, before anything is decided.
    assert db.log.index(key) < db.log.index("load-policy", 1)
    assert db.log.index("delete") < db.log.index("commit")
    if pid is None:
        # The system default has no project feed (as for publish / deactivate).
        assert events == []
    else:
        assert db.log.index("activity") < db.log.index("delete")
        assert len(events) == 1
        assert events[0]["event_type"] == "policy.updated"
        assert events[0]["context"] == {"changed": "draft deleted"}
        assert events[0]["entity_id"] == draft.id
        assert events[0]["entity_label"] == "Checkout gate v3"


@pytest.mark.parametrize(
    "state",
    [
        {"draft": False, "active": True},
        {"draft": False, "active": False},
        # Defensive: a row that was activated once is not a never-published draft.
        {"draft": True, "active": False, "activated_at": datetime(2026, 11, 1, tzinfo=timezone.utc)},
    ],
    ids=["active", "past-version", "activated-once"],
)
def test_a_version_that_was_ever_published_is_refused_with_409(state, events):
    policy = _policy(uuid.uuid4(), **state)
    db = _Db(policy=policy)

    with pytest.raises(HTTPException) as exc:
        _delete(policy.id, db)

    assert exc.value.status_code == 409
    assert exc.value.detail == router.DELETE_PUBLISHED_DETAIL
    assert db.deleted == [] and "commit" not in db.log and events == []


@pytest.mark.parametrize("table", ["decision", "gate"])
def test_a_draft_a_stored_decision_names_is_refused_with_409(table, events):
    draft = _policy(uuid.uuid4())
    refs = {"decision_refs": [uuid.uuid4()]} if table == "decision" else {"gate_refs": [uuid.uuid4()]}
    db = _Db(policy=draft, **refs)

    with pytest.raises(HTTPException) as exc:
        _delete(draft.id, db)

    assert exc.value.status_code == 409
    assert exc.value.detail == router.DELETE_REFERENCED_DETAIL
    assert db.deleted == [] and "commit" not in db.log and events == []


def test_a_draft_published_while_waiting_for_the_lock_is_refused(events):
    draft = _policy(uuid.uuid4())
    db = _Db(policy=draft)
    real = db.execute

    async def _execute(stmt, params=None):
        result = await real(stmt, params)
        if db.log[-1].startswith("lock:"):
            # Another admin's publish committed before this request got the lock.
            draft.is_draft, draft.is_active = False, True
        return result

    db.execute = _execute
    with pytest.raises(HTTPException) as exc:
        _delete(draft.id, db)
    assert exc.value.status_code == 409
    assert db.deleted == []


def test_an_unknown_policy_is_404(events):
    db = _Db(policy=None)
    with pytest.raises(HTTPException) as exc:
        _delete(uuid.uuid4(), db)
    assert exc.value.status_code == 404


def _bound_key(project_id: uuid.UUID) -> SimpleNamespace:
    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="ci-key", is_active=True)
    setattr(user, deps._API_KEY_BOUND_PROJECT_ATTR, project_id)
    return user


def test_a_key_bound_to_another_project_is_refused_403(events):
    draft = _policy(uuid.uuid4())
    db = _Db(policy=draft)
    with pytest.raises(HTTPException) as exc:
        _delete(draft.id, db, user=_bound_key(uuid.uuid4()))
    assert exc.value.status_code == 403
    assert db.log == ["load-policy"] and db.deleted == []


def test_a_bound_key_may_not_delete_a_system_default_draft(events):
    draft = _policy(None)
    db = _Db(policy=draft)
    with pytest.raises(HTTPException) as exc:
        _delete(draft.id, db, user=_bound_key(uuid.uuid4()))
    assert exc.value.status_code == 403
    assert exc.value.detail == router.SYSTEM_DEFAULT_REFUSED_DETAIL
    assert db.deleted == []


def test_a_key_bound_to_the_drafts_own_project_may_delete_it(events):
    pid = uuid.uuid4()
    draft = _policy(pid)
    db = _Db(policy=draft)
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={pid})):
        response = _delete(draft.id, db, user=_bound_key(pid))
    assert response.status_code == 204
    assert db.deleted == [draft]


# ── review F4: publish / PATCH racing the delete answer 404, not 500 ────────


@pytest.fixture
def _no_cache_bump(monkeypatch):
    import app.services.cache_service as cache_service

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _noop)


def test_a_publish_whose_draft_was_deleted_while_it_waited_for_the_lock_is_404(events, _no_cache_bump):
    pid = uuid.uuid4()
    draft = _policy(pid)
    db = _RaceDb(policy=draft, deleted_after_lock=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.publish_policy(draft.id, current_user=ADMIN, db=db))

    assert exc.value.status_code == 404
    assert exc.value.detail == router.DRAFT_DELETED_DETAIL == "This draft was deleted."
    # The lock came first, then the re-read that found the row gone; nothing written.
    assert db.log.index(f"lock:release_gate_policy:{pid}") < db.log.index("refresh")
    assert "commit" not in db.log and events == []


def test_an_edit_whose_draft_was_deleted_before_its_commit_is_404(events):
    from app.models.schemas import ReleaseGatePolicyUpdate

    draft = _policy(uuid.uuid4())
    db = _RaceDb(policy=draft, deleted_before_commit=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.update_policy(
            draft.id, ReleaseGatePolicyUpdate(name="Renamed"), current_user=ADMIN, db=db,
        ))

    assert exc.value.status_code == 404
    assert exc.value.detail == router.DRAFT_DELETED_DETAIL
    assert db.log[-2:] == ["commit-refused", "rollback"]


# ── review F5: the system default records no activity, for every write ──────


def test_system_default_writes_record_nothing_publish_and_delete_alike(events, _no_cache_bump):
    """Kept consistent on purpose: ``_record_policy_event`` skips a policy with
    no project (the project activity ledger has no feed for it), for create,
    edit, publish and deactivate alike; the delete follows the same rule and
    leaves the ``logger.info`` line. If system-scope policy writes ever get an
    audit home (``settings_audit_log``), all five move together."""
    publish_db = _RaceDb(policy=_policy(None))
    asyncio.run(router.publish_policy(publish_db.policy.id, current_user=ADMIN, db=publish_db))
    assert "commit" in publish_db.log

    delete_db = _Db(policy=_policy(None))
    _delete(delete_db.policy.id, delete_db)
    assert delete_db.deleted

    assert events == []


# ── the role floor, through the real app ────────────────────────────────────


class _HandlerReached(Exception):
    pass


class _Session:
    def __init__(self):
        self.touched = 0

    def _reached(self, *_a, **_kw):
        self.touched += 1
        raise _HandlerReached

    execute = scalar = scalars = get = commit = flush = delete = _reached


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD],
    ids=lambda r: r.value,
)
async def test_a_role_below_admin_is_refused_before_the_handler(role):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_user_or_api_key
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import User

    session = _Session()
    uid = uuid.uuid4()
    user = User(
        id=uid, username=f"floor-{role.value.lower()}", email=f"floor-{uid.hex[:8]}@example.test",
        role=role.value, is_active=True, must_change_password=False, hashed_password="!unusable",
    )

    async def _user():
        return user

    async def _db():
        yield session

    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://floor.test") as client:
            resp = await client.delete(f"/api/v1/release-gate-policies/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.pop(get_current_user_or_api_key, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least ADMIN role"
    assert session.touched == 0
