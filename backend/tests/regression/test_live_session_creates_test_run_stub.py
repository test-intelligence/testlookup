"""Regression: ``/runs`` Pipeline-signal KPI was frozen on historical
data even after new live runs were ingested.

Bug pinned (2026-05-19): user reported the Pipeline-signal KPI on
``/runs`` always displayed "Mixed · 43 of 50 builds failed" no matter
how many new live runs they added. Root cause: ``GET /api/v1/runs``
only queries ``test_runs``. The companion ``test_runs`` row for a
live session was only written by the Phase 4.5 drainer (on first
non-empty drain, ~30s later) or by ``persist_live_session`` on
session-complete. Short live runs that finished inside the 30s
drainer window — and runs whose buffer never received events because
the SDK sent them at the end in one batch — never produced a row
in ``test_runs`` at all.

Fix: ``stream_service.create_session`` now writes a companion
TestRun stub at session-create time, status=IN_PROGRESS, wrapped in
a SAVEPOINT so a duplicate-key race with the drainer is harmless.

What this file pins:

  * ``create_session`` adds a TestRun row with the same id as the
    LiveSession.
  * The stub carries the run-level metadata that ``/runs`` and the
    Pipeline-signal need: ``project_id``, ``primary_suite_name``,
    ``branch``, ``trigger_source="live_stream"``, and
    ``status=IN_PROGRESS``.
  * Aggregates start at zero — the drainer and persist task own the
    real counts.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_session_writes_a_test_run_stub():
    """The companion TestRun row must exist BEFORE the drainer fires so
    ``GET /api/v1/runs`` and its Pipeline-signal KPI include the run."""
    from app.services.stream_service import create_session
    from app.models.postgres import LaunchStatus, TestRun

    added_rows: list = []

    db = AsyncMock()
    db.add = MagicMock(side_effect=added_rows.append)
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()
    db.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), name="GoogleProject")

    payload = SimpleNamespace(
        project_id=str(project.id),
        client_name="java-sdk",
        machine_id="ci-1",
        build_number="b-2029",
        framework="testng",
        branch="main",
        commit_hash=None,
        total_tests=10,
        release_name=None,
        launch_name="nightly",
        suite_name="Auth",
        metadata={},
    )

    fake_redis = AsyncMock()
    fake_redis.setex = AsyncMock()

    with patch("app.services.stream_service.resolve_project",
               AsyncMock(return_value=project)), \
         patch("app.services.stream_service.get_redis",
               return_value=fake_redis), \
         patch("app.streams.live_run_state.RedisLiveRunState.start",
               AsyncMock(return_value=None)):
        response = await create_session(db, payload)

    # One LiveSession + one TestRun stub.
    test_runs_added = [r for r in added_rows if isinstance(r, TestRun)]
    assert len(test_runs_added) == 1, (
        "create_session must write exactly one TestRun stub alongside "
        "the LiveSession so /runs sees the row from t=0."
    )
    stub = test_runs_added[0]
    assert stub.status == LaunchStatus.IN_PROGRESS
    assert stub.trigger_source == "live_stream"
    assert stub.project_id == project.id
    assert stub.primary_suite_name == "Auth"
    assert stub.branch == "main"
    assert stub.passed_tests == 0
    assert stub.failed_tests == 0
    # Both ids match: the SDK-facing run_id and the LiveSession.id and
    # the TestRun.id all collapse to one canonical UUID — see
    # ``stream_service.create_session`` where ``session_id == run_id``.
    assert str(stub.id) == response.run_id


@pytest.mark.asyncio
async def test_test_run_stub_save_failure_does_not_break_session_create():
    """SAVEPOINT contains a duplicate-key race so the LiveSession still
    commits even if the stub write trips an exception."""
    from app.services.stream_service import create_session
    from app.models.postgres import LaunchStatus, TestRun

    added_rows: list = []

    db = AsyncMock()
    db.add = MagicMock(side_effect=added_rows.append)
    db.flush = AsyncMock()
    # SAVEPOINT context manager — __aexit__ swallows the exception
    # (returning True) so the outer commit isn't poisoned. We model
    # the failure by having the SAVEPOINT block raise on enter.
    db.begin_nested = MagicMock()
    db.begin_nested.return_value.__aenter__ = AsyncMock(
        side_effect=Exception("duplicate key"),
    )
    db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), name="P")
    payload = SimpleNamespace(
        project_id=str(project.id),
        client_name=None, machine_id=None, build_number=None,
        framework=None, branch=None, commit_hash=None,
        total_tests=0, release_name=None, launch_name=None,
        suite_name=None, metadata=None,
    )
    fake_redis = AsyncMock()
    fake_redis.setex = AsyncMock()

    with patch("app.services.stream_service.resolve_project",
               AsyncMock(return_value=project)), \
         patch("app.services.stream_service.get_redis",
               return_value=fake_redis), \
         patch("app.streams.live_run_state.RedisLiveRunState.start",
               AsyncMock(return_value=None)):
        # Must NOT raise — the duplicate-key SAVEPOINT is swallowed.
        response = await create_session(db, payload)

    assert response.run_id  # session_id round-trip
