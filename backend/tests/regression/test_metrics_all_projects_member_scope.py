"""STRAT-01 / D28: the dashboard summary and the trend chart over a non-admin's OWN projects.

Trap 1: ``get_dashboard_summary`` / ``get_trend_data`` used to take only
``project_id``, and ``None`` read every active project. ``/metrics/summary``,
``/metrics/trends`` and ``/analytics/trend-summary`` now aggregate for a
non-admin who names no project, so every query on those paths must carry the
caller's set (``allowed_project_ids``, ``_tenant_filter`` semantics): a set is
``IN`` the set, an empty set reads nothing, ``None`` is the admin read.

Trap 2: the summary is cached under ``dashboard_summary_v2:all:...`` for every
unpinned read. A member's entry must never be the admin's (every project), so
the member set reaches the key; the admin and pinned keys stay byte-identical.

These run the real services over a database that records every statement. The
same over real rows, with a foreign project seeded, is
``tests/integration/test_all_projects_member_scope_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import cache_service, metrics_service  # noqa: E402

pytestmark = pytest.mark.regression

A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
FOREIGN = uuid.UUID("cccccccc-0000-4000-8000-000000000003")
MEMBER = frozenset({A, B})


class _Rows:
    """Every result shape the services read, all zero (``total_runs`` aside)."""

    def __init__(self, total_runs: int) -> None:
        self.total_runs = total_runs

    def scalar(self):
        return 0

    def one(self):
        zero = dict.fromkeys(
            ("sum_passed", "sum_failed", "sum_broken", "sum_total", "avg_duration_ms",
             "passed", "failed", "broken", "total"),
            0,
        )
        return SimpleNamespace(total_runs=self.total_runs, **zero)

    def fetchall(self):
        return []

    def scalars(self):
        return SimpleNamespace(all=lambda: [])


class _RecordingDB:
    """Compiles each statement for Postgres and keeps its SQL and bound values."""

    def __init__(self, total_runs: int = 0) -> None:
        self.total_runs = total_runs
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        compiled = stmt.compile(dialect=postgresql.dialect())
        binds = dict(compiled.params)
        binds.update(params or {})
        self.statements.append((str(compiled), binds))
        return _Rows(self.total_runs)


def _bound_ids(binds: dict) -> set[str]:
    out: set[str] = set()
    for value in binds.values():
        for item in value if isinstance(value, (list, tuple, set, frozenset)) else [value]:
            out.add(str(item))
    return out


def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "cache_set", AsyncMock(return_value=None))


def _policy() -> metrics_service.ReadinessPolicyResolved:
    # A resolved policy, so the readiness branch counts open P0 defects too.
    return metrics_service.ReadinessPolicyResolved(
        rules={"pass_rate_bands": {}, "hard_caps": {}}, id="p", version=1, level="system", name="Default",
    )


# ── every query of the summary carries the member set ──────────────────────


async def test_every_summary_query_reads_only_the_members_projects(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(metrics_service, "resolve_readiness_policy", AsyncMock(return_value=_policy()))
    db = _RecordingDB(total_runs=1)

    await metrics_service.get_dashboard_summary(db, None, 7, allowed_project_ids=MEMBER)

    # Two periods, active defects, flaky, new failures in 24h, open P0s.
    assert len(db.statements) == 6, [sql[:80] for sql, _ in db.statements]
    for sql, binds in db.statements:
        ids = _bound_ids(binds)
        assert {str(A), str(B)} <= ids, f"a summary query ignored the member set:\n{sql}"
        assert str(FOREIGN) not in ids
        assert "is_active" in sql, f"the active-project clause was displaced:\n{sql}"


async def test_an_empty_set_reads_nothing(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(metrics_service, "resolve_readiness_policy", AsyncMock(return_value=_policy()))
    db = _RecordingDB(total_runs=1)

    await metrics_service.get_dashboard_summary(db, None, 7, allowed_project_ids=frozenset())

    assert len(db.statements) == 6
    for sql, _ in db.statements:
        assert "false" in sql.lower(), f"an empty set must fail closed:\n{sql}"


async def test_the_admin_read_is_unchanged(monkeypatch):
    """``None`` is the unrestricted admin read: no membership clause at all."""
    _no_cache(monkeypatch)
    monkeypatch.setattr(metrics_service, "resolve_readiness_policy", AsyncMock(return_value=_policy()))
    db = _RecordingDB(total_runs=1)

    await metrics_service.get_dashboard_summary(db, None, 7)

    assert len(db.statements) == 6
    for sql, binds in db.statements:
        assert "pid_" not in sql and str(A) not in _bound_ids(binds)
        assert "is_active" in sql


async def test_a_pinned_project_wins_over_a_set(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(metrics_service, "resolve_readiness_policy", AsyncMock(return_value=_policy()))
    db = _RecordingDB(total_runs=1)

    await metrics_service.get_dashboard_summary(db, str(A), 7, allowed_project_ids=MEMBER)

    for sql, binds in db.statements:
        assert str(B) not in _bound_ids(binds), f"the pin must not widen to the set:\n{sql}"


# ── the trend chart ─────────────────────────────────────────────────────────


async def test_the_trend_query_reads_only_the_members_projects():
    db = _RecordingDB()

    await metrics_service.get_trend_data(db, None, 7, allowed_project_ids=MEMBER)

    [(sql, binds)] = db.statements
    assert "tr.project_id IN (%(pid_0)s, %(pid_1)s)" in sql
    assert (binds["pid_0"], binds["pid_1"]) == (str(A), str(B))
    assert "tr.project_id IN (SELECT id FROM projects WHERE is_active)" in sql


async def test_the_trend_query_with_an_empty_set_reads_nothing_and_the_admin_read_is_unchanged():
    empty, admin = _RecordingDB(), _RecordingDB()

    await metrics_service.get_trend_data(empty, None, 7, allowed_project_ids=frozenset())
    await metrics_service.get_trend_data(admin, None, 7)

    assert "AND FALSE" in empty.statements[0][0]
    admin_sql = admin.statements[0][0]
    assert "pid_" not in admin_sql and "AND FALSE" not in admin_sql
    assert "tr.project_id IN (SELECT id FROM projects WHERE is_active)" in admin_sql


# ── the flaky count and the readiness P0 count ──────────────────────────────


async def test_the_flaky_count_reads_only_the_members_projects():
    member, empty = _RecordingDB(), _RecordingDB()

    await metrics_service._count_flaky_tests(member, None, allowed_project_ids=MEMBER)
    await metrics_service._count_flaky_tests(empty, None, allowed_project_ids=frozenset())

    sql, binds = member.statements[0]
    assert "tr.project_id IN (%(pid_0)s, %(pid_1)s)" in sql and "is_active" in sql
    assert (binds["pid_0"], binds["pid_1"]) == (str(A), str(B))
    assert "AND FALSE" in empty.statements[0][0]


def _defect(project: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=project, severity="CRITICAL")


@pytest.mark.parametrize("release", ["rel-1", ("rel-1", "rel-2")])
async def test_a_release_never_widens_the_member_set(monkeypatch, release):
    """``blocking_defects`` is pinned to a project; whatever it returns, a P0
    outside the member's projects never reaches the readiness hard cap."""
    from app.services import release_defect_service

    found = [_defect(A), _defect(FOREIGN)]
    monkeypatch.setattr(release_defect_service, "blocking_defects", AsyncMock(return_value=found))

    member = await metrics_service.count_open_critical_defects(
        _RecordingDB(), None, release, allowed_project_ids=MEMBER,
    )
    admin = await metrics_service.count_open_critical_defects(_RecordingDB(), None, release)

    assert member == 1
    assert admin == 2, "the admin (and pinned) read is unchanged"


# ── Trap 2: the cache key carries the member set ────────────────────────────


def _wire_cached_summary(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The real key builder over an in-memory store. Each project contributes
    10 executions, so the admin's answer (three projects) differs from a
    member's (theirs)."""
    store: dict[str, dict] = {}
    reads = {"n": 0}

    async def _get(namespace, project_id, *, epoch, **kw):
        return store.get(cache_service._build_cache_key(namespace, project_id, epoch=epoch, **kw))

    async def _set(namespace, value, project_id, ttl=0, *, epoch, **kw):
        store[cache_service._build_cache_key(namespace, project_id, epoch=epoch, **kw)] = value

    async def _period_stats(db, project_id, start, end, suite, release_id, *, allowed_project_ids=None):
        reads["n"] += 1
        projects = 3 if allowed_project_ids is None else len(allowed_project_ids)
        return {"total_runs": 0, "total_executions": 10 * projects, "pass_rate": 0.0, "avg_duration_ms": 0}

    monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=0))
    monkeypatch.setattr(cache_service, "cache_get", _get)
    monkeypatch.setattr(cache_service, "cache_set", _set)
    monkeypatch.setattr(metrics_service, "_period_stats", _period_stats)
    monkeypatch.setattr(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0))
    return {"store": store, "reads": reads}


async def test_a_member_never_reads_the_admins_cached_all_projects_summary(monkeypatch):
    wired = _wire_cached_summary(monkeypatch)
    db = _RecordingDB()

    async def _executions(allowed):
        out = await metrics_service.get_dashboard_summary(db, None, 7, allowed_project_ids=allowed)
        return out["total_executions_7d"]["value"]

    assert await _executions(None) == 30          # the admin warms "all"
    assert await _executions(MEMBER) == 20, "the member was served the admin's entry"
    reads = wired["reads"]["n"]
    assert await _executions(frozenset({B, A})) == 20  # the same set, any order: one entry
    assert wired["reads"]["n"] == reads, "the member's own entry was not reused"
    assert await _executions(frozenset({A})) == 10, "another member's entry was served"

    keys = set(wired["store"])
    assert "analytics:dashboard_summary_v2:all:e0:days=7:suite=" in keys, (
        "the admin key must stay byte-identical"
    )
    member_key = f"analytics:dashboard_summary_v2:all:e0:days=7:members={metrics_service.member_set_key(MEMBER)}:suite="
    assert member_key in keys


def test_the_member_segment_is_stable_and_distinct():
    key = metrics_service.member_set_key(MEMBER)
    assert len(key) == 16 and int(key, 16) >= 0
    assert key == metrics_service.member_set_key(frozenset({B, A}))
    assert key != metrics_service.member_set_key(frozenset({A, FOREIGN}))


async def test_a_pinned_read_keeps_its_key(monkeypatch):
    wired = _wire_cached_summary(monkeypatch)

    await metrics_service.get_dashboard_summary(_RecordingDB(), str(A), 7, allowed_project_ids=MEMBER)

    assert list(wired["store"]) == [f"analytics:dashboard_summary_v2:{A}:e0:days=7:suite="]


# ── the raw-SQL fragment is ``_tenant_filter``'s ────────────────────────────


@pytest.mark.parametrize("allowed", [MEMBER, frozenset()])
def test_the_member_fragment_matches_the_tenant_filter(allowed):
    from app.services.analytics_service import _tenant_filter

    mine: dict = {}
    theirs: dict = {}
    fragment = metrics_service._member_sql(mine, allowed)
    tenant = _tenant_filter(theirs, project_id=None, allowed_project_ids=sorted(allowed, key=str))
    assert tenant.startswith(fragment.strip())
    assert mine == theirs
    assert metrics_service._member_sql({}, None) == ""
