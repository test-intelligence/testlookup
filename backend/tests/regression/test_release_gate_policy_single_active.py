"""GATE-03 at the router, without a database.

The database now refuses a second active policy per scope (migration 0210),
so the routes must turn that refusal into a sentence, and must not provoke it
themselves:

* a commit refused by one of the ``uq_rgp_`` scope indexes is a 409 with the
  race text, after a rollback; any OTHER IntegrityError is re-raised (a real
  bug must stay loud);
* ``create_policy`` takes the scope lock BEFORE it reads ``max(version)``;
* ``publish_policy`` reads the scope, locks it, re-reads it, and FLUSHES the
  demotion before the promotion (SQLAlchemy would otherwise order the two
  UPDATEs by primary key, i.e. randomly, and the index would refuse the
  promote even with no race).

The concurrency itself is proven on PostgreSQL in
``tests/integration/test_release_gate_policy_single_active_postgres.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.postgres import ReleaseGatePolicy
from app.models.schemas import ReleaseGatePolicyCreate
from app.routers import release_gate_policies as router


def _integrity(constraint: str) -> IntegrityError:
    return IntegrityError(
        "UPDATE release_gate_policies ...", {},
        Exception(f'duplicate key value violates unique constraint "{constraint}"'),
    )


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


class _Db:
    """Answers by statement text and records the order of everything."""

    def __init__(self, *, policy=None, others=(), commit_error=None):
        self.policy = policy
        self.others = list(others)
        self.commit_error = commit_error
        self.log: list[str] = []
        self.added: list = []
        self.rolled_back = 0

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split()) if hasattr(stmt, "compile") else str(stmt)
        if "pg_advisory_xact_lock" in sql:
            self.log.append(f"lock:{(params or {}).get('key')}")
            return _Result()
        if "max(release_gate_policies.version)" in sql:
            self.log.append("max")
            return _Result(scalar=3)
        if sql.startswith("SELECT release_gate_policies.id FROM"):
            self.log.append("active-ids")
            return _Result([o.id for o in self.others])
        if "release_gate_policies.id IN" in sql:
            self.log.append("load-others")
            return _Result(self.others)
        if "FROM release_gate_policies WHERE release_gate_policies.id =" in sql:
            self.log.append("load-policy")
            return _Result([self.policy])
        if "FROM projects" in sql:
            return _Result([])
        raise AssertionError(f"unexpected statement: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        promoted = bool(self.policy and self.policy.is_active)
        demoted = all(o.is_active is False for o in self.others)
        self.log.append(f"flush(demoted={demoted}, promoted={promoted})")

    async def refresh(self, obj):
        self.log.append("refresh")

    async def commit(self):
        self.log.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rolled_back += 1
        self.log.append("rollback")


ADMIN = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="admin", is_active=True)


def _policy(project_id, *, draft=True, active=False):
    return ReleaseGatePolicy(
        id=uuid.uuid4(), project_id=project_id, version=1, name="p", rules={},
        is_draft=draft, is_active=active, created_by=ADMIN.id,
    )


@pytest.fixture(autouse=True)
def _no_cache_bump(monkeypatch):
    import app.services.cache_service as cache_service

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _noop)


def test_the_lock_key_names_the_scope():
    pid = uuid.uuid4()
    assert router._scope_lock_key(pid) == f"release_gate_policy:{pid}"
    assert router._scope_lock_key(None) == "release_gate_policy:system"


def test_create_locks_the_scope_before_reading_the_version():
    pid = uuid.uuid4()
    db = _Db()
    asyncio.run(router.create_policy(ReleaseGatePolicyCreate(project_id=pid, name="draft"), current_user=ADMIN, db=db))
    assert db.log.index(f"lock:release_gate_policy:{pid}") < db.log.index("max")
    assert db.added and db.added[0].version == 4


@pytest.mark.parametrize("constraint", ["uq_rgp_project_version", "uq_rgp_system_version"])
def test_a_create_version_collision_is_a_409(constraint):
    db = _Db(commit_error=_integrity(constraint))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.create_policy(ReleaseGatePolicyCreate(project_id=None, name="draft"), current_user=ADMIN, db=db))
    assert exc.value.status_code == 409
    assert exc.value.detail == router.CREATE_RACE_DETAIL
    assert db.rolled_back == 1


def test_an_unrelated_integrity_error_on_create_is_re_raised():
    db = _Db(commit_error=_integrity("release_gate_policies_created_by_fkey"))
    with pytest.raises(IntegrityError):
        asyncio.run(router.create_policy(ReleaseGatePolicyCreate(project_id=None, name="draft"), current_user=ADMIN, db=db))


@pytest.mark.parametrize("scope", ["project", "system"])
def test_publish_demotes_flushes_then_promotes(scope):
    pid = uuid.uuid4() if scope == "project" else None
    old = _policy(pid, draft=False, active=True)
    draft = _policy(pid)
    db = _Db(policy=draft, others=[old])
    asyncio.run(router.publish_policy(draft.id, current_user=ADMIN, db=db))
    key = f"lock:release_gate_policy:{pid if pid else 'system'}"
    assert db.log[:4] == ["load-policy", "active-ids", key, "refresh"]
    assert db.log[4:7] == ["active-ids", "load-others", "flush(demoted=True, promoted=False)"]
    assert db.log.index("flush(demoted=True, promoted=False)") < db.log.index("commit")
    assert old.is_active is False and draft.is_active is True and draft.is_draft is False


def test_a_scope_change_under_the_lock_is_the_race_409():
    pid = uuid.uuid4()
    draft = _policy(pid)
    db = _Db(policy=draft)
    calls = {"n": 0}
    real = db.execute

    async def _execute(stmt, params=None):
        result = await real(stmt, params)
        if db.log and db.log[-1] == "active-ids":
            calls["n"] += 1
            if calls["n"] == 2:
                # Another publish committed between the first read and the lock.
                return _Result([uuid.uuid4()])
        return result

    db.execute = _execute
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.publish_policy(draft.id, current_user=ADMIN, db=db))
    assert exc.value.status_code == 409
    assert exc.value.detail == router.PUBLISH_RACE_DETAIL
    assert "commit" not in db.log


@pytest.mark.parametrize("constraint", ["uq_rgp_one_active_project", "uq_rgp_one_active_system"])
def test_a_publish_refused_by_the_one_active_index_is_a_409(constraint):
    draft = _policy(uuid.uuid4())
    db = _Db(policy=draft, commit_error=_integrity(constraint))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.publish_policy(draft.id, current_user=ADMIN, db=db))
    assert exc.value.status_code == 409
    assert exc.value.detail == router.PUBLISH_RACE_DETAIL
    assert db.rolled_back == 1


def test_an_unrelated_integrity_error_on_publish_is_re_raised():
    draft = _policy(uuid.uuid4())
    db = _Db(policy=draft, commit_error=_integrity("release_gate_policies_activated_by_fkey"))
    with pytest.raises(IntegrityError):
        asyncio.run(router.publish_policy(draft.id, current_user=ADMIN, db=db))


def test_an_already_published_policy_is_still_refused_before_the_lock():
    published = _policy(uuid.uuid4(), draft=False, active=True)
    db = _Db(policy=published)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.publish_policy(published.id, current_user=ADMIN, db=db))
    assert (exc.value.status_code, exc.value.detail) == (409, "Policy is already published")
    assert not any(entry.startswith("lock:") for entry in db.log)


def test_the_race_texts_are_the_contracts():
    assert router.PUBLISH_RACE_DETAIL == (
        "Another policy was published for this scope at the same moment. Reload the "
        "policies and publish again if you still want this version."
    )
    assert router.CREATE_RACE_DETAIL == (
        "Another policy version was created for this scope at the same moment. Try again."
    )
