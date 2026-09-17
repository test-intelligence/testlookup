"""H12 regression tests for durable post-ingestion dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def test_input_version_is_stable_for_equivalent_payloads():
    from app.services.run_downstream_outbox import operation_input_version

    first = operation_input_version("agent_pipeline", {"b": 2, "a": 1})
    second = operation_input_version("agent_pipeline", {"a": 1, "b": 2})
    changed = operation_input_version("agent_pipeline", {"a": 1, "b": 3})
    assert first == second
    assert len(first) == 64
    assert changed != first


@pytest.mark.asyncio
async def test_all_durable_claims_rank_tenants_before_global_limit():
    """A backlog larger than the old candidate cap cannot hide a peer."""
    from sqlalchemy.dialects import postgresql

    from app.services.notification.manager import (
        claim_pending_notification_deliveries,
    )
    from app.services.run_downstream_outbox import (
        claim_downstream_dispatches,
        claim_waiting_finalizations,
    )
    from app.services.webhook_service import claim_pending_webhook_dispatches

    class _EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class _CaptureDB:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return _EmptyResult()

    expected = (
        (claim_downstream_dispatches, "run_downstream_outbox.project_id"),
        (claim_waiting_finalizations, "test_runs.project_id"),
        (claim_pending_notification_deliveries, "notification_logs.project_id"),
        (claim_pending_webhook_dispatches, "webhook_subscriptions.project_id"),
    )
    for claim, tenant_column in expected:
        db = _CaptureDB()
        await claim(db, limit=7)
        sql = str(db.statement.compile(dialect=postgresql.dialect()))
        assert f"PARTITION BY {tenant_column}" in sql
        assert "row_number() OVER" in sql
        assert "FOR UPDATE OF" in sql
        assert "SKIP LOCKED" in sql
        # The only LIMIT belongs to the outer lock/claim query. A LIMIT in
        # the ranked CTE would recreate noisy-tenant starvation.
        cte_sql, outer_sql = sql.rsplit("LIMIT", 1)
        assert "LIMIT" not in cte_sql
        assert outer_sql


@pytest.mark.asyncio
async def test_finalize_operations_are_versioned_and_ai_is_optional(monkeypatch):
    from app.services import run_downstream_outbox as service

    staged = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_downstream_operation", staged)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="build-42",
        pass_rate=75.0,
        total_tests=4,
        failed_tests=1,
        end_time=datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
    )
    project = SimpleNamespace(name="payments")

    await service.stage_finalize_operations(
        MagicMock(), run=run, project=project, run_ai=False
    )

    operations = [call.kwargs["operation"] for call in staged.await_args_list]
    assert operations == [
        "run_notifications",
        "transition_notifications",
        "run_completed_webhook",
    ]
    assert all(call.kwargs["input_version"] for call in staged.await_args_list)
    assert all(call.kwargs["initial_status"] == "waiting" for call in staged.await_args_list)
    webhook_version = next(
        call.kwargs["input_version"]
        for call in staged.await_args_list
        if call.kwargs["operation"] == "run_completed_webhook"
    )

    staged.reset_mock()
    run.end_time = datetime(2026, 9, 8, 1, 5, tzinfo=timezone.utc)
    await service.stage_finalize_operations(
        MagicMock(), run=run, project=project, run_ai=False
    )
    retried_webhook_version = next(
        call.kwargs["input_version"]
        for call in staged.await_args_list
        if call.kwargs["operation"] == "run_completed_webhook"
    )
    assert retried_webhook_version == webhook_version

    staged.reset_mock()
    await service.stage_finalize_operations(
        MagicMock(), run=run, project=project, run_ai=True
    )

    ai_operations = [call.kwargs["operation"] for call in staged.await_args_list]
    assert ai_operations == [
        "run_notifications",
        "transition_notifications",
        "suite_comparison",
        "run_completed_webhook",
        "agent_pipeline",
    ]


@pytest.mark.asyncio
async def test_live_persistence_payload_is_durable_and_versioned(monkeypatch):
    from app.services import run_downstream_outbox as service

    staged = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_downstream_operation", staged)
    run_id = uuid.uuid4()
    completed_at = datetime(2026, 9, 8, 12, 34, tzinfo=timezone.utc)
    session = SimpleNamespace(
        run_id="sdk-run-42",
        project_id=uuid.uuid4(),
        build_number="build-42",
        client_name="pytest",
        framework="pytest",
        branch="main",
        commit_hash="abc123",
        suite_name="unit",
        completed_at=completed_at,
    )
    state = {"passed": 3, "failed": 1}

    await service.stage_live_persist_operation(
        MagicMock(), canonical_run_id=run_id, session=session, final_state=state
    )
    await service.stage_live_persist_operation(
        MagicMock(),
        canonical_run_id=run_id,
        session=session,
        final_state={**state, "last_event_at": "later"},
    )

    first, second = staged.await_args_list
    kwargs = first.kwargs
    assert kwargs["run_id"] == run_id
    assert kwargs["operation"] == "persist_live_session"
    assert kwargs["payload"]["run_id"] == str(run_id)
    assert kwargs["payload"]["final_state"] == state
    assert kwargs["payload"]["completed_at"] == completed_at.isoformat()
    assert kwargs["queue"].startswith("ingestion")
    assert kwargs["input_version"]
    assert first.kwargs["input_version"] == second.kwargs["input_version"]


def test_live_persist_keeps_completion_order_when_workers_run_in_reverse():
    from app.worker.tasks import _live_run_completion_time

    run_a_completed = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    run_b_completed = run_a_completed + timedelta(minutes=1)

    # Broker recovery executes B first and the older A much later. Persisted
    # run order must still follow the session-close timestamps.
    persisted_b = _live_run_completion_time(
        run_b_completed.isoformat(),
        existing_end_time=None,
        worker_time=run_b_completed + timedelta(hours=1),
    )
    persisted_a = _live_run_completion_time(
        run_a_completed.isoformat(),
        existing_end_time=None,
        worker_time=run_b_completed + timedelta(hours=2),
    )

    assert persisted_a < persisted_b
    assert _live_run_completion_time(
        None,
        existing_end_time=run_a_completed,
        worker_time=run_b_completed + timedelta(hours=3),
    ) == run_a_completed


@pytest.mark.asyncio
async def test_broker_failure_keeps_outbox_retryable(monkeypatch):
    from app.services import run_downstream_outbox as service

    row = SimpleNamespace(
        id=uuid.uuid4(),
        operation="run_notifications",
        payload={"run_id": str(uuid.uuid4())},
        queue="default",
        priority=5,
        attempts=1,
        dispatch_failures=0,
        dispatch_token=uuid.uuid4(),
    )
    db = MagicMock()
    db.commit = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        service, "claim_downstream_dispatches", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        service, "_publish_downstream", MagicMock(side_effect=ConnectionError("down"))
    )
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "mark_downstream_dispatch_failed", mark_failed)

    result = await service.relay_downstream_outbox(limit=1, max_batches=1)

    assert result == {"claimed": 1, "published": 0, "failed": 1}
    assert "permanent" not in mark_failed.await_args.kwargs
    assert "ConnectionError" in mark_failed.await_args.kwargs["error_code"]


@pytest.mark.asyncio
async def test_relay_marks_acknowledged_publish_only_published(monkeypatch):
    from app.services import run_downstream_outbox as service

    row = SimpleNamespace(
        id=uuid.uuid4(),
        operation="transition_notifications",
        payload={"run_id": str(uuid.uuid4())},
        queue="default",
        priority=5,
        attempts=1,
        dispatch_token=uuid.uuid4(),
    )
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        service, "claim_downstream_dispatches", AsyncMock(return_value=[row])
    )
    publish = MagicMock()
    monkeypatch.setattr(service, "_publish_downstream", publish)
    mark_published = AsyncMock(return_value=True)
    monkeypatch.setattr(
        service, "mark_downstream_dispatch_published", mark_published
    )

    result = await service.relay_downstream_outbox(limit=1, max_batches=1)

    assert result == {"claimed": 1, "published": 1, "failed": 0}
    publish.assert_called_once_with(row)
    mark_published.assert_awaited_once_with(
        db, outbox_id=row.id, dispatch_token=row.dispatch_token
    )


def test_publisher_uses_whitelist_and_stable_task_id(monkeypatch):
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    apply_async = MagicMock()
    monkeypatch.setattr(tasks.dispatch_transition_notifications, "apply_async", apply_async)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        operation="transition_notifications",
        payload={"run_id": str(uuid.uuid4())},
        queue="default",
        priority=5,
        dispatch_token=uuid.uuid4(),
    )

    service._publish_downstream(row)

    apply_async.assert_called_once_with(
        kwargs=row.payload,
        queue="default",
        priority=5,
        task_id=f"run-downstream-{row.id}",
        headers={
            "downstream_outbox_id": str(row.id),
            "downstream_dispatch_token": str(row.dispatch_token),
        },
    )


@pytest.mark.asyncio
async def test_claim_exhaustion_is_terminal_without_republishing():
    from app.services.run_downstream_outbox import (
        MAX_DISPATCH_ATTEMPTS,
        claim_downstream_dispatches,
    )

    exhausted = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="pending",
        attempts=MAX_DISPATCH_ATTEMPTS,
        dispatch_failures=MAX_DISPATCH_ATTEMPTS,
        dispatch_token=uuid.uuid4(),
        lease_expires_at=None,
        next_attempt_at=None,
        last_error="broker_down",
    )
    due = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="pending",
        attempts=1,
        dispatch_failures=0,
        dispatch_token=None,
        lease_expires_at=None,
        next_attempt_at=None,
        last_error="old",
    )
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [exhausted, due]
    locked = MagicMock()
    locked.scalars.return_value.all.return_value = [exhausted, due]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[candidates, locked])

    claimed = await claim_downstream_dispatches(db)

    assert claimed == [due]
    assert exhausted.status == "failed"
    assert exhausted.next_attempt_at is None
    assert due.status == "sending"
    assert due.attempts == 2
    assert due.lease_expires_at is not None


@pytest.mark.asyncio
async def test_expired_accepted_publish_reuses_token_without_exhaustion():
    from app.services.run_downstream_outbox import (
        MAX_DISPATCH_ATTEMPTS,
        claim_downstream_dispatches,
    )

    token = uuid.uuid4()
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="published",
        attempts=MAX_DISPATCH_ATTEMPTS,
        dispatch_failures=0,
        dispatch_token=token,
        lease_expires_at=expired,
        next_attempt_at=expired,
        last_error=None,
        processing_task_id="old-task",
    )
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [row]
    locked = MagicMock()
    locked.scalars.return_value.all.return_value = [row]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[candidates, locked])

    claimed = await claim_downstream_dispatches(db, limit=1)

    assert claimed == [row]
    assert row.status == "sending"
    assert row.attempts == MAX_DISPATCH_ATTEMPTS + 1
    assert row.dispatch_failures == 0
    assert row.dispatch_token == token


@pytest.mark.asyncio
async def test_status_summary_surfaces_pending_and_safe_failure():
    from app.services.run_downstream_outbox import downstream_status_for_run

    rows = [
        SimpleNamespace(
            operation="agent_pipeline",
            status="pending",
            attempts=2,
            dispatch_failures=1,
            execution_attempts=1,
            last_error="broker_ConnectionError",
        ),
        SimpleNamespace(
            operation="run_notifications",
            status="completed",
            attempts=1,
            dispatch_failures=0,
            execution_attempts=1,
            last_error=None,
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    run_id = uuid.uuid4()

    summary = await downstream_status_for_run(db, run_id=run_id)

    assert summary["status"] == "pending"
    assert summary["operations"][0] == {
        "operation": "agent_pipeline",
        "status": "pending",
        "attempts": 2,
        "dispatch_failures": 1,
        "execution_attempts": 1,
        "last_error": "broker_ConnectionError",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notification_rows", "webhook_rows", "expected"),
    [
        ([('failed', 1)], [('PENDING', 2, 0)], "failed"),
        ([('pending', 2)], [('SUCCESS', 1, 0)], "pending"),
    ],
)
async def test_status_summary_includes_durable_child_delivery_outcomes(
    notification_rows,
    webhook_rows,
    expected,
):
    from app.services.run_downstream_outbox import downstream_status_for_run

    parent = SimpleNamespace(
        operation="run_completed_webhook",
        status="completed",
        attempts=1,
        dispatch_failures=0,
        execution_attempts=1,
        last_error=None,
    )
    outbox_result = MagicMock()
    outbox_result.scalars.return_value.all.return_value = [parent]
    notification_result = MagicMock()
    notification_result.all.return_value = notification_rows
    webhook_result = MagicMock()
    webhook_result.all.return_value = webhook_rows
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[outbox_result, notification_result, webhook_result]
    )
    run_id = uuid.uuid4()

    summary = await downstream_status_for_run(db, run_id=run_id)

    assert summary["status"] == expected
    assert summary["child_deliveries"]["notifications"][
        "failed" if expected == "failed" else "retrying"
    ] == 1
    webhook_statement = db.execute.await_args_list[2].args[0]
    compiled = webhook_statement.compile()
    assert "webhook_deliveries.delivery_key IS NOT NULL" in str(compiled)
    assert "webhook_deliveries.run_id" in str(compiled)
    assert "event_payload" not in str(compiled)
    assert str(run_id) in {str(value) for value in compiled.params.values()}


def test_outbox_schema_has_idempotency_and_retry_controls():
    from app.models.postgres import RunDownstreamOutbox

    columns = RunDownstreamOutbox.__table__.columns
    for name in (
        "run_id",
        "project_id",
        "operation",
        "input_version",
        "payload",
        "status",
        "attempts",
        "dispatch_failures",
        "execution_attempts",
        "next_attempt_at",
        "lease_expires_at",
        "dispatch_token",
        "published_at",
        "processing_started_at",
        "completed_at",
        "last_error",
    ):
        assert name in columns
    constraints = {constraint.name for constraint in RunDownstreamOutbox.__table__.constraints}
    assert "uq_run_downstream_operation_version" in constraints


def test_child_delivery_models_expose_the_migrated_lookup_indexes():
    from app.models.postgres import NotificationLog, WebhookDelivery

    notification_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in NotificationLog.__table__.indexes
    }
    webhook_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in WebhookDelivery.__table__.indexes
    }

    assert notification_indexes["ix_notification_log_delivery_due"] == (
        "status",
        "next_delivery_at",
    )
    assert "run_id" in WebhookDelivery.__table__.columns
    assert webhook_indexes["ix_webhook_delivery_run_id"] == ("run_id",)


def test_fair_admission_includes_small_projects_during_500_run_burst():
    from app.services.run_downstream_outbox import _fair_candidate_ids

    project_a, project_b, project_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [
        SimpleNamespace(id=uuid.uuid4(), project_id=project_a)
        for _ in range(500 * 4)
    ]
    b = SimpleNamespace(id=uuid.uuid4(), project_id=project_b)
    c = SimpleNamespace(id=uuid.uuid4(), project_id=project_c)
    rows.extend([b, c])

    selected = _fair_candidate_ids(rows, limit=200)

    assert len(selected) == 200
    assert b.id in selected
    assert c.id in selected


@pytest.mark.asyncio
async def test_one_relay_tick_can_publish_500_runs_worth_of_children(monkeypatch):
    from app.services import run_downstream_outbox as service

    batches = [
        [
            SimpleNamespace(
                id=uuid.uuid4(),
                operation="transition_notifications",
                payload={"run_id": str(uuid.uuid4())},
                queue="default",
                priority=5,
                attempts=1,
                dispatch_token=uuid.uuid4(),
            )
            for _ in range(200)
        ]
        for _ in range(10)
    ]
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        service,
        "claim_downstream_dispatches",
        AsyncMock(side_effect=batches),
    )
    monkeypatch.setattr(service, "_publish_downstream", MagicMock())
    monkeypatch.setattr(
        service,
        "mark_downstream_dispatch_published",
        AsyncMock(return_value=True),
    )

    result = await service.relay_downstream_outbox()

    assert result == {"claimed": 2000, "published": 2000, "failed": 0}


@pytest.mark.asyncio
async def test_execution_is_completed_only_after_consumer_success(monkeypatch):
    from app.services import run_downstream_outbox as service

    token = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="published",
        dispatch_token=token,
        execution_attempts=0,
        lease_expires_at=None,
        next_attempt_at=None,
        processing_task_id=None,
        processing_started_at=None,
        completed_at=None,
        completion_detail=None,
        last_error=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())

    assert await service.begin_downstream_execution(
        outbox_id=row.id,
        dispatch_token=token,
        task_id="worker-1",
    )
    assert row.status == "processing"
    assert row.completed_at is None

    assert await service.complete_downstream_execution(
        outbox_id=row.id,
        dispatch_token=token,
    )
    assert row.status == "completed"
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_stale_delivery_token_cannot_execute(monkeypatch):
    from app.services import run_downstream_outbox as service

    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="published",
        dispatch_token=uuid.uuid4(),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())

    assert not await service.begin_downstream_execution(
        outbox_id=row.id,
        dispatch_token=uuid.uuid4(),
        task_id="stale-worker",
    )
    assert row.status == "published"


def test_finalize_children_activate_after_required_post_processing():
    import inspect

    from app.services.ingestion_pipeline import finalize_run
    from app.services.run_downstream_outbox import stage_finalize_operations

    source = inspect.getsource(finalize_run)
    activation = source.index("_activate_finalize_children")
    assert source.index('"commit_range"') < activation
    assert "emit_event" not in source
    assert '"run_completed_webhook"' in inspect.getsource(stage_finalize_operations)


@pytest.mark.asyncio
async def test_finalize_child_activation_commit_failure_is_retryable(monkeypatch):
    from app.services import ingestion_pipeline, run_downstream_outbox

    run_id = uuid.uuid4()
    activation = AsyncMock(return_value=4)
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("database commit failed")),
        rollback=AsyncMock(),
    )

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(ingestion_pipeline, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(
        run_downstream_outbox,
        "activate_finalize_operations",
        activation,
    )

    with pytest.raises(RuntimeError, match="database commit failed"):
        await ingestion_pipeline._activate_finalize_children(run_id)

    activation.assert_awaited_once_with(db, run_id=run_id)
    db.rollback.assert_awaited_once()


def test_tracked_task_marks_completion_after_the_body(monkeypatch):
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    calls: list[str] = []
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    monkeypatch.setattr(
        service,
        "begin_downstream_execution",
        lambda **_kwargs: calls.append("begin") or True,
    )
    monkeypatch.setattr(
        service,
        "complete_downstream_execution",
        lambda **_kwargs: calls.append("complete") or True,
    )
    monkeypatch.setattr(
        service,
        "fail_downstream_execution",
        lambda **_kwargs: calls.append("fail") or True,
    )

    class ProbeTask(tasks.DownstreamTrackedTask):
        name = "tests.probe_downstream"

        def run(self):
            calls.append("body")
            return "ok"

    probe = ProbeTask()
    probe.bind(tasks.celery_app)
    probe.push_request(
        id="worker-1",
        headers={
            "downstream_outbox_id": str(uuid.uuid4()),
            "downstream_dispatch_token": str(uuid.uuid4()),
        },
    )
    try:
        assert probe() == "ok"
    finally:
        probe.pop_request()

    assert calls == ["begin", "body", "complete"]


def test_ordering_retry_returns_to_outbox_without_spending_execution_attempt(monkeypatch):
    from celery.exceptions import Retry

    from app.services import notification_transitions as transitions
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    calls: list[str] = []
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    monkeypatch.setattr(
        service,
        "begin_downstream_execution",
        lambda **_kwargs: calls.append("begin") or True,
    )
    monkeypatch.setattr(
        service,
        "complete_downstream_execution",
        lambda **_kwargs: calls.append("complete") or True,
    )
    monkeypatch.setattr(
        service,
        "fail_downstream_execution",
        lambda **_kwargs: calls.append("fail") or True,
    )
    monkeypatch.setattr(
        service,
        "defer_downstream_execution",
        lambda **_kwargs: calls.append("defer") or True,
    )

    class ProbeTask(tasks.DownstreamTrackedTask):
        name = "tests.probe_ordered_downstream"

        def run(self):
            raise Retry(
                exc=transitions.TransitionOrderPending(
                    "older_transition_run_pending"
                )
            )

    probe = ProbeTask()
    probe.bind(tasks.celery_app)
    probe.push_request(
        id="worker-order",
        headers={
            "downstream_outbox_id": str(uuid.uuid4()),
            "downstream_dispatch_token": str(uuid.uuid4()),
        },
    )
    try:
        assert probe()["deferred"] is True
    finally:
        probe.pop_request()

    assert calls == ["begin", "defer"]


@pytest.mark.asyncio
async def test_ordering_deferral_restores_the_consumed_execution_attempt(monkeypatch):
    from app.services import run_downstream_outbox as service

    token = uuid.uuid4()
    row = SimpleNamespace(
        status="processing",
        dispatch_token=token,
        execution_attempts=3,
        last_error=None,
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", _Session)

    assert await service.defer_downstream_execution(
        outbox_id=uuid.uuid4(),
        dispatch_token=token,
        reason="older_transition_run_pending",
    )
    assert row.status == "pending"
    assert row.execution_attempts == 2
    assert row.lease_expires_at is None
    assert row.next_attempt_at > datetime.now(timezone.utc)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoped_notification_stages_child_without_provider_io(monkeypatch):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    scope = f"run-outbox:{uuid.uuid4()}"
    pref = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        events=[NotificationEventType.RUN_FAILED.value],
        failure_rate_threshold=80.0,
    )
    preferences = MagicMock()
    preferences.all.return_value = [(pref, "qa@example.test")]
    inserted = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[preferences, inserted])
    db.commit = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    send = AsyncMock()
    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(manager, "_dispatch_to_channel", send)

    await manager._load_and_notify(
        pref.project_id,
        uuid.uuid4(),
        [NotificationEventType.RUN_FAILED],
        lambda _event: ("failed", "body"),
        {"pass_rate": 0.0},
        delivery_scope=scope,
    )

    send.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoped_notification_normalizes_string_channel_from_postgres():
    from app.models.postgres import NotificationEventType
    from app.services.notification import manager

    scope = f"run-outbox:{uuid.uuid4()}"
    user_id = uuid.uuid4()
    pref = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        channel="email",
    )
    db = MagicMock()
    db.execute = AsyncMock()

    await manager._insert_scoped_notification_plans(
        db,
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        delivery_scope=scope,
        metadata={"pass_rate": 0.0},
        plans=[
            (
                pref,
                "qa@example.test",
                NotificationEventType.RUN_FAILED,
                "failed",
                "body",
            )
        ],
    )

    params = db.execute.await_args.args[0].compile().params
    assert "email" in params.values()
    assert hashlib.sha256(
        f"{scope}:{user_id}:email:run_failed".encode("utf-8")
    ).hexdigest() in params.values()


@pytest.mark.asyncio
async def test_team_notification_staging_uses_stable_key_and_route_snapshot(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    scope = f"transition-run:{uuid.uuid4()}"
    db = MagicMock()
    db.execute = AsyncMock()

    async def _stage(target: str):
        await manager.stage_team_notification_deliveries(
            db,
            project_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            delivery_scope=scope,
            deliveries=[{
                "team_name": "payments",
                "channel_type": NotificationChannel.SLACK.value,
                "target": target,
                "event_type": NotificationEventType.TEST_RECOVERED.value,
                "title": "recovered",
                "body": "body",
                "metadata": {"build_number": "42"},
                "fallback": None,
            }],
        )

    await _stage("https://hooks.slack.test/original")
    await _stage("https://hooks.slack.test/changed-before-retry")

    statements = [call.args[0] for call in db.execute.await_args_list]
    compiled = [statement.compile().params for statement in statements]
    keys = [
        next(value for name, value in params.items() if name.startswith("delivery_key"))
        for params in compiled
    ]
    assert keys == [
        hashlib.sha256(f"{scope}:team:payments:slack".encode()).hexdigest()
    ] * 2
    snapshots = [
        next(
            value
            for name, value in params.items()
            if name.startswith("delivery_metadata")
        )[manager._TEAM_ROUTE_METADATA_KEY]
        for params in compiled
    ]
    assert snapshots[0]["target"] == "https://hooks.slack.test/original"
    assert snapshots[1]["target"] == "https://hooks.slack.test/changed-before-retry"


@pytest.mark.asyncio
async def test_team_notification_provider_failure_retries_without_fallback(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    delivery_key = hashlib.sha256(b"stable-team-notification").hexdigest()
    fallback_scope = f"transition-run:{uuid.uuid4()}:team-fallback:payments"
    token = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        preference_id=None,
        delivery_attempts=1,
        delivery_token=token,
        event_type=NotificationEventType.TEST_RECOVERED.value,
        title="payments recovered",
        body="body",
        delivery_metadata={
            "build_number": "42",
            manager._TEAM_ROUTE_METADATA_KEY: {
                "team_name": "payments",
                "channel_type": NotificationChannel.SLACK.value,
                "target": "https://hooks.slack.test/snapshotted",
                "fallback": {
                    "delivery_scope": fallback_scope,
                    "events": [NotificationEventType.TEST_RECOVERED.value],
                    "title": "recovered",
                    "body": "fallback body",
                    "metadata": {"build_number": "42"},
                },
            },
        },
        delivery_key=delivery_key,
    )
    claim_db = MagicMock()
    claim_db.commit = AsyncMock()
    outcome_db = MagicMock()
    outcome_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    outcome_db.commit = AsyncMock()
    sessions = iter([claim_db, outcome_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        manager,
        "claim_pending_notification_deliveries",
        AsyncMock(return_value=[row]),
    )
    send = AsyncMock(side_effect=RuntimeError("provider timeout"))
    monkeypatch.setattr(manager.slack_service, "send_notification", send)
    fallback = AsyncMock()
    monkeypatch.setattr(manager, "_stage_scoped_preference_deliveries", fallback)

    result = await manager.relay_pending_notification_deliveries()

    assert result == {"claimed": 1, "sent": 0, "retrying": 1, "failed": 0}
    assert send.await_args.kwargs["webhook_url"] == (
        "https://hooks.slack.test/snapshotted"
    )
    assert send.await_args.kwargs["delivery_id"] == delivery_key
    assert manager._TEAM_ROUTE_METADATA_KEY not in send.await_args.kwargs["metadata"]
    fallback.assert_not_awaited()
    statement = outcome_db.execute.await_args.args[0]
    assert statement.compile().params["status"] == "pending"
    assert statement.compile().params["error_detail"] == "provider timeout"


@pytest.mark.asyncio
async def test_terminal_team_failure_stages_fallback_in_fenced_outcome_transaction(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    fallback_scope = f"transition-run:{uuid.uuid4()}:team-fallback:payments"
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        preference_id=None,
        delivery_attempts=manager._MAX_DURABLE_DELIVERY_ATTEMPTS,
        delivery_token=uuid.uuid4(),
        event_type=NotificationEventType.TEST_RECOVERED.value,
        title="payments recovered",
        body="body",
        delivery_metadata={
            manager._TEAM_ROUTE_METADATA_KEY: {
                "team_name": "payments",
                "channel_type": NotificationChannel.SLACK.value,
                "target": "https://hooks.slack.test/snapshotted",
                "fallback": {
                    "delivery_scope": fallback_scope,
                    "events": [NotificationEventType.TEST_RECOVERED.value],
                    "title": "recovered",
                    "body": "fallback body",
                    "metadata": {"build_number": "42"},
                },
            }
        },
        delivery_key=hashlib.sha256(b"terminal-team-notification").hexdigest(),
    )
    claim_db = MagicMock()
    claim_db.commit = AsyncMock()
    outcome_db = MagicMock()
    outcome_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    outcome_db.commit = AsyncMock()
    sessions = iter([claim_db, outcome_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        manager,
        "claim_pending_notification_deliveries",
        AsyncMock(return_value=[row]),
    )
    monkeypatch.setattr(
        manager.slack_service,
        "send_notification",
        AsyncMock(side_effect=RuntimeError("provider timeout")),
    )
    fallback = AsyncMock()
    monkeypatch.setattr(manager, "_stage_scoped_preference_deliveries", fallback)

    result = await manager.relay_pending_notification_deliveries()

    assert result == {"claimed": 1, "sent": 0, "retrying": 0, "failed": 1}
    fallback.assert_awaited_once()
    assert fallback.await_args.args == (outcome_db,)
    assert fallback.await_args.kwargs["project_id"] == row.project_id
    assert fallback.await_args.kwargs["run_id"] == row.run_id
    assert fallback.await_args.kwargs["events"] == [
        NotificationEventType.TEST_RECOVERED
    ]
    assert fallback.await_args.kwargs["metadata"] == {"build_number": "42"}
    assert fallback.await_args.kwargs["delivery_scope"] == fallback_scope
    outcome_statement = outcome_db.execute.await_args_list[0].args[0]
    assert outcome_statement.compile().params["status"] == "failed"
    assert outcome_statement.compile().params["routing_fallback"] == "delivery_failed"
    outcome_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_terminal_team_worker_cannot_stage_fallback(monkeypatch):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        preference_id=None,
        delivery_attempts=manager._MAX_DURABLE_DELIVERY_ATTEMPTS,
        delivery_token=uuid.uuid4(),
        event_type=NotificationEventType.TEST_RECOVERED.value,
        title="payments recovered",
        body="body",
        delivery_metadata={
            manager._TEAM_ROUTE_METADATA_KEY: {
                "team_name": "payments",
                "channel_type": NotificationChannel.SLACK.value,
                "target": "https://hooks.slack.test/snapshotted",
                "fallback": {
                    "delivery_scope": f"transition-run:{uuid.uuid4()}:fallback",
                    "events": [NotificationEventType.TEST_RECOVERED.value],
                },
            }
        },
        delivery_key=hashlib.sha256(b"stale-team-notification").hexdigest(),
    )
    claim_db = MagicMock()
    claim_db.commit = AsyncMock()
    outcome_db = MagicMock()
    outcome_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    outcome_db.commit = AsyncMock()
    sessions = iter([claim_db, outcome_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        manager,
        "claim_pending_notification_deliveries",
        AsyncMock(return_value=[row]),
    )
    monkeypatch.setattr(
        manager.slack_service,
        "send_notification",
        AsyncMock(side_effect=RuntimeError("provider timeout")),
    )
    fallback = AsyncMock()
    monkeypatch.setattr(manager, "_stage_scoped_preference_deliveries", fallback)

    result = await manager.relay_pending_notification_deliveries()

    assert result == {"claimed": 1, "sent": 0, "retrying": 0, "failed": 0}
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_relay_retries_provider_failure_with_stable_identity(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    delivery_key = hashlib.sha256(b"stable-notification").hexdigest()
    pref = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        email_override=None,
    )
    token = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        # Every NotificationLog row has a project_id column; the relay re-checks
        # the recipient against it before delivering (QA-R3-1).
        project_id=pref.project_id,
        preference_id=pref.id,
        delivery_attempts=1,
        delivery_token=token,
        event_type=NotificationEventType.RUN_FAILED.value,
        title="failed",
        body="body",
        delivery_metadata={"pass_rate": 0.0},
        delivery_key=delivery_key,
    )
    preferences = MagicMock()
    preferences.all.return_value = [(pref, "qa@example.test")]
    recipient = MagicMock()
    recipient.all.return_value = [(pref.user_id, True, "QA_ENGINEER")]
    membership = MagicMock()
    membership.all.return_value = [(pref.user_id, pref.project_id)]
    claim_db = MagicMock()
    claim_db.execute = AsyncMock(side_effect=[preferences, recipient, membership])
    claim_db.commit = AsyncMock()
    outcome_db = MagicMock()
    outcome_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    outcome_db.commit = AsyncMock()
    sessions = iter([claim_db, outcome_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        manager,
        "claim_pending_notification_deliveries",
        AsyncMock(return_value=[row]),
    )
    send = AsyncMock(return_value=("failed", "provider timeout"))
    monkeypatch.setattr(manager, "_dispatch_to_channel", send)
    monkeypatch.setattr(
        manager.email_service,
        "_get_smtp_cfg",
        AsyncMock(return_value={"enabled": True}),
    )

    result = await manager.relay_pending_notification_deliveries()

    assert result == {"claimed": 1, "sent": 0, "retrying": 1, "failed": 0}
    assert send.await_args.kwargs["delivery_id"] == delivery_key
    outcome_db.commit.assert_awaited_once()
    statement = outcome_db.execute.await_args.args[0]
    assert statement.compile().params["status"] == "pending"
    assert statement.compile().params["error_detail"] == "provider timeout"


@pytest.mark.asyncio
async def test_notification_retry_rechecks_review_before_sending_accepted_narrative(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    pref = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        email_override=None,
    )
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=pref.project_id,
        run_id=uuid.uuid4(),
        preference_id=pref.id,
        delivery_attempts=1,
        delivery_token=uuid.uuid4(),
        event_type=NotificationEventType.AI_ANALYSIS_COMPLETE.value,
        title="AI summary",
        body="stale pending draft",
        delivery_metadata={
            "pass_rate": 80.0,
            "_review_gate_v1": {"original_summary": "accepted narrative"},
        },
        delivery_key=hashlib.sha256(b"review-aware-notification").hexdigest(),
    )

    claim_dbs = [MagicMock(), MagicMock()]
    outcome_dbs = [MagicMock(), MagicMock()]
    for db in claim_dbs:
        preferences = MagicMock()
        preferences.all.return_value = [(pref, "qa@example.test")]
        recipient = MagicMock()
        recipient.all.return_value = [(pref.user_id, True, "QA_ENGINEER")]
        membership = MagicMock()
        membership.all.return_value = [(pref.user_id, pref.project_id)]
        db.execute = AsyncMock(side_effect=[preferences, recipient, membership])
        db.commit = AsyncMock()
    for db in outcome_dbs:
        db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
        db.commit = AsyncMock()
    sessions = iter(
        [claim_dbs[0], outcome_dbs[0], claim_dbs[1], outcome_dbs[1]]
    )

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    first_decision = object()
    second_decision = object()
    refresh = AsyncMock(
        side_effect=[
            (
                "AI summary",
                "awaiting human review",
                {"pass_rate": 80.0},
                first_decision,
            ),
            (
                "AI summary",
                "accepted narrative",
                {"pass_rate": 80.0},
                second_decision,
            ),
        ]
    )
    send = AsyncMock(side_effect=[("failed", "provider timeout"), ("sent", None)])
    record_distribution = AsyncMock()
    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        manager,
        "claim_pending_notification_deliveries",
        AsyncMock(side_effect=[[row], [row]]),
    )
    monkeypatch.setattr(
        manager, "_refresh_review_gated_delivery", refresh, raising=False
    )
    monkeypatch.setattr(manager, "_dispatch_to_channel", send)
    monkeypatch.setattr(
        "app.services.report_distribution_policy.record_distribution",
        record_distribution,
    )
    monkeypatch.setattr(
        manager.email_service,
        "_get_smtp_cfg",
        AsyncMock(return_value={"enabled": True}),
    )

    first = await manager.relay_pending_notification_deliveries()
    row.delivery_attempts = 2
    row.delivery_token = uuid.uuid4()
    second = await manager.relay_pending_notification_deliveries()

    assert first == {"claimed": 1, "sent": 0, "retrying": 1, "failed": 0}
    assert second == {"claimed": 1, "sent": 1, "retrying": 0, "failed": 0}
    assert [call.args[3] for call in send.await_args_list] == [
        "awaiting human review",
        "accepted narrative",
    ]
    assert refresh.await_count == 2
    record_distribution.assert_awaited_once()
    assert record_distribution.await_args.args[1] is second_decision
    successful_update = outcome_dbs[1].execute.await_args_list[0].args[0]
    successful_values = successful_update.compile().params
    assert "accepted narrative" in successful_values.values()
    assert "stale pending draft" not in successful_values.values()


@pytest.mark.asyncio
async def test_explicit_digest_delivery_retries_then_updates_subscription_once(
    monkeypatch,
):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    subscription_id = uuid.uuid4()
    delivery_key = hashlib.sha256(b"stable-ai-digest").hexdigest()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        preference_id=None,
        delivery_attempts=1,
        delivery_token=uuid.uuid4(),
        event_type=NotificationEventType.AI_ANALYSIS_COMPLETE.value,
        title="AI summary",
        body="summary body",
        delivery_metadata={
            "build_number": "42",
            manager._EXPLICIT_ROUTE_METADATA_KEY: {
                "channel_type": NotificationChannel.EMAIL.value,
                "target": "qa@example.test",
                "digest_subscription_id": str(subscription_id),
            },
        },
        delivery_key=delivery_key,
    )
    claim_dbs = [MagicMock(), MagicMock()]
    outcome_dbs = [MagicMock(), MagicMock()]
    # Re-audit N33: the relay re-checks a staged digest before sending it --
    # the subscription is still active, and its owner may still read the
    # row's project. Each pass answers: the subscription, its owner's account,
    # the owner's membership (an active member, so the digest may go).
    owner_id = uuid.uuid4()
    for db in claim_dbs:
        db.commit = AsyncMock()
        subscription = MagicMock()
        subscription.all.return_value = [(subscription_id, owner_id)]
        account = MagicMock()
        account.all.return_value = [(owner_id, True, "QA_ENGINEER")]
        membership = MagicMock()
        membership.all.return_value = [(owner_id, row.project_id)]
        db.execute = AsyncMock(side_effect=[subscription, account, membership])
    outcome_dbs[0].execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    outcome_dbs[0].commit = AsyncMock()
    outcome_dbs[1].execute = AsyncMock(
        side_effect=[SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=1)]
    )
    outcome_dbs[1].commit = AsyncMock()
    sessions = iter(
        [claim_dbs[0], outcome_dbs[0], claim_dbs[1], outcome_dbs[1]]
    )

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    claims = AsyncMock(side_effect=[[row], [row]])
    send = AsyncMock(side_effect=[RuntimeError("provider timeout"), None])
    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(manager, "claim_pending_notification_deliveries", claims)
    monkeypatch.setattr(manager.email_service, "send_notification", send)
    monkeypatch.setattr(
        manager.email_service,
        "_get_smtp_cfg",
        AsyncMock(return_value={"enabled": True}),
    )

    first = await manager.relay_pending_notification_deliveries()
    row.delivery_attempts = 2
    row.delivery_token = uuid.uuid4()
    second = await manager.relay_pending_notification_deliveries()

    assert first == {"claimed": 1, "sent": 0, "retrying": 1, "failed": 0}
    assert second == {"claimed": 1, "sent": 1, "retrying": 0, "failed": 0}
    assert [call.kwargs["delivery_id"] for call in send.await_args_list] == [
        delivery_key,
        delivery_key,
    ]
    assert manager._EXPLICIT_ROUTE_METADATA_KEY not in send.await_args_list[1].kwargs[
        "metadata"
    ]
    assert outcome_dbs[0].execute.await_count == 1
    assert outcome_dbs[1].execute.await_count == 2
    counter_statement = outcome_dbs[1].execute.await_args_list[1].args[0]
    assert "UPDATE digest_subscriptions" in str(counter_statement)
    assert subscription_id in counter_statement.compile().params.values()


@pytest.mark.asyncio
async def test_explicit_digest_staging_uses_stable_delivery_key():
    from app.services.notification.manager import (
        _EXPLICIT_ROUTE_METADATA_KEY,
        stage_explicit_notification_deliveries,
    )

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    user_id = uuid.uuid4()
    delivery = {
        "route_id": str(subscription_id),
        "user_id": user_id,
        "channel_type": "email",
        "target": "qa@example.test",
        "event_type": "ai_analysis_complete",
        "title": "AI summary",
        "body": "summary body",
        "digest_subscription_id": str(subscription_id),
        "metadata": {"build_number": "42"},
    }
    first_db = MagicMock(execute=AsyncMock())
    second_db = MagicMock(execute=AsyncMock())

    for db in (first_db, second_db):
        await stage_explicit_notification_deliveries(
            db,
            project_id=project_id,
            run_id=run_id,
            delivery_scope=f"ai-summary:{run_id}:v1:digests",
            deliveries=[delivery],
        )

    first_params = first_db.execute.await_args.args[0].compile().params
    second_params = second_db.execute.await_args.args[0].compile().params
    first_key = next(
        value for key, value in first_params.items() if "delivery_key" in key
    )
    second_key = next(
        value for key, value in second_params.items() if "delivery_key" in key
    )
    assert first_key == second_key
    assert len(first_key) == 64
    metadata = next(
        value for key, value in first_params.items() if "delivery_metadata" in key
    )
    assert metadata[_EXPLICIT_ROUTE_METADATA_KEY]["target"] == "qa@example.test"
    assert metadata[_EXPLICIT_ROUTE_METADATA_KEY]["digest_subscription_id"] == str(
        subscription_id
    )


@pytest.mark.asyncio
async def test_notification_relay_reclaims_expired_ambiguous_send():
    from app.services.notification.manager import (
        claim_pending_notification_deliveries,
    )

    old_token = uuid.uuid4()
    row = SimpleNamespace(
        status="sending",
        delivery_attempts=1,
        delivery_token=old_token,
        delivery_started_at=None,
        delivery_lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        next_delivery_at=None,
        error_detail=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    claimed = await claim_pending_notification_deliveries(db)

    assert claimed == [row]
    claim_statement = db.execute.await_args.args[0]
    assert "notification_logs.preference_id IS NOT NULL" not in str(claim_statement)
    assert row.status == "sending"
    assert row.delivery_attempts == 2
    assert row.delivery_token != old_token
    assert row.delivery_lease_expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_exhausted_notification_is_leased_for_atomic_terminal_fallback():
    from app.services.notification import manager

    row = SimpleNamespace(
        status="sending",
        delivery_attempts=manager._MAX_DURABLE_DELIVERY_ATTEMPTS,
        delivery_token=uuid.uuid4(),
        delivery_started_at=None,
        delivery_lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        next_delivery_at=None,
        error_detail="provider timeout",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    claimed = await manager.claim_pending_notification_deliveries(db)

    assert claimed == [row]
    assert row.status == "sending"
    assert row.delivery_attempts == manager._MAX_DURABLE_DELIVERY_ATTEMPTS
    assert row._relay_exhausted is True
    assert row.delivery_token is not None
    assert row.delivery_lease_expires_at > datetime.now(timezone.utc)


def test_agent_outbox_ignores_competing_direct_pipeline_redis_dedup(monkeypatch):
    from app.agents import workflow
    from app.services import intelligence_snapshot_service, run_downstream_outbox
    from app.worker import tasks

    outbox_id = uuid.uuid4()
    run_pipeline = AsyncMock(return_value={"completed_stages": [], "errors": []})
    monkeypatch.setattr(workflow, "run_offline_pipeline", run_pipeline)
    redis_duplicate = AsyncMock(return_value=True)
    monkeypatch.setattr(tasks, "_is_duplicate", redis_duplicate)
    monkeypatch.setattr(
        run_downstream_outbox,
        "repair_terminal_ai_summary_operation",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        intelligence_snapshot_service,
        "invalidate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    db = MagicMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    import app.db.postgres as postgres

    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())

    task = tasks.run_agent_pipeline
    task.push_request(
        id=f"run-downstream-{outbox_id}",
        retries=0,
        headers={"downstream_outbox_id": str(outbox_id)},
    )
    try:
        task.run(
            test_run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            build_number="42",
        )
    finally:
        task.pop_request()

    expected_pipeline_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"testlookup:agent-pipeline:{outbox_id}",
        )
    )
    assert run_pipeline.await_args.kwargs["pipeline_run_id"] == expected_pipeline_id
    assert run_pipeline.await_args.kwargs["create_if_missing"] is True
    redis_duplicate.assert_not_awaited()


def test_terminal_pipeline_retry_repairs_ai_child_before_parent_completion(monkeypatch):
    from app.agents import workflow
    from app.services import intelligence_snapshot_service, run_downstream_outbox
    from app.worker import tasks

    outbox_id = uuid.uuid4()
    dispatch_token = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    calls: list[str] = []
    run_pipeline = AsyncMock()
    monkeypatch.setattr(workflow, "run_offline_pipeline", run_pipeline)
    monkeypatch.setattr(
        intelligence_snapshot_service,
        "invalidate",
        AsyncMock(return_value=None),
    )
    repair = AsyncMock(
        side_effect=lambda *_args, **_kwargs: calls.append("repair_child")
        or {
            "pipeline_status": "completed",
            "summary_completed": True,
            "child_inserted": True,
        }
    )
    monkeypatch.setattr(
        run_downstream_outbox,
        "repair_terminal_ai_summary_operation",
        repair,
    )
    monkeypatch.setattr(
        run_downstream_outbox,
        "begin_downstream_execution",
        AsyncMock(side_effect=lambda **_kwargs: calls.append("begin_parent") or True),
    )
    monkeypatch.setattr(
        run_downstream_outbox,
        "complete_downstream_execution",
        AsyncMock(side_effect=lambda **_kwargs: calls.append("complete_parent") or True),
    )
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    db = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: calls.append("commit_child"))
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    import app.db.postgres as postgres
    from celery.app.task import Task as CeleryTask

    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    # Direct Celery task calls push a fresh empty request in this local harness,
    # hiding the delivery headers from the task body. The worker tracer keeps
    # one request context. Model that production behavior while still invoking
    # DownstreamTrackedTask.__call__ and its parent completion hooks.
    monkeypatch.setattr(
        CeleryTask,
        "__call__",
        lambda self, *args, **kwargs: self.run(*args, **kwargs),
    )
    task = tasks.run_agent_pipeline
    task.push_request(
        id=f"run-downstream-{outbox_id}",
        retries=0,
        headers={
            "downstream_outbox_id": str(outbox_id),
            "downstream_dispatch_token": str(dispatch_token),
        },
    )
    try:
        result = task(
            test_run_id=str(run_id),
            project_id=str(project_id),
            build_number="42",
        )
    finally:
        task.pop_request()

    assert result == {"completed_stages": ["summary"], "error_count": 0}
    assert calls.index("repair_child") < calls.index("commit_child")
    assert calls.index("commit_child") < calls.index("complete_parent")
    assert run_pipeline.await_count == 0
    repair.assert_awaited_once()
    assert repair.await_args.kwargs["pipeline_run_id"] == uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"testlookup:agent-pipeline:{outbox_id}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "passed"])
async def test_terminal_success_repair_uses_completed_summary_stage(
    monkeypatch,
    terminal_status,
):
    from app.services import run_downstream_outbox as service

    pipeline_run_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    terminal_result = MagicMock()
    terminal_result.scalar_one_or_none.return_value = terminal_status
    summary_result = MagicMock()
    summary_result.scalar_one_or_none.return_value = uuid.uuid4()
    db = SimpleNamespace(execute=AsyncMock(side_effect=[terminal_result, summary_result]))
    stage = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_ai_summary_notification_operation", stage)

    repaired = await service.repair_terminal_ai_summary_operation(
        db,
        pipeline_run_id=pipeline_run_id,
        run_id=run_id,
        project_id=project_id,
        build_number="42",
    )

    assert repaired == {
        "pipeline_status": terminal_status,
        "summary_completed": True,
        "child_inserted": True,
    }
    stage.assert_awaited_once_with(
        db,
        run_id=run_id,
        project_id=project_id,
        build_number="42",
    )


@pytest.mark.asyncio
async def test_terminal_success_without_completed_summary_preserves_no_child(monkeypatch):
    from app.services import run_downstream_outbox as service

    terminal_result = MagicMock()
    terminal_result.scalar_one_or_none.return_value = "completed"
    summary_result = MagicMock()
    summary_result.scalar_one_or_none.return_value = None
    db = SimpleNamespace(execute=AsyncMock(side_effect=[terminal_result, summary_result]))
    stage = AsyncMock()
    monkeypatch.setattr(service, "stage_ai_summary_notification_operation", stage)

    repaired = await service.repair_terminal_ai_summary_operation(
        db,
        pipeline_run_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="42",
    )

    assert repaired == {
        "pipeline_status": "completed",
        "summary_completed": False,
        "child_inserted": False,
    }
    stage.assert_not_awaited()


def test_pipeline_terminalization_stages_ai_child_before_commit():
    import inspect

    from app.agents.workflow import _mark_pipeline_done

    source = inspect.getsource(_mark_pipeline_done)
    assert source.index("stage_ai_summary_notification_operation") < source.rindex(
        "await db.commit()"
    )


def test_ai_summary_consumer_stages_preference_and_digest_children(monkeypatch):
    from app.db import mongo, postgres
    from app.services import auto_tagging_service
    from app.services.notification import manager
    from app.worker import tasks

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        project_id=project_id,
        pass_rate=75.0,
        total_tests=4,
        failed_tests=1,
        primary_suite_name="smoke",
        primary_release_id=None,
    )
    project = SimpleNamespace(name="payments")
    subscription = SimpleNamespace(
        id=subscription_id,
        user_id=user_id,
        project_id=project_id,
        schedule="PER_RUN",
        scope_value=None,
        trigger_filter="all",
    )
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project
    load_db = SimpleNamespace(execute=AsyncMock(side_effect=[run_result, project_result]))
    digest_result = MagicMock()
    digest_result.all.return_value = [(subscription, "qa@example.test")]
    digest_db = SimpleNamespace(execute=AsyncMock(return_value=digest_result), commit=AsyncMock())
    tag_db = SimpleNamespace(commit=AsyncMock())
    # E8.4: the task now opens one session between load and digest to gate the
    # AI summary on human review. This test is about staging the preference and
    # digest children, so the gate is stubbed below and gets its own session.
    gate_db = SimpleNamespace(commit=AsyncMock())
    sessions = iter([load_db, gate_db, digest_db, tag_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    class _Summaries:
        async def find_one(self, _query):
            return {
                "executive_summary": "one failure requires review",
                "executive_panel": {"status_signal": "CONDITIONAL_GO"},
            }

    from app.db.mongo import Collections

    dispatch_preferences = AsyncMock()
    stage_digests = AsyncMock()
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    from app.services import report_distribution_policy

    async def _no_gate(_db, **kwargs):
        return kwargs["summary_text"], None

    monkeypatch.setattr(report_distribution_policy, "gate_ai_summary_text", _no_gate)
    monkeypatch.setattr(
        mongo,
        "get_mongo_db",
        lambda: {Collections.RUN_SUMMARIES: _Summaries()},
    )
    monkeypatch.setattr(
        manager,
        "dispatch_ai_summary_notifications",
        dispatch_preferences,
    )
    monkeypatch.setattr(
        manager,
        "stage_explicit_notification_deliveries",
        stage_digests,
    )
    monkeypatch.setattr(
        auto_tagging_service,
        "auto_tag_after_analysis",
        AsyncMock(return_value=None),
    )

    task = tasks.dispatch_ai_summary_email
    task.push_request(id="ai-summary-consumer", retries=0)
    try:
        task.run(
            test_run_id=str(run_id),
            project_id=str(project_id),
            build_number="42",
        )
    finally:
        task.pop_request()

    dispatch_preferences.assert_awaited_once()
    assert dispatch_preferences.await_args.kwargs["delivery_scope"] == (
        f"ai-summary:{run_id}:v1:preferences"
    )
    stage_digests.assert_awaited_once()
    assert stage_digests.await_args.args == (digest_db,)
    assert stage_digests.await_args.kwargs["delivery_scope"] == (
        f"ai-summary:{run_id}:v1:digests"
    )
    delivery = stage_digests.await_args.kwargs["deliveries"][0]
    assert delivery["route_id"] == str(subscription_id)
    assert delivery["digest_subscription_id"] == str(subscription_id)
    assert delivery["target"] == "qa@example.test"
    digest_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_waiting_finalization_claim_is_leased_and_bounded():
    from app.services import run_downstream_outbox as service

    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="worker-lost-42",
    )
    runs_result = MagicMock()
    runs_result.scalars.return_value.all.return_value = [run]
    children_result = MagicMock()
    children_result.all.return_value = [
        SimpleNamespace(operation="run_notifications", attempts=0),
        SimpleNamespace(operation="agent_pipeline", attempts=0),
    ]
    update_result = MagicMock(rowcount=2)
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[runs_result, children_result, update_result]
    )

    claims = await service.claim_waiting_finalizations(
        db, limit=1, lease_seconds=600
    )

    assert len(claims) == 1
    assert claims[0]["run_id"] == run.id
    assert claims[0]["run_ai"] is True
    assert claims[0]["attempt"] == 1
    update_statement = db.execute.await_args_list[2].args[0]
    params = update_statement.compile().params
    assert params["attempts"] == 1
    assert params["dispatch_token"] == claims[0]["recovery_token"]
    assert params["next_attempt_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_waiting_finalization_recovery_stops_after_bounded_attempts():
    from app.services import run_downstream_outbox as service

    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="permanent-finalize-failure",
    )
    runs_result = MagicMock()
    runs_result.scalars.return_value.all.return_value = [run]
    children_result = MagicMock()
    children_result.all.return_value = [
        SimpleNamespace(
            operation="run_notifications",
            attempts=service.MAX_FINALIZATION_RECOVERY_ATTEMPTS,
        )
    ]
    update_result = MagicMock(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[runs_result, children_result, update_result]
    )

    claims = await service.claim_waiting_finalizations(db, limit=1)

    assert claims == []
    terminal_statement = db.execute.await_args_list[2].args[0]
    params = terminal_statement.compile().params
    assert params["status"] == "failed"
    assert params["last_error"] == "finalization_recovery_attempts_exhausted"
    assert params["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_waiting_recovery_failure_is_token_fenced():
    from app.services import run_downstream_outbox as service

    result = MagicMock(rowcount=0)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    token = uuid.uuid4()

    changed = await service.mark_waiting_finalization_recovery_failed(
        db,
        run_id=uuid.uuid4(),
        recovery_token=token,
        attempt=2,
        error_code="finalization_RuntimeError",
    )

    assert changed is False
    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "run_downstream_outbox.dispatch_token" in sql
    assert statement.compile().params["dispatch_token_1"] == token


@pytest.mark.asyncio
async def test_waiting_recovery_retries_finalize_after_worker_loss(monkeypatch):
    from app.services import ingestion_pipeline
    from app.services import run_downstream_outbox as service

    claim = {
        "run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "build_number": "lost-worker",
        "run_ai": True,
        "recovery_token": uuid.uuid4(),
        "attempt": 1,
    }
    class _TransactionContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return False

    claim_db = MagicMock()
    claim_db.begin.return_value = _TransactionContext()
    retry_db = MagicMock()
    retry_db.begin.return_value = _TransactionContext()
    sessions = iter([claim_db, retry_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        service, "claim_waiting_finalizations", AsyncMock(return_value=[claim])
    )
    finalize = AsyncMock(side_effect=RuntimeError("worker died"))
    monkeypatch.setattr(ingestion_pipeline, "finalize_run", finalize)
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(
        service, "mark_waiting_finalization_recovery_failed", mark_failed
    )

    result = await service.recover_waiting_finalizations(limit=1)

    assert result == {"claimed": 1, "completed": 0, "retrying": 1}
    finalize.assert_awaited_once_with(
        run_id=str(claim["run_id"]),
        project_id=str(claim["project_id"]),
        build_number="lost-worker",
        run_ai=True,
    )
    mark_failed.assert_awaited_once_with(
        retry_db,
        run_id=claim["run_id"],
        recovery_token=claim["recovery_token"],
        attempt=1,
        error_code="finalization_RuntimeError",
    )


def test_celery_does_not_globally_requeue_worker_lost_tasks():
    from app.worker.celery_app import celery_app

    assert celery_app.conf.task_acks_late is True
    # H12 recovery is owned by PostgreSQL leases. Enabling this globally would
    # also replay unrelated tasks that perform non-idempotent provider I/O.
    assert not celery_app.conf.task_reject_on_worker_lost
    assert (
        "recover-waiting-run-finalizations" in celery_app.conf.beat_schedule
    )


# ── Every staged payload must fit its task (found by the N14 homelab check) ──


@pytest.mark.asyncio
async def test_every_staged_payload_binds_to_its_task(monkeypatch):
    """Celery checks a task's arguments inside apply_async, before anything
    reaches the broker. agent_pipeline was staged as {run_id, ...} for a task
    whose parameter is test_run_id, so every publish raised TypeError and no
    finished run's AI pipeline ever started -- while every test here replaced
    apply_async, which is exactly where that check lives."""
    import inspect

    from app.services import run_downstream_outbox as service

    staged = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_downstream_operation", staged)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="build-42",
        pass_rate=75.0,
        total_tests=4,
        failed_tests=1,
        end_time=datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
    )
    session = SimpleNamespace(
        run_id="sdk-run-42", project_id=run.project_id, build_number="build-42",
        client_name="pytest", framework="pytest", branch="main", commit_hash="abc123",
        suite_name="unit", completed_at=None,
    )

    await service.stage_finalize_operations(
        MagicMock(), run=run, project=SimpleNamespace(name="payments"), run_ai=True
    )
    await service.stage_ai_summary_notification_operation(
        MagicMock(), run_id=run.id, project_id=run.project_id, build_number="build-42"
    )
    await service.stage_live_persist_operation(
        MagicMock(), canonical_run_id=run.id, session=session, final_state={"passed": 1}
    )

    payloads = {call.kwargs["operation"]: call.kwargs["payload"] for call in staged.await_args_list}
    assert set(payloads) == set(service._OPERATIONS), (
        "an operation the outbox publishes is not staged here, so its payload is unchecked"
    )
    tasks = service.downstream_tasks()
    assert set(tasks) == set(service._OPERATIONS)
    for operation, payload in sorted(payloads.items()):
        try:
            inspect.signature(tasks[operation].run).bind(**payload)
        except TypeError as exc:
            pytest.fail(f"{operation} is staged with arguments its task refuses: {exc}")


def test_an_agent_pipeline_staged_with_the_old_key_still_publishes(monkeypatch):
    """A run already stuck on broker_TypeError gets its analysis after the upgrade."""
    import inspect

    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    apply_async = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "apply_async", apply_async)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        operation="agent_pipeline",
        payload={"run_id": "r-1", "project_id": "p-1", "build_number": "b-1", "workflow_type": "offline"},
        queue="ai_analysis",
        priority=6,
        dispatch_token=uuid.uuid4(),
    )

    service._publish_downstream(row)

    sent = apply_async.call_args.kwargs["kwargs"]
    assert sent == {"test_run_id": "r-1", "project_id": "p-1", "build_number": "b-1", "workflow_type": "offline"}
    inspect.signature(tasks.run_agent_pipeline.run).bind(**sent)
    assert row.payload["run_id"] == "r-1", "the stored intent was mutated in place"
