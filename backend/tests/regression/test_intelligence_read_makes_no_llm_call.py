"""A cold run-intelligence read answers without the LLM defect build.

``GET /runs/{id}/intelligence`` on a cold cache built the defect candidates in
the request: the request session held its connection while a second session
made up to five LLM calls, each taking a third for its cost-cap check. With
the production pool of three connections per worker, three runs opened at once
held them all; every other request waited out the 30 s pool timeout, and the
token revocation check -- the first thing to need a connection -- answered 503
"revocation store unavailable" (homelab, 2026-10-13).

Now the read answers with the heuristic candidates, marks them pending, and
the build runs in the background: once per run, one at a time per worker, and
not again within the retry window after an attempt that persisted nothing.
A pending payload is never cached.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import intelligence_snapshot_service as snapshots
from app.services import run_intelligence_service as svc

CLUSTERS = [
    {"cluster_id": f"cl_{i}", "label": f"cluster {i}", "criticality_level": "HIGH", "member_test_ids": []}
    for i in range(7)
]


@pytest.fixture(autouse=True)
def _fresh_build_state(monkeypatch):
    # raising=False: on code without the background build these tests must
    # fail on behaviour, not error in the fixture.
    monkeypatch.setattr(svc, "_DEFECT_BUILD_SLOT", asyncio.Semaphore(1), raising=False)
    monkeypatch.setattr(svc, "_DEFECT_BUILD_IN_FLIGHT", set(), raising=False)
    monkeypatch.setattr(svc, "_DEFECT_BUILD_FINISHED_AT", {}, raising=False)
    monkeypatch.setattr(svc, "_DEFECT_BUILD_TASKS", set(), raising=False)


def _db(persisted=()):
    rows = MagicMock()
    rows.all = MagicMock(return_value=list(persisted))
    result = MagicMock()
    result.scalars = MagicMock(return_value=rows)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


class _Build:
    """Stands in for the LLM build: blocks until released, records concurrency."""

    def __init__(self, persisted=True):
        self.release = asyncio.Event()
        self.calls: list[tuple[uuid.UUID, list[dict]]] = []
        self.active = 0
        self.max_active = 0
        self.persisted = persisted

    async def __call__(self, run_id, failure_clusters):
        self.calls.append((run_id, failure_clusters))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return [{"cluster_id": "cl_0"}] if self.persisted else []


async def _read(run_id, db=None):
    """One cold read, bounded: a read that waits on the build fails, not hangs."""
    return await asyncio.wait_for(
        svc._load_or_build_defect_candidates(run_id, CLUSTERS, [], db or _db()), timeout=2,
    )


async def _drain():
    while svc._DEFECT_BUILD_TASKS:
        await asyncio.wait_for(asyncio.gather(*list(svc._DEFECT_BUILD_TASKS)), timeout=2)


@pytest.mark.asyncio
async def test_a_cold_read_answers_before_the_llm_build_runs():
    build = _Build()
    run_id = uuid.uuid4()
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        candidates, pending = await _read(run_id)
        assert pending is True
        assert [c["cluster_id"] for c in candidates] == [f"cl_{i}" for i in range(5)]
        await asyncio.sleep(0)
        assert build.calls == [(run_id, CLUSTERS[:5])]
        assert build.active == 1, "the build is still running after the read answered"
        build.release.set()
        await _drain()
    assert str(run_id) not in svc._DEFECT_BUILD_IN_FLIGHT


@pytest.mark.asyncio
async def test_one_build_per_run_while_it_is_in_flight():
    build = _Build()
    run_id = uuid.uuid4()
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        for _ in range(3):
            _, pending = await _read(run_id)
            assert pending is True
        await asyncio.sleep(0)
        build.release.set()
        await _drain()
    assert len(build.calls) == 1


@pytest.mark.asyncio
async def test_builds_run_one_at_a_time_per_worker():
    build = _Build()
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        for _ in range(3):
            await _read(uuid.uuid4())
        for _ in range(5):
            await asyncio.sleep(0)
        assert build.active == 1
        build.release.set()
        await _drain()
    assert len(build.calls) == 3
    assert build.max_active == 1


@pytest.mark.asyncio
async def test_an_attempt_that_persisted_nothing_is_not_retried_at_once():
    build = _Build(persisted=False)
    build.release.set()
    run_id = uuid.uuid4()
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        await _read(run_id)
        await _drain()
        candidates, pending = await _read(run_id)
    assert pending is False, "the heuristic is the answer until the retry window passes"
    assert len(candidates) == 5
    assert len(build.calls) == 1


@pytest.mark.asyncio
async def test_a_build_that_raises_frees_the_run():
    run_id = uuid.uuid4()
    with patch.object(svc, "_build_and_persist_defect_candidates", AsyncMock(side_effect=RuntimeError("llm down"))):
        _, pending = await _read(run_id)
        await _drain()
    assert pending is True
    assert str(run_id) not in svc._DEFECT_BUILD_IN_FLIGHT


@pytest.mark.asyncio
async def test_persisted_candidates_are_the_answer_and_nothing_is_built():
    build = _Build()
    row = SimpleNamespace(
        cluster_id="cl_0", title="Checkout fails", severity="HIGH", failure_category="PRODUCT",
        composite_score=0.8, status="pending", is_duplicate=False, duplicate_of=None,
        promoted_defect_id=None, evidence_bundle={},
    )
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        candidates, pending = await _read(uuid.uuid4(), _db([row]))
    assert pending is False
    assert [c["label"] for c in candidates] == ["Checkout fails"]
    assert build.calls == []


@pytest.mark.asyncio
async def test_a_failed_read_builds_nothing():
    build = _Build()
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("pool timeout")))
    with patch.object(svc, "_build_and_persist_defect_candidates", build):
        candidates, pending = await _read(uuid.uuid4(), db)
    assert pending is False
    assert len(candidates) == 5
    assert build.calls == []


@pytest.mark.asyncio
async def test_the_payload_reports_pending_candidates():
    from tests.regression.test_run_intelligence_session_concurrency import (
        _FakeMongo,
        _GuardedSession,
        _make_run,
        _result,
    )

    run = _make_run()
    heuristic = [{"cluster_id": "cl_0"}]
    with patch.object(svc, "get_baseline_diff", AsyncMock(return_value=None)), \
         patch.object(svc, "_load_or_build_defect_candidates", AsyncMock(return_value=(heuristic, True))):
        result = await svc.get_run_intelligence(run.id, _GuardedSession([_result(scalar=run)]), _FakeMongo())
    assert result["defect_candidates"] == heuristic
    assert result["defect_candidates_pending"] is True


@pytest.mark.asyncio
async def test_a_pending_payload_is_not_cached():
    db = SimpleNamespace(execute=AsyncMock(), add=MagicMock(), commit=AsyncMock())
    await snapshots.save_snapshot(db, uuid.uuid4(), {"defect_candidates": [], "defect_candidates_pending": True})
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
