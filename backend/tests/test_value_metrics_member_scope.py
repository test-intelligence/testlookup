"""W35 STRAT-01 -- value metrics aggregate over the caller's OWN projects.

Route half: each test runs the REAL scope dependency
(``analytics_scope(_VALUE_SCOPE)``) with only the membership lookup patched,
then ``GET /value-metrics``'s handler with its result; ``get_value_metrics``
and ``build_meta`` are fakes recording what they got.

Service half: every statement ``get_value_metrics`` runs for a set carries an
``IN`` over exactly that set (an empty set reads nothing; ``None`` -- the admin
read -- only the live-project clause), each hours-model leg query does the
same, and the unpinned hours model neither queries nor touches the cache.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.routers import value_metrics as router
from app.services import value_metrics_service as vms
from app.services.analytics_scope import analytics_scope

pytestmark = pytest.mark.asyncio

A, B, FOREIGN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PAYLOAD = {"period_days": 30, "defects_auto_grouped": 3}


class _Request:
    def __init__(self, project_id):
        values = [project_id] if project_id else []
        self.query_params = SimpleNamespace(getlist=lambda _key: values)


async def _scope(project_id, accessible):
    dependency = analytics_scope(router._VALUE_SCOPE)
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)), \
         patch("app.core.deps.resolve_release_query_scopes", AsyncMock(return_value=[])):
        return await dependency(
            _Request(project_id), project_id=str(project_id) if project_id else None,
            release_id=None, suite_name=None, days=30, db=AsyncMock(), current_user=object(),
        )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fake = AsyncMock(return_value=dict(PAYLOAD))
    monkeypatch.setattr(router, "get_value_metrics", fake)
    monkeypatch.setattr(router, "build_meta", AsyncMock(return_value={"schema_version": 3}))
    return fake


async def _get(scope) -> dict:
    return await router.get_metrics(months=6, scope=scope, db=MagicMock())


# ── the route ───────────────────────────────────────────────────────────────


async def test_a_member_of_two_projects_naming_none_reads_both(service):
    scope = await _scope(None, {A, B})

    body = await _get(scope)

    assert not scope.denied
    service.assert_awaited_once()
    kwargs = service.await_args.kwargs
    assert kwargs["project_id"] is None
    # The member's set -- never None, which is the unrestricted admin read.
    assert kwargs["allowed_project_ids"] == frozenset({A, B})
    assert FOREIGN not in kwargs["allowed_project_ids"]
    assert body == {**PAYLOAD, "meta": {"schema_version": 3}}


async def test_a_foreign_project_is_the_empty_payload(service):
    body = await _get(await _scope(FOREIGN, {A, B}))

    assert body == {"meta": {"schema_version": 3}}
    service.assert_not_awaited()


async def test_a_caller_with_no_project_at_all_is_the_empty_payload(service):
    body = await _get(await _scope(None, set()))

    assert body == {"meta": {"schema_version": 3}}
    service.assert_not_awaited()


async def test_the_admin_naming_none_reads_every_project(service):
    await _get(await _scope(None, None))

    kwargs = service.await_args.kwargs
    assert kwargs["project_id"] is None and kwargs["allowed_project_ids"] is None


async def test_a_member_naming_their_own_project_is_pinned(service):
    await _get(await _scope(A, {A, B}))

    kwargs = service.await_args.kwargs
    assert kwargs["project_id"] == A and kwargs["allowed_project_ids"] is None


# ── the service ─────────────────────────────────────────────────────────────


def _recording_db() -> tuple[MagicMock, list]:
    """A session whose ``execute`` records each statement and answers zero."""
    seen: list = []

    async def execute(statement, params=None):
        seen.append((statement, params))
        result = MagicMock()
        result.scalar.return_value = 0
        result.all.return_value = []
        result.fetchall.return_value = []
        result.first.return_value = (None, None)
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    return db, seen


def _compiled(statement):
    return statement.compile(dialect=postgresql.dialect())


def _in_lists(statement) -> list:
    """The values of every expanding ``IN`` the statement binds."""
    return [sorted(map(str, v)) for v in _compiled(statement).params.values() if isinstance(v, (list, tuple))]


async def test_every_counter_reads_only_the_members_set():
    db, seen = _recording_db()

    body = await vms.get_value_metrics(db, None, days=30, allowed_project_ids=frozenset({A, B}))

    assert len(seen) == 11, "one statement per legacy counter"
    for statement, _params in seen:
        sql = str(_compiled(statement))
        assert _in_lists(statement) == [sorted([str(A), str(B)])], sql
        assert "is_active" in sql, sql
    assert body["project_id"] is None


async def test_an_empty_set_reads_nothing_and_the_admin_read_only_live_projects():
    # An empty set: the conjunction collapses to WHERE false (fails closed).
    # None (the admin): the live-project clause and nothing narrower.
    for allowed, expected in ((frozenset(), "where false"), (None, "is_active")):
        db, seen = _recording_db()
        await vms.get_value_metrics(db, None, days=30, allowed_project_ids=allowed)
        assert len(seen) == 11
        for statement, _params in seen:
            sql = str(_compiled(statement)).lower()
            assert _in_lists(statement) == [] and expected in sql, sql


async def test_the_unpinned_hours_model_runs_no_query_and_never_touches_the_cache(monkeypatch):
    """The cache below only ever holds a pinned project's model, so a member's
    aggregate cannot share an entry with the admin's every-project read."""
    from app.services import cache_service

    touched = {name: AsyncMock(return_value=None) for name in ("cache_get", "cache_set", "get_analytics_epoch")}
    for name, fake in touched.items():
        monkeypatch.setattr(cache_service, name, fake)
    legs, span = AsyncMock(return_value={}), AsyncMock(return_value=(None, None))
    monkeypatch.setattr(vms, "_gather_leg_counts", legs)
    monkeypatch.setattr(vms, "_run_span", span)
    db, seen = _recording_db()

    body = await vms.get_value_metrics(db, None, days=30, allowed_project_ids=frozenset({A, B}))

    assert body["available"] is False
    assert body["insufficient_data_reason"] == "select a project to compute the hours-saved model"
    assert body["assumptions_source"] == "default"
    for fake in (*touched.values(), legs, span):
        fake.assert_not_awaited()


@pytest.mark.parametrize("leg", [
    "_cluster_counts", "_triage_counts", "_quarantine_suppressed_counts", "_runs_unblocked_counts",
])
async def test_each_leg_query_reads_only_the_set(leg):
    db, seen = _recording_db()
    since = datetime.now(timezone.utc) - timedelta(days=30)

    await getattr(vms, leg)(db, None, since, False, allowed_project_ids=frozenset({A, B}))

    ((statement, params),) = seen
    sql = str(statement)
    assert "tr.project_id IN (:pid_0, :pid_1)" in sql and "is_active" in sql, sql
    assert {params["pid_0"], params["pid_1"]} == {str(A), str(B)}
    assert "project_id" not in params  # no pin, so no unrestricted fallback either


async def test_the_run_span_reads_only_the_set():
    db, seen = _recording_db()

    await vms._run_span(db, None, allowed_project_ids=frozenset({A, B}))

    ((statement, _params),) = seen
    assert _in_lists(statement) == [sorted([str(A), str(B)])]
