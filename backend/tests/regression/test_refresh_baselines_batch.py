"""Regression pin for the perf/refresh-baselines-batch fix.

``refresh_baselines`` issued a per-TestCase-row ``SELECT PerfBaseline`` inside
``record_observation`` (1 + N queries on the nightly sweep). It now prefetches
the batch's baselines in ONE query and caches the per-(project, fingerprint)
baseline as it goes.

The correctness-critical case: a batch routinely contains MULTIPLE rows for the
same (project, fingerprint) (each run of a test accumulates into one Welford
baseline). The old per-row SELECT saw the just-created row via autoflush; the
batched path must cache the newly-created baseline so repeats accumulate into
ONE row — otherwise it would create duplicates and violate
``uq_perf_baseline_fingerprint``. This test drives the REAL ``record_observation``
+ a real ``PerfBaseline`` to pin that behavior.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    """Captures db.add()ed objects; returns the sweep rows then an empty
    baseline prefetch (so every fingerprint is newly created)."""

    def __init__(self, sweep_rows):
        self.added = []
        self.commit = AsyncMock()
        self._results = [_Result(sweep_rows), _Result([])]  # sweep, prefetch
        self._i = 0

    async def execute(self, stmt):
        r = self._results[self._i] if self._i < len(self._results) else _Result([])
        self._i += 1
        return r

    def add(self, obj):
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_repeated_fingerprint_accumulates_into_one_baseline():
    from app.models.postgres import PerfBaseline
    from app.services import perf_regression_service as svc

    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pid = uuid.uuid4()
    # fpX twice (100ms, 200ms), fpY once (50ms) — fpX MUST become one baseline.
    rows = [
        SimpleNamespace(project_id=pid, test_fingerprint="fpX", duration_ms=100,
                        test_name="x", suite_name="s", created_at=base + timedelta(minutes=1)),
        SimpleNamespace(project_id=pid, test_fingerprint="fpY", duration_ms=50,
                        test_name="y", suite_name="s", created_at=base + timedelta(minutes=2)),
        SimpleNamespace(project_id=pid, test_fingerprint="fpX", duration_ms=200,
                        test_name="x", suite_name="s", created_at=base + timedelta(minutes=3)),
    ]
    sess = _Session(rows)

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: sess), \
         patch.object(svc, "_load_refresh_cursor", AsyncMock(return_value=None)), \
         patch.object(svc, "_save_refresh_cursor", AsyncMock()):
        out = await svc.refresh_baselines()

    baselines = [o for o in sess.added if isinstance(o, PerfBaseline)]
    by_fp = {b.test_fingerprint: b for b in baselines}

    # ONE baseline per distinct fingerprint — fpX NOT duplicated.
    assert len(baselines) == 2
    assert set(by_fp) == {"fpX", "fpY"}
    # fpX accumulated BOTH observations (Welford): n=2, mean=(100+200)/2=150.
    assert by_fp["fpX"].sample_count == 2
    assert by_fp["fpX"].mean_ms == 150.0
    assert by_fp["fpY"].sample_count == 1
    assert by_fp["fpY"].mean_ms == 50.0
    # 3 observed rows, 2 distinct baselines.
    assert out["observed"] == 3
    assert out["baselines"] == 2
    # Only TWO db.execute calls: the sweep + ONE batched prefetch (no per-row SELECT).
    assert sess._i == 2


@pytest.mark.asyncio
async def test_existing_baseline_is_reused_not_recreated():
    from app.models.postgres import PerfBaseline
    from app.services import perf_regression_service as svc

    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pid = uuid.uuid4()
    existing = PerfBaseline(project_id=pid, test_fingerprint="fpZ", test_name="z", suite_name="s")
    svc._welford_update(existing, 100)  # pre-existing single observation
    rows = [
        SimpleNamespace(project_id=pid, test_fingerprint="fpZ", duration_ms=300,
                        test_name="z", suite_name="s", created_at=base + timedelta(minutes=1)),
    ]

    class _Sess(_Session):
        def __init__(self, sweep_rows, prefetched):
            super().__init__(sweep_rows)
            self._results = [_Result(sweep_rows), _Result(prefetched)]

    sess = _Sess(rows, [existing])

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: sess), \
         patch.object(svc, "_load_refresh_cursor", AsyncMock(return_value=None)), \
         patch.object(svc, "_save_refresh_cursor", AsyncMock()):
        await svc.refresh_baselines()

    # No NEW baseline added — the prefetched one was reused + updated (n: 1 -> 2).
    assert [o for o in sess.added if isinstance(o, PerfBaseline)] == []
    assert existing.sample_count == 2
