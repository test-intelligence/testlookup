"""Transaction-boundary tests for governance metrics.

The lifecycle services stage counters; only the request-session owner may emit
them after commit.  These tests use a real ``info`` dict like AsyncSession and
do not treat an AsyncMock fallback as evidence of production semantics.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import metrics as core_metrics
from app.db import postgres
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
async def test_many_project_lifecycles_share_bounded_counters_without_sql_refresh(monkeypatch):
    from prometheus_client import CollectorRegistry, Counter, generate_latest

    registry = CollectorRegistry()
    transitions = Counter("testlookup_test_case_transitions_total", "test", ["from", "to", "actor_role"], registry=registry)
    promotions = Counter("testlookup_test_case_promotions_total", "test", registry=registry)
    deprecations = Counter("testlookup_test_case_deprecations_without_reason_total", "test", registry=registry)
    monkeypatch.setattr(core_metrics, "test_case_transitions_total", transitions)
    monkeypatch.setattr(core_metrics, "test_case_promotions_total", promotions)
    monkeypatch.setattr(core_metrics, "test_case_deprecations_without_reason_total", deprecations)
    db = SimpleNamespace(info={}, execute=AsyncMock(side_effect=AssertionError("metrics must not read SQL")))
    # Project create/delete/recreate cycles must change volume, never series.
    for generation in range(2):
        for project in range(1000):
            project_id = f"project-{generation}-{project}"
            metrics.stage_test_management_counter(db, "transition", (project_id, "draft", "active", "QA_ENGINEER"))
            metrics.stage_test_management_counter(db, "promotion", (project_id,))
            metrics.stage_test_management_counter(db, "deprecation_without_reason", (project_id,))
        await metrics.emit_staged_test_management_metrics(db)

    assert transitions.labels("draft", "active", "QA_ENGINEER")._value.get() == 2000
    assert promotions._value.get() == deprecations._value.get() == 2000
    samples = [sample for family in registry.collect() for sample in family.samples]
    assert len(samples) == 6  # total + created for three counter children
    assert b"project-" not in generate_latest(registry)
    assert db.info == {}
    db.execute.assert_not_awaited()


def test_unexpected_transition_dimensions_collapse_without_losing_event_count(monkeypatch):
    counter = Mock()
    monkeypatch.setattr(core_metrics, "test_case_transitions_total", counter)
    for index in range(1000):
        metrics._emit_counter("transition", (f"project-{index}", f"old-{index}", f"new-{index}", f"role-{index}"))
    assert counter.labels.call_count == 1000
    assert {call.args for call in counter.labels.call_args_list} == {("other", "other", "other")}


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
