"""Serving a stale snapshot must schedule the refresh it promises.

The GET handler's own comment called the stale path "serves immediately while
refresh is recommended". Nothing recommended it and nothing ran it:

* ``get_stale_snapshot`` filters on ``schema_version``, NOT on ``stale``, so it
  happily returns the very row ``get_cached_snapshot`` just refused;
* the handler ``return``s that row *before* the live recompute below it;
* ``save_snapshot`` is the only writer that resets ``stale = False``, and it
  sits after that return, so it was unreachable on this path;
* there is no TTL.

So the flag, once set, was permanent. ``mark_stale`` is called when a QA Lead
overrides a release verdict (``release_readiness.py``) and when a defect is
promoted — meaning a committed ``GO -> NO_GO`` override left
``GET /runs/{id}/intelligence`` answering ``GO`` forever.

The fix keeps serving stale (that is a deliberate latency trade) and schedules
the recompute that makes it a trade rather than a permanent answer.
"""
from __future__ import annotations

import uuid

import pytest
from types import SimpleNamespace

from fastapi import BackgroundTasks
from fastapi.responses import Response

from app.routers import run_intelligence as ri

RUN_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _clear_refresh_state():
    """``_REFRESH_IN_FLIGHT`` is module-level mutable state.

    Only these tests monkeypatch it today; any future test that reaches the
    stale path would otherwise leave a key behind and silently suppress the
    refresh in whatever ran next.
    """
    ri._REFRESH_IN_FLIGHT.clear()
    yield
    ri._REFRESH_IN_FLIGHT.clear()


@pytest.mark.asyncio
async def test_the_stale_path_schedules_a_refresh(monkeypatch):
    monkeypatch.setattr(ri, "get_cached_snapshot", lambda db, run_id: _none())
    monkeypatch.setattr(ri, "get_stale_snapshot", lambda db, run_id: _payload())
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)

    async def _with_review(db, response, run_id, payload):
        return payload

    monkeypatch.setattr(ri, "_with_review", _with_review)

    background = BackgroundTasks()
    result = await ri.get_run_intelligence_endpoint(
        run_id=RUN_ID,
        response=Response(),
        background=background,
        include="",
        report_version=None,
        db=None,
        _=None,
    )

    assert result["_snapshot"]["stale"] is True
    assert result["_snapshot"]["refresh_requested"] is True, (
        "the response says nothing about a refresh; a consumer cannot tell a "
        "stale-but-converging answer from a stale-forever one"
    )
    assert "refresh_scheduled" not in result["_snapshot"], (
        "'scheduled' over-promises: the field is set before the task runs, and "
        "the task can short-circuit, raise, or die with the worker"
    )
    scheduled = [t.func for t in background.tasks]
    assert ri._refresh_snapshot_after_response in scheduled, (
        "serving stale without scheduling a refresh makes the flag permanent"
    )


@pytest.mark.asyncio
async def test_the_refresh_writes_a_snapshot_which_is_what_clears_stale(monkeypatch):
    saved = {}

    async def _fake_get(run_id, db, mongo):
        return {"x": 1, "provenance": {"fallback_used": True}}

    async def _fake_save(db, run_id, payload, fallback_used=False):
        saved["run_id"] = run_id
        saved["fallback_used"] = fallback_used

    monkeypatch.setattr(ri, "get_run_intelligence", _fake_get)
    monkeypatch.setattr(ri, "save_snapshot", _fake_save)
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)
    monkeypatch.setattr(ri, "_REFRESH_IN_FLIGHT", set())

    await ri._refresh_snapshot_after_response(RUN_ID)

    assert saved["run_id"] == RUN_ID
    # The fallback flag must survive; a refresh that silently downgrades
    # provenance would be worse than the staleness it fixes.
    assert saved["fallback_used"] is True


@pytest.mark.asyncio
async def test_a_failing_refresh_never_escapes(monkeypatch):
    # It runs after the response is sent. Raising there cannot help anyone and
    # would surface as an unhandled task error.
    async def _boom(run_id, db, mongo):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(ri, "get_run_intelligence", _boom)
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)
    monkeypatch.setattr(ri, "_REFRESH_IN_FLIGHT", set())

    await ri._refresh_snapshot_after_response(RUN_ID)  # must not raise


@pytest.mark.asyncio
async def test_a_second_request_does_not_pile_on_another_refresh(monkeypatch):
    # Every caller is served the same stale row until the refresh lands, so a
    # popular run would otherwise schedule one recompute per request and race
    # them all onto the same row.
    calls = []

    async def _slow(run_id, db, mongo):
        calls.append(run_id)
        return {"provenance": {}}

    async def _save(db, run_id, payload, fallback_used=False):
        return None

    monkeypatch.setattr(ri, "get_run_intelligence", _slow)
    monkeypatch.setattr(ri, "save_snapshot", _save)
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)
    monkeypatch.setattr(ri, "_REFRESH_IN_FLIGHT", {str(RUN_ID)})

    await ri._refresh_snapshot_after_response(RUN_ID)
    assert calls == [], "a refresh already in flight was duplicated"


@pytest.mark.asyncio
async def test_the_in_flight_guard_releases_so_later_refreshes_still_run(monkeypatch):
    """The de-duplication must not become a permanent lock.

    Caught by mutation: replacing the ``finally: discard(key)`` with ``pass``
    left every other test green. A leaked key means the FIRST refresh lands and
    every later one for that run is skipped forever — which is the original
    stale-forever defect again, one layer down and harder to see.
    """
    calls = []

    async def _get(run_id, db, mongo):
        calls.append(run_id)
        return {"provenance": {}}

    async def _save(db, run_id, payload, fallback_used=False):
        return None

    monkeypatch.setattr(ri, "get_run_intelligence", _get)
    monkeypatch.setattr(ri, "save_snapshot", _save)
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)
    monkeypatch.setattr(ri, "_REFRESH_IN_FLIGHT", set())

    await ri._refresh_snapshot_after_response(RUN_ID)
    await ri._refresh_snapshot_after_response(RUN_ID)

    assert len(calls) == 2, "the in-flight key leaked; later refreshes are skipped forever"
    assert ri._REFRESH_IN_FLIGHT == set(), "the key was not released"


@pytest.mark.asyncio
async def test_the_key_is_released_even_when_the_refresh_fails(monkeypatch):
    # A failing refresh that leaks its key would wedge that run permanently.
    async def _boom(run_id, db, mongo):
        raise RuntimeError("boom")

    monkeypatch.setattr(ri, "get_run_intelligence", _boom)
    monkeypatch.setattr(ri, "get_mongo_db", lambda: None)
    monkeypatch.setattr(ri, "_REFRESH_IN_FLIGHT", set())

    await ri._refresh_snapshot_after_response(RUN_ID)
    assert ri._REFRESH_IN_FLIGHT == set()


async def _none():
    return None


async def _payload():
    return {"summary": "stale content"}


class TestAReleaseOverrideLeavesNothingServable:
    """A human GO -> NO_GO must not be servable as GO even once.

    Marking the snapshot stale keeps it servable while a refresh converges,
    which is a fair trade for automatic staleness (defect promotion) and the
    wrong one for a release gate. The override path deletes instead, so the
    next read has no cached and no stale row and must recompute.
    """

    def test_the_override_path_deletes_rather_than_marks_stale(self):
        import inspect

        from app.routers import release_readiness

        src = inspect.getsource(release_readiness)
        assert "stage_invalidate" in src
        assert "import mark_stale" not in src, (
            "the override still marks the snapshot stale, leaving the "
            "superseded verdict servable"
        )

    @pytest.mark.asyncio
    async def test_stage_invalidate_does_not_commit(self):
        # It must land in the SAME transaction as the override; committing here
        # would let a rolled-back override drop a valid snapshot.
        from app.services import intelligence_snapshot_service as svc

        calls = []

        class _DB:
            async def execute(self, _stmt):
                calls.append("execute")
                return SimpleNamespace(rowcount=1)

            async def commit(self):
                calls.append("commit")

        assert await svc.stage_invalidate(_DB(), RUN_ID) is True
        assert "commit" not in calls, "stage_invalidate must leave the commit to its caller"

