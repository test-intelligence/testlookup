"""TRND-05: window-honest KPIs.

* Trends' pass-rate tile printed the summary's RELATIVE change as "pp": a
  window that moved 80 % -> 88 % read "10pp vs prev window". The summary now
  carries ``avg_pass_rate_7d.pass_rate_delta_pp`` (8.0) beside the relative
  ``trend`` (10.0), which other consumers keep reading.
* Value Metrics' hours-saved headline was a fixed trailing 30 days whatever
  the window picker said. ``get_hours_saved_model(..., headline_days)`` now
  computes the headline (and its availability gate) over the page's window;
  ``hours_saved_30d`` keeps its 30-day meaning for the digest and old readers.
"""
from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import cache_service  # noqa: E402
from app.services import metrics_service  # noqa: E402
from app.services import value_metrics_service as vms  # noqa: E402


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache_service, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "cache_set", AsyncMock())
    monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=0))


# ── Trends: percentage points, not a relative change ────────────────────────


def _stats(pass_rate: float, executions: int) -> dict:
    return {"total_runs": 3, "total_executions": executions, "pass_rate": pass_rate, "avg_duration_ms": 10}


class TestPassRateDeltaPp:
    def test_eighty_to_eighty_eight_is_eight_points(self):
        assert metrics_service.pass_rate_delta_pp(_stats(88.0, 50), _stats(80.0, 50)) == 8.0

    def test_a_drop_is_negative(self):
        assert metrics_service.pass_rate_delta_pp(_stats(71.25, 50), _stats(90.0, 50)) == -18.8

    @pytest.mark.parametrize(("cur", "prev"), [(_stats(88.0, 50), _stats(0.0, 0)), (_stats(0.0, 0), _stats(80.0, 50))])
    def test_an_empty_window_has_no_delta(self, cur, prev):
        # 0 % read off a window with no executions is not a pass rate.
        assert metrics_service.pass_rate_delta_pp(cur, prev) is None


def _wire_summary(monkeypatch: pytest.MonkeyPatch, *, cur: dict, prev: dict):
    windows = {}

    async def _period_stats(db, project_id, start, end, suite, release_id):
        # The current window is read first, then the one before it.
        answer = cur if not windows else prev
        windows[len(windows)] = (start, end)
        return answer

    monkeypatch.setattr(metrics_service, "_period_stats", _period_stats)
    monkeypatch.setattr(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0))
    monkeypatch.setattr(metrics_service, "_resolve_policy_for_project", AsyncMock(return_value=None))
    result = MagicMock()
    result.scalar.return_value = 0
    return SimpleNamespace(execute=AsyncMock(return_value=result))


async def test_the_summary_publishes_points_beside_the_relative_trend(monkeypatch):
    db = _wire_summary(monkeypatch, cur=_stats(88.0, 40), prev=_stats(80.0, 40))

    out = await metrics_service.get_dashboard_summary(db, str(uuid.uuid4()), days=30)

    card = out["avg_pass_rate_7d"]
    assert card["pass_rate_delta_pp"] == 8.0
    # The relative change other consumers read is unchanged: (88 - 80) / 80.
    assert card["trend"] == 10.0


async def test_the_meta_routes_key_on_the_payload_shape(monkeypatch):
    """An entry cached before the delta existed is never served to the pages."""
    db = _wire_summary(monkeypatch, cur=_stats(88.0, 40), prev=_stats(80.0, 40))

    async def _meta() -> dict:
        return {"as_of": "now"}

    await metrics_service.get_dashboard_summary(db, "p1", days=30, meta_builder=_meta)

    kwargs = cache_service.cache_get.await_args.kwargs
    assert kwargs["shape"] == metrics_service.SUMMARY_SHAPE_VERSION
    assert metrics_service.SUMMARY_SHAPE_VERSION >= 2


# ── Value Metrics: the headline follows the window ──────────────────────────

NOW = datetime.now(timezone.utc)


def _wire_model(monkeypatch: pytest.MonkeyPatch, clustered_per_day: int = 3):
    """``clustered_failures`` grows with the window: N days -> 3N failures,
    each worth 20 default triage minutes (1 h per day of window)."""
    monkeypatch.setattr(vms, "get_effective_assumptions", AsyncMock(return_value=vms.EffectiveAssumptions()))
    monkeypatch.setattr(vms, "_run_span", AsyncMock(return_value=(NOW - timedelta(days=400), NOW)))
    windows: list[int] = []

    async def fake_gather(db, project_id, since, monthly):
        if monthly:
            return {}
        days = round((datetime.now(timezone.utc) - since) / timedelta(days=1))
        windows.append(days)
        return {"_total": {"clustered_failures": clustered_per_day * days}}

    monkeypatch.setattr(vms, "_gather_leg_counts", fake_gather)
    return windows


@pytest.mark.parametrize(("days", "hours"), [(7, 7.0), (30, 30.0), (90, 90.0)])
async def test_the_headline_covers_the_requested_window(monkeypatch, days, hours):
    windows = _wire_model(monkeypatch)

    model = await vms.get_hours_saved_model(MagicMock(), uuid.uuid4(), months=6, headline_days=days)

    headline = model["headline"]
    assert headline["window_days"] == days
    assert headline["hours_saved"] == hours
    # Pro-rated FTE: hours over (173.2 h/month x days/30).
    assert headline["fte_equivalent"] == round(hours / 173.2 / (days / 30), 3)
    # The 30-day compatibility figures keep their meaning in every window.
    assert headline["hours_saved_30d"] == 30.0
    assert headline["fte_equivalent_30d"] == round(30.0 / 173.2, 3)
    assert model["available"] is True
    # One extra query only when the window is not the 30-day one.
    assert sorted(set(windows)) == sorted({days, 30})


async def test_the_gate_reads_the_selected_window(monkeypatch):
    _wire_model(monkeypatch, clustered_per_day=0)

    model = await vms.get_hours_saved_model(MagicMock(), uuid.uuid4(), headline_days=7)

    assert model["available"] is False
    assert model["insufficient_data_reason"] == "no automation signal in the last 7 days"
    assert model["headline"]["hours_saved"] == 0.0


async def test_the_window_is_part_of_the_cache_key(monkeypatch):
    _wire_model(monkeypatch)
    project_id = uuid.uuid4()

    await vms.get_hours_saved_model(MagicMock(), project_id, headline_days=90)

    assert cache_service.cache_get.await_args.kwargs["w"] == 90
    assert cache_service.cache_set.await_args.kwargs["w"] == 90


async def test_the_digest_headline_stays_thirty_days(monkeypatch):
    windows = _wire_model(monkeypatch)

    out = await vms.compute_headline(MagicMock(), uuid.uuid4())

    assert out == {"available": True, "hours_saved_30d": 30.0}
    assert windows == [30]


async def test_value_metrics_hand_the_window_to_the_headline(monkeypatch):
    model = AsyncMock(return_value={"headline": {}})
    monkeypatch.setattr(vms, "get_hours_saved_model", model)
    monkeypatch.setattr(vms, "get_effective_assumptions", AsyncMock(return_value=vms.EffectiveAssumptions()))
    result = MagicMock()
    result.scalar.return_value = 0
    result.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    project_id = uuid.uuid4()

    await vms.get_value_metrics(db, project_id=project_id, days=90, months=6)

    model.assert_awaited_once_with(db, project_id, months=6, headline_days=90)
