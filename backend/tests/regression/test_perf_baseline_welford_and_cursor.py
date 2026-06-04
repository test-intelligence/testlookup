"""Regression pins for the perf_regression_service review (review/perf-regression-service).

Two bugs fixed:

1. [Major] ``_welford_update`` crashed with ``TypeError: float() argument ...
   not 'NoneType'`` on the FIRST observation of a new baseline: a pending
   ``PerfBaseline`` has ``stddev_ms=None`` (the column default applies at INSERT,
   and the update runs before any flush), and the ``n>=2`` guard left it None
   when the p95 line did ``float(stddev_ms)``. So ``refresh_baselines`` threw
   before its commit and NO baseline was ever created — the feature was inert
   whenever the flag was on. Now stddev is set to 0.0 for n<2 and the p95 line
   coalesces None.

2. [Major] ``refresh_baselines`` re-swept the most-recent 5000 TestCase rows
   every night with no watermark. Welford is additive, so rows lingering in the
   window were counted repeatedly, inflating ``sample_count`` and corrupting the
   stats. Now an app_settings cursor (``perf_baseline.refresh_cursor``) drives an
   exactly-once, oldest-first incremental sweep (``created_at > cursor`` ASC).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.regression


# ── Bug 1: Welford never crashes + is correct ───────────────────────────────────

def test_welford_first_observation_does_not_crash():
    from app.models.postgres import PerfBaseline
    from app.services.perf_regression_service import _welford_update

    b = PerfBaseline(project_id=uuid.uuid4(), test_fingerprint="fp")
    assert b.stddev_ms is None  # pending row: column default not yet applied
    _welford_update(b, 100)  # must not raise
    assert b.sample_count == 1
    assert b.mean_ms == 100.0
    assert b.stddev_ms == 0.0
    assert b.p95_ms == 100.0  # mean + 1.645*0


def test_welford_tracks_mean_and_stddev_and_spike():
    from app.models.postgres import PerfBaseline
    from app.services.perf_regression_service import _welford_update, is_spike

    b = PerfBaseline(project_id=uuid.uuid4(), test_fingerprint="fp")
    # 12 samples with real variance around ~100ms
    for ms in [90, 110, 95, 105, 100, 98, 102, 97, 103, 99, 101, 100]:
        _welford_update(b, ms)
    assert b.sample_count == 12
    assert 95 < b.mean_ms < 105
    assert b.stddev_ms > 0
    # within-noise observation is not a spike; a 10x jump is
    assert is_spike(b, 101) is False
    assert is_spike(b, 1000) is True


def test_is_spike_requires_min_samples():
    from app.models.postgres import PerfBaseline
    from app.services.perf_regression_service import _welford_update, is_spike

    b = PerfBaseline(project_id=uuid.uuid4(), test_fingerprint="fp")
    for ms in [90, 110, 95, 105, 100]:  # only 5 < _MIN_SAMPLES_FOR_SPIKE (10)
        _welford_update(b, ms)
    assert is_spike(b, 999_999) is False


# ── Bug 2: incremental, exactly-once refresh via the app_settings cursor ────────

@pytest.mark.asyncio
async def test_refresh_cursor_round_trips():
    from app.models.postgres import AppSetting
    from app.services import perf_regression_service as svc

    store: dict[str, AppSetting] = {}

    class _DB:
        async def execute(self, _stmt):
            setting = next(iter(store.values()), None)
            return SimpleNamespace(scalar_one_or_none=lambda: setting)

        def add(self, obj):
            store[obj.key] = obj

    db = _DB()
    when = datetime(2026, 6, 2, 4, 30, tzinfo=timezone.utc)
    assert await svc._load_refresh_cursor(db) is None
    await svc._save_refresh_cursor(db, when)
    assert await svc._load_refresh_cursor(db) == when


@pytest.mark.asyncio
async def test_refresh_baselines_is_incremental_and_advances_cursor():
    from app.services import perf_regression_service as svc

    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(project_id=uuid.uuid4(), test_fingerprint="fp1",
                        duration_ms=100, test_name="a", suite_name="s",
                        created_at=base + timedelta(minutes=1)),
        SimpleNamespace(project_id=uuid.uuid4(), test_fingerprint=None,  # skipped
                        duration_ms=120, test_name="b", suite_name="s",
                        created_at=base + timedelta(minutes=2)),
        SimpleNamespace(project_id=uuid.uuid4(), test_fingerprint="fp3",
                        duration_ms=130, test_name="c", suite_name="s",
                        created_at=base + timedelta(minutes=3)),
    ]
    captured_sql: list[str] = []

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: rows)

    class _Session:
        def __init__(self):
            self.commit = AsyncMock()

        async def execute(self, stmt):
            captured_sql.append(str(stmt).lower())
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    cursor = base  # pretend last run stopped at `base`
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: _Session()), \
         patch.object(svc, "_load_refresh_cursor", AsyncMock(return_value=cursor)), \
         patch.object(svc, "_save_refresh_cursor", AsyncMock()) as save_mock, \
         patch.object(svc, "record_observation", AsyncMock()) as rec_mock:
        out = await svc.refresh_baselines()

    # only the two fingerprinted rows are recorded (exactly once each)
    assert rec_mock.await_count == 2
    assert out["observed"] == 2
    # cursor advanced to the newest swept row (incl. the skipped one)
    assert save_mock.await_args.args[1] == base + timedelta(minutes=3)
    # the sweep is bounded by the cursor and ordered oldest-first
    sweep = next((s for s in captured_sql if "test_cases" in s and "created_at >" in s), None)
    assert sweep is not None, captured_sql
    assert "order by" in sweep and "created_at asc" in sweep
    # the per-row PerfBaseline SELECT is replaced by ONE batched IN prefetch
    assert any("perf_baselines" in s and " in (" in s for s in captured_sql), captured_sql


@pytest.mark.asyncio
async def test_refresh_baselines_noop_when_flag_off():
    from app.services import perf_regression_service as svc

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        out = await svc.refresh_baselines()
    assert out == {"observed": 0, "baselines": 0, "skipped": 1}
