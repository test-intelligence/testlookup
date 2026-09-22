"""E3 / VIZ-302 unit tests: ``report_metrics`` on ``/metrics/summary``.

The numbers over real rows are ``tests/integration/test_report_metrics_postgres.py``.
Here: the route asks for the block only on ``include=report_metrics``, the
history probe is bounded and skipped when it decides nothing, the cache key
moves with its shape (an
entry written before the block existed is never served as the new shape),
unmeasured values are ``null`` with a reason and never 0, the comparability
decision, and that the block's scope is the scope of the existing fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.models.viz_contracts import validate_contract
from app.services import analytics_meta
from app.services import metrics_service
from app.services import report_metrics_service as rms

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
WINDOW = {"from": "2026-09-14", "to": "2026-09-21", "days": 7}
PREV_WINDOW = {"from": "2026-09-07", "to": "2026-09-14", "days": 7}
#: A stand-in block for the cache tests (they never look inside it).
_EMPTY_BLOCK = {"schema_version": 1, "stub": True}


def _counts(**over) -> dict:
    base = {"runs": 3, "total": 19, "passed": 14, "failed": 1, "broken": 1, "skipped": 1,
            "unknown": 2, "duration": 4000, "duration_runs": 2}
    return {**base, **over}


# ── a period: unmeasured is null with a reason, never 0 ─────────────────────


def test_a_measured_period():
    period = rms.period_figures(_counts(), WINDOW)
    assert period["runs"] == 3 and period["pass_rate"] == 87.5
    assert period["total_duration_ms"] == 4000 and period["avg_duration_ms"] == 2000
    assert period["reasons"] == {}


def test_no_runs_is_every_metric_null_with_a_reason():
    period = rms.period_figures(_counts(runs=0, total=0, passed=0, failed=0, broken=0,
                                        skipped=0, unknown=0, duration=None, duration_runs=0),
                                WINDOW)
    for key in rms.REPORT_METRIC_KEYS:
        assert period[key] is None, key
        assert period["reasons"][key].strip(), key


def test_nothing_evaluated_is_a_null_rate_not_zero_percent():
    period = rms.period_figures(_counts(passed=0, failed=0, broken=0, skipped=4, unknown=0,
                                        total=4), WINDOW)
    assert period["pass_rate"] is None and period["reasons"]["pass_rate"]
    assert period["skipped"] == 4


def test_no_duration_recorded_is_null_not_zero():
    period = rms.period_figures(_counts(duration=None, duration_runs=0), WINDOW)
    assert period["total_duration_ms"] is None and period["avg_duration_ms"] is None
    assert period["duration_runs"] == 0
    assert period["reasons"]["total_duration_ms"] and period["reasons"]["avg_duration_ms"]


# ── comparability ───────────────────────────────────────────────────────────


def _decide(**over):
    args = {"current_runs": 3, "previous_runs": 2, "history_before": True,
            "first_previous": NOW - timedelta(days=10), "suite_scoped": False,
            "pending_current": 0, "pending_previous": 0, "previous_window": PREV_WINDOW}
    return rms.comparability(**{**args, **over})


@pytest.mark.parametrize(
    "over, code",
    [
        ({}, None),
        ({"current_runs": 0}, "not_measured"),
        ({"previous_runs": 0}, "no_data"),
        ({"history_before": False}, "partial_window"),
        ({"suite_scoped": True, "pending_current": 1}, "different_basis"),
        ({"suite_scoped": True, "pending_previous": 2}, "different_basis"),
        # Both periods mix run totals in: the same basis.
        ({"suite_scoped": True, "pending_current": 1, "pending_previous": 1}, None),
        # Without a suite there are no rows to count: pending runs mean nothing.
        ({"pending_current": 1}, None),
    ],
)
def test_comparability(over, code):
    comparable, reason_code, reason = _decide(**over)
    assert comparable is (code is None)
    assert reason_code == code
    assert (reason is None) is (code is None)
    if reason is not None:
        assert reason.strip()


@pytest.mark.parametrize("over", [{}, {"previous_runs": 0}, {"current_runs": 0}])
def test_every_assembled_block_is_valid_c6(over):
    """What the service assembles passes the contract both sides validate."""
    decided = _decide(**over)
    current_runs = over.get("current_runs", 3)
    previous_runs = over.get("previous_runs", 2)
    previous = rms.period_figures(_counts(runs=previous_runs, duration=None, duration_runs=0), PREV_WINDOW)
    previous.update(dict(zip(("comparable", "reason_code", "reason"), decided)))
    block = {
        "schema_version": rms.REPORT_METRICS_SCHEMA_VERSION,
        "pass_rate_basis": rms.PASS_RATE_BASIS,
        "current": rms.period_figures(_counts(runs=current_runs), WINDOW),
        "previous": previous,
    }
    validate_contract("report_metrics", block)


def test_the_partial_window_reason_names_the_first_run():
    _c, _code, reason = _decide(history_before=False, first_previous=datetime(2026, 9, 8, 18, tzinfo=timezone.utc))
    assert "2026-09-08" in reason
    # ...and states the bound it looked back over, not "the first run ever".
    assert f"{rms.HISTORY_LOOKBACK_DAYS} days" in reason


# ── the history probe: bounded, and only when it decides something ─────────


def test_the_history_probe_is_bounded_and_reads_newest_first():
    """Fix round C (m1): the old probe was ``created_at < start LIMIT 1`` with
    no floor and no order -- under a suite filter with no earlier match it
    read the scope's whole history, one suite EXISTS per run. The probe now
    has a floor ``HISTORY_LOOKBACK_DAYS`` before the previous window, a
    ceiling at it, and reads newest-first so the first match stops it."""
    previous_start = NOW - timedelta(days=14)
    conditions = rms.scope_conditions(str(uuid.uuid4()), ("payments",), None)
    stmt = rms.history_before_statement(conditions, previous_start)
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    floor = (previous_start - timedelta(days=rms.HISTORY_LOOKBACK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    ceiling = previous_start.strftime("%Y-%m-%d %H:%M:%S")
    assert f"test_runs.created_at >= '{floor}" in sql, sql
    assert f"test_runs.created_at < '{ceiling}" in sql, sql
    assert "ORDER BY test_runs.created_at DESC" in sql and sql.rstrip().endswith("LIMIT 1"), sql
    # Still the scope's own filter, predicate for predicate.
    for condition in conditions:
        from sqlalchemy import select

        from app.models.postgres import TestRun

        piece = _where(str(select(TestRun.id).where(condition).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        assert piece in sql, piece


class _Row:
    """A result row: named counts, 0 for anything not given, ``first_p`` None."""

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        if name == "first_p":
            return self._values.get(name)
        return self._values.get(name, 0)


class _Probe:
    """Answers the block's statements with ``row``; records every statement."""

    def __init__(self, row: _Row, history: bool = True):
        self.row, self.history, self.sql = row, history, []

    async def execute(self, stmt, *a, **k):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        result = MagicMock()
        result.one.return_value = self.row
        result.first.return_value = (uuid.uuid4(),) if self.history else None
        return result


def _probes(db: _Probe) -> list[str]:
    return [sql for sql in db.sql if "ORDER BY test_runs.created_at DESC" in sql]


@pytest.mark.asyncio
@pytest.mark.parametrize("suite", [None, ("payments",)])
@pytest.mark.parametrize("current, previous, probed", [
    (3, 2, True),    # both periods measured: history decides comparable
    (0, 2, False),   # not_measured wins before history is read
    (3, 0, False),   # no_data wins before history is read
    (0, 0, False),
])
async def test_the_history_probe_runs_only_when_it_decides_something(suite, current, previous, probed):
    db =_Probe(_Row(all_runs_c=current, all_runs_p=previous))
    block = await rms.build_report_metrics(db, str(uuid.uuid4()), 7, suite, None, now=NOW)
    assert len(_probes(db)) == (1 if probed else 0), db.sql
    # The unbounded probe is gone in every case.
    assert not [s for s in db.sql if "LIMIT" in s and "ORDER BY" not in s], db.sql
    validate_contract("report_metrics", block)


@pytest.mark.asyncio
async def test_no_earlier_run_in_the_lookback_is_a_partial_window():
    db = _Probe(_Row(all_runs_c=3, all_runs_p=2), history=False)
    block = await rms.build_report_metrics(db, str(uuid.uuid4()), 7, None, None, now=NOW)
    assert block["previous"]["reason_code"] == "partial_window"
    db = _Probe(_Row(all_runs_c=3, all_runs_p=2), history=True)
    block = await rms.build_report_metrics(db, str(uuid.uuid4()), 7, None, None, now=NOW)
    assert block["previous"]["comparable"] is True


# ── the block's scope is the existing fields' scope ─────────────────────────


class _Capture:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        result = MagicMock()
        row = MagicMock()
        for name in ("total_runs", "avg_duration_ms", "passed", "failed", "broken", "total",
                     "sum_passed", "sum_failed", "sum_broken", "sum_total"):
            setattr(row, name, 0)
        result.one.return_value = row
        return result


def _where(sql: str) -> str:
    """The statement's own WHERE (SQLAlchemy puts it on a new line; a
    ``FILTER (WHERE`` in the select list is inline)."""
    return sql.split("\nWHERE ", 1)[1]


@pytest.mark.parametrize("suite", [None, ("payments", "cart")])
@pytest.mark.parametrize("release", [None, "33333333-3333-4333-8333-333333333333",
                                     ("33333333-3333-4333-8333-333333333333", "unattributed")])
def test_the_block_filters_runs_exactly_as_period_stats_does(suite, release):
    """"Same scope, same basis": the block's run filter is ``_period_stats``'s,
    predicate for predicate, with nothing left over but its two window bounds
    (two modules, one rule -- pinned here because they cannot share the list
    without rewriting ``_period_stats``, whose source other guards read)."""
    import asyncio

    from sqlalchemy import select

    from app.models.postgres import TestRun
    from app.services.analytics_scope import suite_keys

    project = str(uuid.uuid4())
    legacy = _Capture()
    asyncio.run(metrics_service._period_stats(legacy, project, NOW - timedelta(days=7), NOW, suite, release))
    remaining = _where(legacy.sql[-1])  # the run-level statement on either branch
    conditions = rms.scope_conditions(project, suite_keys(suite), release)
    assert conditions
    for condition in conditions:
        piece = _where(str(select(TestRun.id).where(condition).compile(dialect=postgresql.dialect())))
        assert piece in remaining, piece
        remaining = remaining.replace(piece, "", 1)
    # An OR-shaped condition compiles bare alone and parenthesised in the
    # conjunction, so removing it leaves an empty "()".
    leftovers = [part.strip() for part in remaining.split(" AND ") if part.strip("() ")]
    assert len(leftovers) == 2 and all("test_runs.created_at" in part for part in leftovers), leftovers

    # ...and the block's own statements carry that filter.
    block = _Capture()
    try:
        asyncio.run(rms.build_report_metrics(block, project, 7, suite, release, now=NOW))
    except Exception:
        pass  # the fake rows are thin; the statements are what is checked
    assert block.sql, "the block built no statement: the check below would be vacuous"
    for condition in conditions:
        piece = _where(str(select(TestRun.id).where(condition).compile(dialect=postgresql.dialect())))
        assert all(piece in stmt for stmt in block.sql), piece


# ── the cache: the key moves with the payload shape ─────────────────────────


class _Cache:
    """A dict cache keyed exactly as ``cache_service`` keys Redis."""

    def __init__(self):
        from app.services.cache_service import _build_cache_key

        self.key = _build_cache_key
        self.store: dict[str, dict] = {}
        self.reads: list[str] = []

    async def get(self, namespace, project_id, *, epoch, **kw):
        key = self.key(namespace, project_id, epoch=epoch, **kw)
        self.reads.append(key)
        return self.store.get(key)

    async def set(self, namespace, value, project_id, *, ttl=None, epoch, **kw):
        self.store[self.key(namespace, project_id, epoch=epoch, **kw)] = value


def _wired(cache: _Cache):
    block = _EMPTY_BLOCK
    result = MagicMock()
    result.scalar.return_value = 0
    return (
        patch("app.services.cache_service.get_analytics_epoch", AsyncMock(return_value=4)),
        patch("app.services.cache_service.cache_get", cache.get),
        patch("app.services.cache_service.cache_set", cache.set),
        patch.object(metrics_service, "_period_stats", AsyncMock(return_value={
            "total_runs": 1, "total_executions": 5, "pass_rate": 80.0, "avg_duration_ms": 10})),
        patch.object(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0)),
        patch.object(metrics_service, "_resolve_policy_for_project", AsyncMock(return_value=None)),
        patch.object(rms, "build_report_metrics", AsyncMock(return_value=block)),
        SimpleNamespace(execute=AsyncMock(return_value=result)),
    )


async def _call(db, **kw):
    return await metrics_service.get_dashboard_summary(
        db, "p1", 7, meta_builder=AsyncMock(return_value={"generated_at": "x", "as_of": "x"}), **kw,
    )


@pytest.mark.asyncio
async def test_an_old_shape_entry_is_never_served_as_the_new_shape():
    cache = _Cache()
    *patches, db = _wired(cache)
    # The envelope's own version, not a literal: bumping META_SCHEMA_VERSION
    # (VIZ-203 added two `meta` keys) must not read as "the legacy call stopped
    # hitting its entry" -- what this test pins is the `metrics=` segment.
    old_key = cache.key(
        "dashboard_summary_v2", "p1", epoch=4, days=7, suite="",
        schema=analytics_meta.META_SCHEMA_VERSION,
    )
    cache.store[old_key] = {"total_executions_7d": {"value": 999}, "meta": {"generated_at": "x"}}
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        # The legacy call (no block) still hits the entry it always hit...
        legacy = await _call(db)
        assert legacy["total_executions_7d"]["value"] == 999
        # ...and the new shape never reads it.
        fresh = await _call(db, report_metrics=True)
    assert fresh["total_executions_7d"]["value"] == 5
    assert "report_metrics" in fresh
    assert cache.reads[-1] != old_key
    assert f"metrics={rms.REPORT_METRICS_SCHEMA_VERSION}" in cache.reads[-1]


@pytest.mark.asyncio
async def test_a_cached_payload_without_the_block_is_a_miss():
    cache = _Cache()
    *patches, db = _wired(cache)
    new_key = cache.key("dashboard_summary_v2", "p1", epoch=4, days=7, suite="", schema=2,
                        metrics=rms.REPORT_METRICS_SCHEMA_VERSION)
    cache.store[new_key] = {"total_executions_7d": {"value": 999}, "meta": {"generated_at": "x"}}
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        out = await _call(db, report_metrics=True)
    assert out["total_executions_7d"]["value"] == 5 and "report_metrics" in out


@pytest.mark.asyncio
async def test_the_block_is_cached_with_the_payload():
    cache = _Cache()
    *patches, db = _wired(cache)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as build:
        first = await _call(db, report_metrics=True)
        second = await _call(db, report_metrics=True)
    assert build.await_count == 1
    assert second["report_metrics"] == first["report_metrics"]


@pytest.mark.asyncio
async def test_without_the_flag_the_payload_and_key_are_unchanged():
    cache = _Cache()
    *patches, db = _wired(cache)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as build:
        out = await _call(db)
    build.assert_not_awaited()
    assert "report_metrics" not in out
    assert "metrics=" not in cache.reads[-1]


# ── the route asks for it, denied or not ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("include, wanted", [
    # Opt-in (fix round C, m1): no ``include`` is the pre-VIZ-302 request --
    # no block, no block queries, the old cache key.
    (None, False),
    ([], False),
    (["something_else"], False),
    (["report_metrics"], True),
    ([" Report_Metrics "], True),
    (["meta,report_metrics"], True),
    (["x", "report_metrics"], True),
])
async def test_the_summary_route_computes_the_block_only_when_asked(include, wanted):
    from app.routers import metrics as router
    from app.services.analytics_scope import AnalyticsScope

    scope = AnalyticsScope(uuid.uuid4(), None, (), (), 7)
    svc = AsyncMock(return_value={})
    with patch.object(router, "get_dashboard_summary", svc):
        if include is None:
            # A direct call that omits it (the route's default) must not get
            # FastAPI's ``Query`` object as a truthy value.
            await router.dashboard_summary(scope=scope, db=AsyncMock())
        else:
            await router.dashboard_summary(scope=scope, db=AsyncMock(), include=include)
    assert svc.await_args.kwargs["report_metrics"] is wanted


def test_the_include_param_is_declared_on_the_summary_route():
    """Over HTTP the flag arrives as ``?include=report_metrics``: the route
    must actually declare the query parameter (a handler default nobody can
    set would leave the block unreachable)."""
    from fastapi.dependencies.utils import get_flat_dependant

    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v1/metrics/summary")
    names = {p.name for p in get_flat_dependant(route.dependant).query_params}
    assert "include" in names


@pytest.mark.asyncio
async def test_a_denied_scope_keeps_its_historical_payload():
    """A scope the caller may not read answers ``{meta}`` exactly as before:
    no block, so nothing about the project is computed for it."""
    from app.routers import metrics as router
    from app.services.analytics_scope import AnalyticsScope

    scope = AnalyticsScope(None, frozenset(), (), (), 7, denied=True)
    svc = AsyncMock()
    with patch.object(router, "build_meta", AsyncMock(return_value={"measured": False})), \
         patch.object(router, "get_dashboard_summary", svc):
        out = await router.dashboard_summary(scope=scope, db=AsyncMock())
    assert set(out) == {"meta"}
    svc.assert_not_awaited()
