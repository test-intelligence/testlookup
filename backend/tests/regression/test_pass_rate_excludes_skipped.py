"""Regression: canonical pass_rate must EXCLUDE skipped from the denominator.

Bug (2026-05-30 multi-pass review, `live-stream-ingestion`): the persisted
pass_rate authority — ``ingestion._update_run_aggregates`` (called by
finalize_run for BOTH file and live ingestion) — divided ``passed / total``
where total INCLUDED skipped. The live-stream transient displays
(live_consumer / stream_service) divided by ``passed+failed+broken`` (skipped
EXCLUDED), so a live run's pass_rate visibly shifted the moment it finalized,
and a run that was mostly skips showed a misleadingly low pass rate.

Fix: ``_update_run_aggregates`` now divides by passed+failed+broken (skipped
excluded), matching the live paths and the FAILED-iff-failed+broken status
rule. This file pins that semantics.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

# Importing app.services.ingestion pulls testng_parser → defusedxml (a declared
# dep). Skip cleanly where it's not installed locally; runs in CI/Docker.
pytest.importorskip("defusedxml")


class _Result:
    """One stand-in for all three read results _update_run_aggregates uses."""

    def __init__(self, counts):
        self._counts = counts

    def one(self):
        return self._counts

    def all(self):
        return []                      # no suite rows

    def scalar_one_or_none(self):
        return None                    # no existing primary_suite_name


class _CapturingDB:
    def __init__(self, counts):
        self._counts = counts
        self.calls = 0
        self.update_stmt = None

    async def execute(self, stmt):
        self.calls += 1
        # Call order in _update_run_aggregates: 1=aggregate, 2=suite_q,
        # 3=existing_psn, 4=UPDATE.
        if self.calls == 4:
            self.update_stmt = stmt
        return _Result(self._counts)


@pytest.mark.asyncio
async def test_pass_rate_excludes_skipped_from_denominator():
    from sqlalchemy.dialects import postgresql
    from app.services import ingestion as ing

    # 8 passed, 2 failed, 0 broken, 90 skipped → total 100.
    # Excluding skipped: 8 / (8+2+0) = 80.0.  Including skipped (old bug): 8.0.
    counts = SimpleNamespace(total=100, passed=8, failed=2, skipped=90, broken=0)
    db = _CapturingDB(counts)

    await ing._update_run_aggregates(db, uuid.uuid4())

    assert db.update_stmt is not None, "expected an UPDATE on test_runs"
    params = db.update_stmt.compile(dialect=postgresql.dialect()).params
    assert params["pass_rate"] == 80.0, (
        f"pass_rate must exclude skipped (expected 80.0, got {params['pass_rate']}). "
        "A regression here means file vs live ingestion disagree on pass_rate again."
    )


@pytest.mark.asyncio
async def test_pass_rate_is_100_when_all_executed_pass_despite_skips():
    from sqlalchemy.dialects import postgresql
    from app.services import ingestion as ing

    # 5 passed, 0 failed, 0 broken, 5 skipped. "Of the tests that ran, all
    # passed" → 100.0, not 50.0.
    counts = SimpleNamespace(total=10, passed=5, failed=0, skipped=5, broken=0)
    db = _CapturingDB(counts)

    await ing._update_run_aggregates(db, uuid.uuid4())

    params = db.update_stmt.compile(dialect=postgresql.dialect()).params
    assert params["pass_rate"] == 100.0
