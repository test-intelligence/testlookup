"""FAIL-02: the median time to fix -- the response contract and the statement's binds.

The episode rule itself is proven on Postgres
(``integration/test_time_to_fix_postgres.py``); this file pins what a fake
session can: the exact keys, the floor ("Not measured" below 5 fixes, never
0), rounding, and that the one statement is bound and tenant-scoped in every
half (the executions, the quarantine requests and the human triage).
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest

from app.services import analytics_service as svc
from app.services.analytics_scope import AnalyticsScope

KEYS = {"measured", "reason", "median_hours", "mean_hours", "fixed", "open",
        "excluded_flaky_tests", "excluded_oscillating_tests", "min_fixes", "window_days"}


class _Session:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return SimpleNamespace(one=lambda: SimpleNamespace(_mapping=self.row))


def _scope(project=None, allowed=None, days=30, releases=(), suites=()):
    return AnalyticsScope(project_id=project, allowed_project_ids=allowed, release_ids=tuple(releases),
                          suite_names=tuple(suites), days=days)


def _row(**kw):
    base = {"fixed": 0, "open": 0, "median_hours": None, "mean_hours": None,
            "excluded_flaky_tests": 0, "excluded_oscillating_tests": 0}
    base.update(kw)
    return base


def _placeholders(sql: str) -> set[str]:
    return set(re.findall(r"(?<!:):([a-z_][a-z0-9_]*)", sql))


def test_the_constants_are_the_contracts():
    assert svc.TIME_TO_FIX_MIN_FIXES == 5
    assert svc.TIME_TO_FIX_OSCILLATION_MIN == 3


@pytest.mark.asyncio
async def test_below_five_fixes_is_not_measured_with_the_reason_never_zero():
    db = _Session(_row(fixed=4, open=2, median_hours=12.0, mean_hours=13.0))
    out = await svc.time_to_fix(db, _scope(uuid.uuid4(), days=14))
    assert set(out) == KEYS
    assert out["measured"] is False
    assert out["median_hours"] is None and out["mean_hours"] is None
    assert out["reason"] == "Fewer than 5 tests were fixed in the last 14 days (4 fixed)."
    assert (out["fixed"], out["open"], out["min_fixes"], out["window_days"]) == (4, 2, 5, 14)


@pytest.mark.asyncio
async def test_five_fixes_are_measured_and_hours_round_to_one_decimal():
    from decimal import Decimal

    db = _Session(_row(fixed=23, open=1, median_hours=Decimal("43.21666"), mean_hours=55.04999,
                       excluded_flaky_tests=2, excluded_oscillating_tests=1))
    out = await svc.time_to_fix(db, _scope(uuid.uuid4()))
    assert out == {
        "measured": True, "reason": None, "median_hours": 43.2, "mean_hours": 55.0,
        "fixed": 23, "open": 1, "excluded_flaky_tests": 2, "excluded_oscillating_tests": 1,
        "min_fixes": 5, "window_days": 30,
    }
    assert isinstance(out["median_hours"], float)
    db = _Session(_row(fixed=5, median_hours=1.0, mean_hours=1.0))
    assert (await svc.time_to_fix(db, _scope(uuid.uuid4())))["measured"] is True


@pytest.mark.asyncio
async def test_one_statement_every_bind_bound_and_every_half_tenant_scoped():
    from app.services.flaky_quarantine_service import _LIVE_STATES

    pid = uuid.uuid4()
    rid = "3f9a5f7e-1c2b-4d6a-9e8f-0a1b2c3d4e5f"
    db = _Session(_row())
    await svc.time_to_fix(db, _scope(pid, releases=(rid,), suites=("checkout",)))
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert not (_placeholders(sql) - set(params) - {"live_states"}), "a bind the statement names is unbound"
    assert params["live_states"] == list(_LIVE_STATES)
    assert params["flaky_status"] == "FLAKY_TEST"
    assert params["oscillation_min"] == 3
    assert params["project_id"] == str(pid)
    for alias in ("tr", "q", "tr2"):
        assert f"AND {alias}.project_id = :project_id" in sql, alias
        assert f"{alias}.project_id IN (SELECT id FROM projects WHERE is_active)" in sql, alias
    assert "tr.primary_release_id = :release_id" in sql and params["release_id"] == rid
    assert params["suite_name"] == "checkout"
    assert "flaky_quarantine_requests q" in sql
    # A test is (project, fingerprint): every window partitions by both.
    assert sql.count("PARTITION BY pid, fp") == 2


@pytest.mark.asyncio
async def test_a_member_with_no_project_reads_only_their_projects_and_an_empty_set_reads_nothing():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = _Session(_row())
    await svc.time_to_fix(db, _scope(allowed=frozenset({a, b})))
    sql, params = db.calls[0]
    assert {params["pid_0"], params["pid_1"]} == {str(a), str(b)}
    for alias in ("tr", "q", "tr2"):
        assert f"AND {alias}.project_id IN (:pid_0, :pid_1)" in sql, alias
    db = _Session(_row())
    await svc.time_to_fix(db, _scope(allowed=frozenset()))
    assert db.calls[0][0].count("AND FALSE") == 3


def test_the_route_is_windowed_to_90_days_and_cached():
    import inspect

    from app.routers import analytics

    assert analytics._TIME_TO_FIX.default_days == 30
    assert analytics._TIME_TO_FIX.max_days == 90
    src = inspect.getsource(analytics)
    assert '@router.get("/time-to-fix")\n@analytics_error_contract\n@analytics_read(namespace="time_to_fix")' in src
