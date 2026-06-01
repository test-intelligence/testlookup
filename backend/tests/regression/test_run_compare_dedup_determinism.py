"""Regression: run-compare per-fingerprint dedup was nondeterministic.

Bug pinned (review/run-compare-service, 2026-06-01):

``_load_test_rows`` keeps one TestCase per ``test_fingerprint`` (retries inside
one run share a fingerprint) via a keep-last loop, but the query had no
``ORDER BY`` — so which retry won was whatever order Postgres returned,
making the compared status/duration for a retried test flip between otherwise
identical requests. Fix: order ``created_at, id`` ascending so keep-last
deterministically retains the most recent execution.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import run_compare_service as svc  # noqa: E402


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


@pytest.mark.asyncio
async def test_load_test_rows_query_is_ordered():
    captured = {}

    class _FakeDB:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _ScalarsResult([])

    await svc._load_test_rows(_FakeDB(), uuid.uuid4())

    sql = captured["sql"].lower()
    assert "order by" in sql, "dedup query must be ordered for a deterministic winner"
    assert "created_at" in sql, "dedup must order by created_at (most recent wins)"


@pytest.mark.asyncio
async def test_load_test_rows_keeps_most_recent_retry():
    """With rows returned created_at-ascending, the keep-last loop must retain
    the newest execution per fingerprint."""
    old_tc = SimpleNamespace(test_fingerprint="fp1", status="FAILED", duration_ms=10)
    new_tc = SimpleNamespace(test_fingerprint="fp1", status="PASSED", duration_ms=12)

    class _FakeDB:
        async def execute(self, stmt):
            # DB returns ascending by created_at after the ORDER BY.
            return _ScalarsResult([old_tc, new_tc])

    rows = await svc._load_test_rows(_FakeDB(), uuid.uuid4())

    assert set(rows) == {"fp1"}
    assert rows["fp1"] is new_tc, "the most recent retry must win the dedup"
