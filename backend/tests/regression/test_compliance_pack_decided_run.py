"""Regression: compliance pack picked the latest linked run, not the latest
*decided* one.

Bug pinned (review/compliance-pack-service, 2026-06-01):

``_gather_decision_and_run`` selected the newest ``TestRun`` linked to the
release and only afterward looked up its ``ReleaseDecision``. A newer linked
run that hadn't been through the gate yet (decision is None) masked an older,
decided run — so a release that was decided and then re-run could no longer
produce a compliance pack. Fix: join ``ReleaseDecision`` into the query so only
decided runs are eligible, newest-first.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import compliance_pack_service as svc  # noqa: E402


@pytest.mark.asyncio
async def test_gather_decision_and_run_query_joins_release_decision():
    captured = {}

    class _Res:
        def first(self):
            return None

    class _DB:
        async def execute(self, stmt):
            captured["sql"] = " ".join(str(stmt).split())
            return _Res()

    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    decision, run = await svc._gather_decision_and_run(_DB(), release)

    # No decided run → (None, None), and only ONE query (joined, not the old
    # run-then-decision two-step).
    assert (decision, run) == (None, None)
    sql = captured["sql"]
    assert "release_test_run_links" in sql, "must join the release↔run link table"
    assert "release_decisions" in sql, (
        "must join ReleaseDecision so an undecided newer run can't mask an "
        "older decided run"
    )


@pytest.mark.asyncio
async def test_gather_decision_and_run_returns_decided_pair():
    """When a decided run exists, the joined query yields (decision, run)."""
    decision = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(id=uuid.uuid4())

    class _Res:
        def first(self):
            # The joined SELECT(ReleaseDecision, TestRun) yields this order.
            return (decision, run)

    class _DB:
        async def execute(self, stmt):
            return _Res()

    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    got_decision, got_run = await svc._gather_decision_and_run(_DB(), release)
    assert got_decision is decision
    assert got_run is run
