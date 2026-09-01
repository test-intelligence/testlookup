"""Transaction-boundary tests for governance metrics.

The lifecycle services stage counters; only the request-session owner may emit
them after commit.  These tests use a real ``info`` dict like AsyncSession and
do not treat an AsyncMock fallback as evidence of production semantics.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as core_metrics
from app.db import postgres
from app.models.postgres import TestCaseLifecycleState as LifecycleState
from app.services import test_management_metrics_service as metrics


def _session():
    return SimpleNamespace(info={})


@pytest.mark.asyncio
async def test_counter_is_queued_until_post_commit_emission(monkeypatch):
    db = _session()
    emit = Mock()
    monkeypatch.setattr(metrics, "_emit_counter", emit)

    metrics.stage_test_management_counter(
        db,
        "transition",
        ("project-1", "draft", "review_requested", "QA_ENGINEER"),
    )

    emit.assert_not_called()
    await metrics.emit_staged_test_management_metrics(db)
    emit.assert_called_once_with(
        "transition",
        ("project-1", "draft", "review_requested", "QA_ENGINEER"),
    )
    assert db.info == {}


@pytest.mark.asyncio
async def test_rollback_discards_staged_counter_without_emitting(monkeypatch):
    db = _session()
    emit = Mock()
    monkeypatch.setattr(metrics, "_emit_counter", emit)
    metrics.stage_test_management_counter(db, "promotion", ("project-1",))

    metrics.discard_staged_test_management_metrics(db)
    await metrics.emit_staged_test_management_metrics(db)

    emit.assert_not_called()
    assert db.info == {}


@pytest.mark.asyncio
async def test_post_commit_metric_failure_is_best_effort_and_queue_is_drained(monkeypatch):
    db = _session()
    emit = Mock(side_effect=RuntimeError("registry unavailable"))
    monkeypatch.setattr(metrics, "_emit_counter", emit)
    metrics.stage_test_management_counter(db, "promotion", ("project-1",))

    # Telemetry must never turn a committed governance mutation into a 500.
    await metrics.emit_staged_test_management_metrics(db)

    emit.assert_called_once()
    assert db.info == {}


@pytest.mark.asyncio
async def test_state_gauge_sets_every_bounded_state_including_zero(monkeypatch):
    project_id = __import__("uuid").uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [("active", 2), ("draft", 1)])
        )
    )
    values = {}
    gauge = Mock()

    def _labels(project, state):
        child = Mock()
        child.set.side_effect = lambda value: values.__setitem__((project, state), value)
        return child

    gauge.labels.side_effect = _labels
    monkeypatch.setattr(core_metrics, "test_cases_by_state", gauge)

    await metrics._refresh_state_gauge(db, project_id)

    assert len(values) == len(LifecycleState)
    assert values[(str(project_id), "active")] == 2
    assert values[(str(project_id), "draft")] == 1
    assert values[(str(project_id), "archived")] == 0


@pytest.mark.asyncio
async def test_post_commit_refreshes_state_and_orphan_gauges_once(monkeypatch):
    project_id = __import__("uuid").uuid4()
    db = _session()
    state_refresh = AsyncMock()
    orphan_refresh = AsyncMock()
    monkeypatch.setattr(metrics, "_refresh_state_gauge", state_refresh)
    monkeypatch.setattr(metrics, "_refresh_orphan_gauge", orphan_refresh)

    metrics.stage_test_case_state_refresh(db, project_id)
    metrics.stage_test_case_state_refresh(db, project_id)
    metrics.stage_orphan_gauge_refresh(db, project_id)
    await metrics.emit_staged_test_management_metrics(db)

    state_refresh.assert_awaited_once_with(db, project_id)
    orphan_refresh.assert_awaited_once_with(db, project_id)
    assert db.info == {}


@pytest.mark.asyncio
async def test_rollback_discards_staged_gauge_refreshes(monkeypatch):
    project_id = __import__("uuid").uuid4()
    db = _session()
    state_refresh = AsyncMock()
    orphan_refresh = AsyncMock()
    monkeypatch.setattr(metrics, "_refresh_state_gauge", state_refresh)
    monkeypatch.setattr(metrics, "_refresh_orphan_gauge", orphan_refresh)
    metrics.stage_test_case_state_refresh(db, project_id)
    metrics.stage_orphan_gauge_refresh(db, project_id)

    metrics.discard_staged_test_management_metrics(db)
    await metrics.emit_staged_test_management_metrics(db)

    state_refresh.assert_not_awaited()
    orphan_refresh.assert_not_awaited()
    assert db.info == {}


@pytest.mark.asyncio
async def test_orphan_gauge_refresh_explicitly_resets_zero(monkeypatch):
    project_id = __import__("uuid").uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar=lambda: 0)
        )
    )
    gauge = Mock()
    monkeypatch.setattr(core_metrics, "automation_cases_orphaned", gauge)

    await metrics._refresh_orphan_gauge(db, project_id)

    gauge.labels.assert_called_once_with(str(project_id))
    gauge.labels.return_value.set.assert_called_once_with(0)


def test_test_double_without_session_info_uses_documented_immediate_fallback(
    monkeypatch,
):
    db = SimpleNamespace()
    emit = Mock()
    monkeypatch.setattr(metrics, "_emit_counter", emit)

    metrics.stage_test_management_counter(
        db, "deprecation_without_reason", ("project-1",)
    )

    emit.assert_called_once_with(
        "deprecation_without_reason", ("project-1",)
    )


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_failed_gauge_read_is_isolated_from_committed_request_session(
    monkeypatch,
):
    project_id = __import__("uuid").uuid4()
    request_db = AsyncSession()
    isolated_db = SimpleNamespace()
    state_refresh = AsyncMock(side_effect=RuntimeError("read transaction failed"))
    monkeypatch.setattr(metrics, "_refresh_state_gauge", state_refresh)
    monkeypatch.setattr(
        postgres,
        "get_session_factory",
        lambda: lambda: _SessionContext(isolated_db),
    )
    metrics.stage_test_case_state_refresh(request_db, project_id)

    try:
        # A telemetry failure after commit must be swallowed and must never
        # open or abort a transaction on the response-producing session.
        await metrics.emit_staged_test_management_metrics(request_db)

        state_refresh.assert_awaited_once_with(isolated_db, project_id)
        assert request_db.info == {}
        assert request_db.in_transaction() is False
    finally:
        await request_db.close()


@pytest.mark.asyncio
async def test_request_session_emits_only_after_successful_commit(monkeypatch):
    calls: list[str] = []
    session = SimpleNamespace(
        info={},
        commit=AsyncMock(side_effect=lambda: calls.append("commit")),
        rollback=AsyncMock(side_effect=lambda: calls.append("rollback")),
        close=AsyncMock(side_effect=lambda: calls.append("close")),
    )
    monkeypatch.setattr(
        postgres,
        "get_session_factory",
        lambda: lambda: _SessionContext(session),
    )
    monkeypatch.setattr(
        metrics,
        "emit_staged_test_management_metrics",
        AsyncMock(side_effect=lambda _db: calls.append("emit")),
    )
    monkeypatch.setattr(
        metrics,
        "discard_staged_test_management_metrics",
        lambda _db: calls.append("discard"),
    )

    dependency = postgres.get_db()
    assert await dependency.__anext__() is session
    with pytest.raises(StopAsyncIteration):
        await dependency.__anext__()

    assert calls == ["commit", "emit", "close"]


@pytest.mark.asyncio
async def test_request_session_rollback_discards_without_emission(monkeypatch):
    calls: list[str] = []
    session = SimpleNamespace(
        info={},
        commit=AsyncMock(side_effect=lambda: calls.append("commit")),
        rollback=AsyncMock(side_effect=lambda: calls.append("rollback")),
        close=AsyncMock(side_effect=lambda: calls.append("close")),
    )
    monkeypatch.setattr(
        postgres,
        "get_session_factory",
        lambda: lambda: _SessionContext(session),
    )
    monkeypatch.setattr(
        metrics,
        "emit_staged_test_management_metrics",
        lambda _db: calls.append("emit"),
    )
    monkeypatch.setattr(
        metrics,
        "discard_staged_test_management_metrics",
        lambda _db: calls.append("discard"),
    )

    dependency = postgres.get_db()
    assert await dependency.__anext__() is session
    with pytest.raises(RuntimeError, match="request failed"):
        await dependency.athrow(RuntimeError("request failed"))

    assert calls == ["rollback", "discard", "close"]
