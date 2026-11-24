"""D-25: the Home KPI's relative trend needs a fair previous window.

A project whose first run is 9 days old, read with days=7, has a previous
window (days 8..14 ago) that is mostly before it existed: one partial day gave
"Up 2500% vs prev period (better)". The trend is ``None`` with direction
"none" unless the previous window lies wholly inside the project's history and
holds at least ``MIN_PREVIOUS_RUNS`` runs. ``pass_rate_delta_pp`` (the move in
points that Trends prints) follows the same rule, so Home and Trends agree.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if importlib.util.find_spec("aiosmtplib") is None:  # pragma: no cover - CI installs it
    # The import chain pulls the mailer in; stub it only where it is not installed.
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import cache_service  # noqa: E402
from app.services import metrics_service  # noqa: E402


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache_service, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "cache_set", AsyncMock())
    monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=0))


def _stats(runs: int, executions: int, pass_rate: float = 90.0) -> dict:
    return {"total_runs": runs, "total_executions": executions, "pass_rate": pass_rate, "avg_duration_ms": 10}


def _wire(monkeypatch: pytest.MonkeyPatch, *, cur: dict, prev: dict, first_run: datetime | None):
    calls: list[int] = []

    async def _period_stats(db, project_id, start, end, suite, release_id, *, allowed_project_ids=None):
        answer = cur if not calls else prev
        calls.append(1)
        return answer

    monkeypatch.setattr(metrics_service, "_period_stats", _period_stats)
    monkeypatch.setattr(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0))
    monkeypatch.setattr(metrics_service, "resolve_readiness_policy", AsyncMock(return_value=None))
    monkeypatch.setattr(metrics_service, "first_run_at", AsyncMock(return_value=first_run))
    result = MagicMock()
    result.scalar.return_value = 0
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def _ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def test_a_nine_day_old_project_at_seven_days_has_no_executions_trend(monkeypatch):
    # The reported shape: 26 x the executions of one partial day before.
    db = _wire(monkeypatch, cur=_stats(52, 2600), prev=_stats(3, 100), first_run=_ago(9))
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    card = out["total_executions_7d"]
    assert card["trend"] is None
    assert card["trend_direction"] == "none"
    assert out["avg_pass_rate_7d"]["trend"] is None
    assert out["avg_pass_rate_7d"]["trend_direction"] == "none"


async def test_a_thin_previous_window_has_no_trend_even_inside_history(monkeypatch):
    db = _wire(monkeypatch, cur=_stats(40, 400), prev=_stats(2, 20), first_run=_ago(60))
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    assert out["total_executions_7d"]["trend"] is None
    assert out["total_executions_7d"]["trend_direction"] == "none"


async def test_no_run_at_all_has_no_trend(monkeypatch):
    db = _wire(monkeypatch, cur=_stats(0, 0), prev=_stats(0, 0), first_run=None)
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    assert out["total_executions_7d"]["trend_direction"] == "none"


async def test_an_ordinary_previous_window_keeps_its_trend(monkeypatch):
    db = _wire(monkeypatch, cur=_stats(10, 150), prev=_stats(10, 100), first_run=_ago(60))
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    card = out["total_executions_7d"]
    assert card["trend"] == 50.0 and card["trend_direction"] == "up"


async def test_the_pass_rate_points_follow_the_same_rule(monkeypatch):
    # Young project: Home says "no earlier period to compare", so Trends must
    # not print "+8 pp vs previous 7 days" from the same payload.
    db = _wire(monkeypatch, cur=_stats(20, 200, 88.0), prev=_stats(3, 30, 80.0), first_run=_ago(9))
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    assert out["avg_pass_rate_7d"]["pass_rate_delta_pp"] is None

    db = _wire(monkeypatch, cur=_stats(20, 200, 88.0), prev=_stats(10, 100, 80.0), first_run=_ago(60))
    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=7)
    assert out["avg_pass_rate_7d"]["pass_rate_delta_pp"] == 8.0


@pytest.mark.parametrize(
    ("first_run_days_ago", "prev_runs", "expected"),
    [(15, 3, True), (13, 50, False), (30, metrics_service.MIN_PREVIOUS_RUNS - 1, False)],
)
def test_previous_window_comparable(first_run_days_ago, prev_runs, expected):
    prev_start = _ago(14)
    assert metrics_service.previous_window_comparable(
        _ago(first_run_days_ago), prev_start, prev_runs
    ) is expected


def test_relative_trend_is_none_without_a_fair_base():
    assert metrics_service.relative_trend(26, 1, comparable=False) is None
    assert metrics_service.relative_trend(26, 0, comparable=True) is None
    assert metrics_service.relative_trend(150, 100, comparable=True) == 50.0
