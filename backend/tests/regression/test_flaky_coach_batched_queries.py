"""Regression pins for the flaky-coach batching refactor (perf/flaky-coach-batch).

``refresh_flaky_coach`` previously issued ``status_q`` + ``tc_q`` per candidate
fingerprint — a ``1 + 1 + 2N`` N+1 on a request path (POST /flaky-coach/refresh).
The per-fingerprint lookups are now batched into two queries: a windowed query
(ROW_NUMBER PARTITION BY fingerprint, top-30 by created_at desc) and a
DISTINCT ON for the latest test name/suite. So the round-trip count is constant
(4) regardless of candidate count, and the flaky classification is unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


def _now():
    return datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_refresh_flaky_coach_batches_and_preserves_classification():
    from app.services import test_health_coach_service as svc

    project_id = uuid.uuid4()
    t1 = _now() - timedelta(days=3)
    t2 = _now() - timedelta(days=2)
    t3 = _now() - timedelta(days=1)

    # 3 candidate fingerprints: fpA flaky (pass+fail); fpB pass-only (filtered);
    # fpC fail-only (filtered).
    candidates = _Result([
        SimpleNamespace(test_fingerprint="fpA"),
        SimpleNamespace(test_fingerprint="fpB"),
        SimpleNamespace(test_fingerprint="fpC"),
    ])
    # status rows, ordered by (fp, created_at desc) — as the windowed query returns them.
    status_rows = _Result([
        SimpleNamespace(fp="fpA", status="FAILED", created_at=t3),
        SimpleNamespace(fp="fpA", status="PASSED", created_at=t2),
        SimpleNamespace(fp="fpA", status="FAILED", created_at=t1),
        SimpleNamespace(fp="fpB", status="PASSED", created_at=t3),
        SimpleNamespace(fp="fpB", status="PASSED", created_at=t2),
        SimpleNamespace(fp="fpB", status="PASSED", created_at=t1),
        SimpleNamespace(fp="fpC", status="FAILED", created_at=t3),
        SimpleNamespace(fp="fpC", status="FAILED", created_at=t2),
        SimpleNamespace(fp="fpC", status="FAILED", created_at=t1),
    ])
    tc_rows = _Result([
        SimpleNamespace(fp="fpA", test_name="testA", suite_name="S"),
        SimpleNamespace(fp="fpB", test_name="testB", suite_name="S"),
        SimpleNamespace(fp="fpC", test_name="testC", suite_name="S"),
    ])

    # execute() order: 1 candidates, 2 delete, 3 status batch, 4 tc batch
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=[candidates, _Result([]), status_rows, tc_rows])
    added = []
    db.add = lambda obj: added.append(obj)

    count = await svc.refresh_flaky_coach(project_id, db, days=30)

    # Constant 4 round trips — NOT 1 + 1 + 2*3 = 8.
    assert db.execute.await_count == 4
    # Only fpA is flaky (fpB pass-only, fpC fail-only are filtered out).
    assert count == 1
    assert len(added) == 1
    res = added[0]
    assert res.test_fingerprint == "fpA"
    assert res.total_runs == 3
    assert res.failed_runs == 2
    assert abs(res.failure_rate - (2 / 3)) < 1e-9
    assert res.test_name == "testA"        # latest-name DISTINCT ON
    assert res.suite_name == "S"
    assert res.status_history == ["FAILED", "PASSED", "FAILED"]  # created_at-desc order
    assert res.flaky_since == t1           # earliest failure
    assert res.last_failure_at == t3       # latest failure


@pytest.mark.asyncio
async def test_refresh_flaky_coach_no_candidates_skips_batch_queries():
    from app.services import test_health_coach_service as svc

    db = SimpleNamespace()
    # candidates empty → only candidates fetch + delete run; no status/tc batch.
    db.execute = AsyncMock(side_effect=[_Result([]), _Result([])])
    db.add = lambda obj: None

    count = await svc.refresh_flaky_coach(uuid.uuid4(), db, days=30)
    assert count == 0
    assert db.execute.await_count == 2  # candidates + delete only
