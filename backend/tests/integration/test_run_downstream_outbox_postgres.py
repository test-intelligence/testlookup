"""Real PostgreSQL atomicity and concurrency coverage for the run outbox."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    NotificationChannel,
    NotificationEventType,
    NotificationLog,
    NotificationPreference,
    NotificationTestState,
    Project,
    RunDownstreamOutbox,
    TestCase as DbTestCase,
    TestStatus as DbTestStatus,
    TestRun as DbTestRun,
    User,
    WebhookDelivery,
    WebhookSubscription,
)
from app.services.run_downstream_outbox import (
    claim_downstream_dispatches,
    claim_waiting_finalizations,
    operation_input_version,
    stage_downstream_operation,
)

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _delete_projects(db, *project_ids: uuid.UUID) -> None:
    """Use SQL DELETE so database cascades own integration-test cleanup."""
    await db.execute(delete(Project).where(Project.id.in_(project_ids)))


async def test_parent_claim_is_tenant_fair_in_postgres_above_child_candidate_cap():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    noisy_project_id, peer_project_id = uuid.uuid4(), uuid.uuid4()
    noisy_run_id, peer_run_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions() as db:
            db.add_all(
                [
                    Project(
                        id=noisy_project_id,
                        name=f"fair-noisy-{noisy_project_id.hex}",
                        slug=f"fair-noisy-{noisy_project_id.hex}",
                    ),
                    Project(
                        id=peer_project_id,
                        name=f"fair-peer-{peer_project_id.hex}",
                        slug=f"fair-peer-{peer_project_id.hex}",
                    ),
                    DbTestRun(
                        id=noisy_run_id,
                        project_id=noisy_project_id,
                        build_number=f"fair-noisy-{noisy_run_id.hex}",
                        status="PASSED",
                        total_tests=0,
                        passed_tests=0,
                        failed_tests=0,
                        skipped_tests=0,
                        broken_tests=0,
                        unknown_tests=0,
                    ),
                    DbTestRun(
                        id=peer_run_id,
                        project_id=peer_project_id,
                        build_number=f"fair-peer-{peer_run_id.hex}",
                        status="PASSED",
                        total_tests=0,
                        passed_tests=0,
                        failed_tests=0,
                        skipped_tests=0,
                        broken_tests=0,
                        unknown_tests=0,
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    RunDownstreamOutbox(
                        run_id=noisy_run_id,
                        project_id=noisy_project_id,
                        operation="transition_notifications",
                        input_version=f"{index:064x}",
                        payload={"run_id": str(noisy_run_id)},
                    )
                    for index in range(501)
                ]
                + [
                    RunDownstreamOutbox(
                        run_id=peer_run_id,
                        project_id=peer_project_id,
                        operation="transition_notifications",
                        input_version="f" * 64,
                        payload={"run_id": str(peer_run_id)},
                    )
                ]
            )
            await db.commit()

        async with sessions() as db:
            claimed = await claim_downstream_dispatches(db, limit=2)
            assert {row.project_id for row in claimed} == {
                noisy_project_id,
                peer_project_id,
            }
            await db.rollback()
    finally:
        async with sessions() as db:
            await _delete_projects(db, noisy_project_id, peer_project_id)
            await db.commit()
        await engine.dispose()


async def test_waiting_finalization_recovery_is_tenant_fair_above_batch_limit():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    noisy_project_id, peer_project_id = uuid.uuid4(), uuid.uuid4()
    noisy_run_ids = [uuid.uuid4() for _ in range(6)]
    peer_run_id = uuid.uuid4()
    old_created_at = datetime.now(timezone.utc) - timedelta(days=2)
    peer_created_at = datetime.now(timezone.utc) - timedelta(days=1)
    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    try:
        async with sessions() as db:
            db.add_all(
                [
                    Project(
                        id=noisy_project_id,
                        name=f"waiting-fair-noisy-{noisy_project_id.hex}",
                        slug=f"waiting-fair-noisy-{noisy_project_id.hex}",
                    ),
                    Project(
                        id=peer_project_id,
                        name=f"waiting-fair-peer-{peer_project_id.hex}",
                        slug=f"waiting-fair-peer-{peer_project_id.hex}",
                    ),
                ]
            )
            runs = [
                DbTestRun(
                    id=run_id,
                    project_id=noisy_project_id,
                    build_number=f"waiting-fair-noisy-{run_id.hex}",
                    status="PASSED",
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                    created_at=old_created_at + timedelta(seconds=index),
                )
                for index, run_id in enumerate(noisy_run_ids)
            ]
            runs.append(
                DbTestRun(
                    id=peer_run_id,
                    project_id=peer_project_id,
                    build_number=f"waiting-fair-peer-{peer_run_id.hex}",
                    status="PASSED",
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                    created_at=peer_created_at,
                )
            )
            db.add_all(runs)
            await db.flush()
            db.add_all(
                [
                    RunDownstreamOutbox(
                        run_id=run.id,
                        project_id=run.project_id,
                        operation="transition_notifications",
                        input_version="a" * 64,
                        payload={"run_id": str(run.id)},
                        status="waiting",
                        next_attempt_at=due_at,
                    )
                    for run in runs
                ]
            )
            await db.commit()

        async with sessions() as db:
            claims = await claim_waiting_finalizations(db, limit=2)
            assert {claim["project_id"] for claim in claims} == {
                noisy_project_id,
                peer_project_id,
            }
            await db.rollback()
    finally:
        async with sessions() as db:
            await _delete_projects(db, noisy_project_id, peer_project_id)
            await db.commit()
        await engine.dispose()


async def test_transition_state_row_lock_serializes_concurrent_mutation_in_postgres():
    from app.services.notification_transitions import _load_or_seed_states

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    fingerprint = f"lock-{uuid.uuid4().hex}"
    try:
        async with sessions() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"transition-lock-{project_id.hex}",
                    slug=f"transition-lock-{project_id.hex}",
                )
            )
            await db.flush()
            db.add(
                NotificationTestState(
                    project_id=project_id,
                    test_fingerprint=fingerprint,
                    state="passing",
                    consecutive_failures=0,
                    is_known_flaky=False,
                )
            )
            await db.commit()

        async with sessions() as first:
            first_state = (
                await _load_or_seed_states(first, project_id, [fingerprint], set())
            )[fingerprint]

            async def _read_with_second_lock():
                async with sessions() as second:
                    state = (
                        await _load_or_seed_states(
                            second, project_id, [fingerprint], set()
                        )
                    )[fingerprint]
                    consecutive_failures = state.consecutive_failures
                    await second.rollback()
                    return consecutive_failures

            second_task = asyncio.create_task(_read_with_second_lock())
            await asyncio.sleep(0.1)
            assert not second_task.done()
            first_state.consecutive_failures = 1
            await first.commit()
            assert await asyncio.wait_for(second_task, timeout=2) == 1
    finally:
        async with sessions() as db:
            await _delete_projects(db, project_id)
            await db.commit()
        await engine.dispose()


async def test_transition_default_delivery_uses_locked_transaction_without_deadlock(
    monkeypatch,
):
    """Default children share the state transaction that locks the project row."""
    import app.db.postgres as postgres
    from app.services import notification_routing, notification_transitions
    from app.services import ownership_resolver_service
    from app.services import github_pr_comment_service

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fingerprint = uuid.uuid4().hex
    try:
        async with sessions() as db:
            db.add_all(
                [
                    Project(
                        id=project_id,
                        name=f"transition-default-{project_id.hex}",
                        slug=f"transition-default-{project_id.hex}",
                    ),
                    User(
                        id=user_id,
                        email=f"transition-{user_id.hex}@example.test",
                        username=f"transition-{user_id.hex}",
                        hashed_password="not-used",
                    ),
                ]
            )
            db.add(
                DbTestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"transition-default-{run_id.hex}",
                    status="PASSED",
                    total_tests=1,
                    passed_tests=1,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                    end_time=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            db.add(
                DbTestCase(
                    test_run_id=run_id,
                    test_fingerprint=fingerprint,
                    test_name="test_recovers",
                    suite_name="smoke",
                    status=DbTestStatus.PASSED.value,
                )
            )
            db.add(
                NotificationTestState(
                    project_id=project_id,
                    test_fingerprint=fingerprint,
                    state="failing",
                    consecutive_failures=2,
                    last_notified_state="failing",
                    is_known_flaky=False,
                )
            )
            db.add(
                NotificationPreference(
                    user_id=user_id,
                    project_id=project_id,
                    channel=NotificationChannel.EMAIL.value,
                    enabled=True,
                    events=[NotificationEventType.TEST_RECOVERED.value],
                )
            )
            await db.commit()

        monkeypatch.setattr(postgres, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(
            github_pr_comment_service,
            "_flaky_fingerprints",
            AsyncMock(return_value=set()),
        )
        monkeypatch.setattr(
            notification_routing,
            "load_team_channels",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            ownership_resolver_service,
            "load_rules_for_project",
            AsyncMock(return_value=[]),
        )

        result = await asyncio.wait_for(
            notification_transitions.evaluate_run_transitions(run_id),
            timeout=5,
        )
        assert result["events"] == 1

        async with sessions() as db:
            delivery = (
                await db.execute(
                    select(NotificationLog).where(
                        NotificationLog.run_id == run_id,
                        NotificationLog.delivery_key.is_not(None),
                    )
                )
            ).scalar_one()
            state = (
                await db.execute(
                    select(NotificationTestState).where(
                        NotificationTestState.project_id == project_id,
                        NotificationTestState.test_fingerprint == fingerprint,
                    )
                )
            ).scalar_one()
            assert delivery.status == "pending"
            assert delivery.event_type == NotificationEventType.TEST_RECOVERED.value
            assert state.state == "passing"
            assert state.last_run_id == run_id
    finally:
        async with sessions() as db:
            await db.execute(
                delete(NotificationLog).where(NotificationLog.run_id == run_id)
            )
            await _delete_projects(db, project_id)
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()


async def test_newer_transition_waits_for_older_live_run_in_postgres():
    from app.services.notification_transitions import (
        TransitionOrderPending,
        _ensure_transition_order,
    )

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    older_run_id, newer_run_id = uuid.uuid4(), uuid.uuid4()
    base = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        async with sessions() as db:
            project = Project(
                id=project_id,
                name=f"transition-order-{project_id.hex}",
                slug=f"transition-order-{project_id.hex}",
            )
            older = DbTestRun(
                id=older_run_id,
                project_id=project_id,
                build_number=f"older-{older_run_id.hex}",
                status="FAILED",
                end_time=base,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                skipped_tests=0,
                broken_tests=0,
                unknown_tests=0,
            )
            newer = DbTestRun(
                id=newer_run_id,
                project_id=project_id,
                build_number=f"newer-{newer_run_id.hex}",
                status="FAILED",
                end_time=base + timedelta(seconds=1),
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                skipped_tests=0,
                broken_tests=0,
                unknown_tests=0,
            )
            db.add_all([project, older, newer])
            await db.flush()
            older_operation = RunDownstreamOutbox(
                run_id=older_run_id,
                project_id=project_id,
                operation="transition_notifications",
                input_version="a" * 64,
                payload={"run_id": str(older_run_id)},
                status="processing",
            )
            db.add_all(
                [
                    older_operation,
                    RunDownstreamOutbox(
                        run_id=newer_run_id,
                        project_id=project_id,
                        operation="transition_notifications",
                        input_version="b" * 64,
                        payload={"run_id": str(newer_run_id)},
                        status="processing",
                    ),
                ]
            )
            await db.commit()

        async with sessions() as db:
            newer = await db.get(DbTestRun, newer_run_id)
            with pytest.raises(TransitionOrderPending):
                await _ensure_transition_order(db, newer)
            await db.rollback()

        async with sessions() as db:
            older_operation = await db.get(
                RunDownstreamOutbox, older_operation.id
            )
            older_operation.status = "failed"
            await db.commit()

        # A dead-lettered predecessor is still a chronological gap. Newer
        # state must wait until an operator explicitly repairs and completes
        # the failed row.
        async with sessions() as db:
            newer = await db.get(DbTestRun, newer_run_id)
            with pytest.raises(TransitionOrderPending):
                await _ensure_transition_order(db, newer)
            await db.rollback()

        async with sessions() as db:
            older_operation = await db.get(
                RunDownstreamOutbox, older_operation.id
            )
            older_operation.status = "completed"
            await db.commit()

        async with sessions() as db:
            newer = await db.get(DbTestRun, newer_run_id)
            await _ensure_transition_order(db, newer)
    finally:
        async with sessions() as db:
            await _delete_projects(db, project_id)
            await db.commit()
        await engine.dispose()


async def test_run_and_outbox_are_atomic_and_concurrent_staging_is_unique():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid.uuid4()
    rolled_back_run_id = uuid.uuid4()
    payload = {"run_id": str(run_id), "project_id": "test", "build_number": "42"}
    version = operation_input_version("run_notifications", payload)
    try:
        async with sessions() as db:
            project_id = (
                await db.execute(select(Project.id).order_by(Project.created_at).limit(1))
            ).scalar_one()
            db.add(
                DbTestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"outbox-{run_id.hex}",
                    status="PASSED",
                    total_tests=1,
                    passed_tests=1,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                )
            )
            await db.commit()

        async def stage_once() -> bool:
            async with sessions() as db:
                inserted = await stage_downstream_operation(
                    db,
                    run_id=run_id,
                    project_id=project_id,
                    operation="run_notifications",
                    input_version=version,
                    payload=payload,
                    queue="default",
                )
                await db.commit()
                return inserted

        assert sorted(await asyncio.gather(stage_once(), stage_once())) == [False, True]
        async with sessions() as db:
            count = (
                await db.execute(
                    select(func.count(RunDownstreamOutbox.id)).where(
                        RunDownstreamOutbox.run_id == run_id
                    )
                )
            ).scalar_one()
            assert count == 1

        async with sessions() as db:
            db.add(
                DbTestRun(
                    id=rolled_back_run_id,
                    project_id=project_id,
                    build_number=f"outbox-rollback-{rolled_back_run_id.hex}",
                    status="PASSED",
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                )
            )
            await db.flush()
            rollback_payload = {"run_id": str(rolled_back_run_id)}
            await stage_downstream_operation(
                db,
                run_id=rolled_back_run_id,
                project_id=project_id,
                operation="transition_notifications",
                input_version=operation_input_version(
                    "transition_notifications", rollback_payload
                ),
                payload=rollback_payload,
                queue="default",
            )
            await db.rollback()

        async with sessions() as db:
            assert await db.get(DbTestRun, rolled_back_run_id) is None
            rolled_back_intents = (
                await db.execute(
                    select(func.count(RunDownstreamOutbox.id)).where(
                        RunDownstreamOutbox.run_id == rolled_back_run_id
                    )
                )
            ).scalar_one()
            assert rolled_back_intents == 0
    finally:
        async with sessions() as db:
            run = await db.get(DbTestRun, run_id)
            if run is not None:
                await db.delete(run)
                await db.commit()
        await engine.dispose()


async def test_waiting_finalization_recovery_has_one_concurrent_owner():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid.uuid4()
    try:
        async with sessions() as db:
            project_id = (
                await db.execute(select(Project.id).order_by(Project.created_at).limit(1))
            ).scalar_one()
            db.add(
                DbTestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"waiting-recovery-{run_id.hex}",
                    status="PASSED",
                    total_tests=1,
                    passed_tests=1,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                )
            )
            await db.flush()
            payload = {
                "run_id": str(run_id),
                "project_id": str(project_id),
                "build_number": "lost-worker",
            }
            await stage_downstream_operation(
                db,
                run_id=run_id,
                project_id=project_id,
                operation="agent_pipeline",
                input_version=operation_input_version("agent_pipeline", payload),
                payload=payload,
                queue="ai_analysis",
                initial_status="waiting",
            )
            row = (
                await db.execute(
                    select(RunDownstreamOutbox).where(
                        RunDownstreamOutbox.run_id == run_id
                    )
                )
            ).scalar_one()
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()

        async def claim_once() -> list[dict]:
            async with sessions() as db:
                claims = await claim_waiting_finalizations(db, limit=1)
                await db.commit()
                return claims

        concurrent = await asyncio.gather(claim_once(), claim_once())
        assert sorted(len(claims) for claims in concurrent) == [0, 1]
        claim = next(claims[0] for claims in concurrent if claims)
        assert claim["run_id"] == run_id
        assert claim["run_ai"] is True
    finally:
        async with sessions() as db:
            run = await db.get(DbTestRun, run_id)
            if run is not None:
                await db.delete(run)
                await db.commit()
        await engine.dispose()


async def test_durable_webhook_fanout_is_idempotent_in_postgres(monkeypatch):
    from app.services import webhook_service
    from app.worker import tasks as worker_tasks

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    scope = f"run-outbox:{uuid.uuid4()}"
    publish = Mock()
    monkeypatch.setattr(webhook_service, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", publish)
    try:
        async with sessions() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"webhook-outbox-{project_id.hex}",
                    slug=f"webhook-outbox-{project_id.hex}",
                )
            )
            db.add(
                WebhookSubscription(
                    id=subscription_id,
                    project_id=project_id,
                    name="durable run webhook",
                    target_url="https://example.com/webhook",
                    events=["run.completed"],
                    enabled=True,
                    has_secret=False,
                )
            )
            db.add(
                DbTestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"webhook-outbox-{run_id.hex}",
                    status="PASSED",
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    broken_tests=0,
                    unknown_tests=0,
                )
            )
            await db.commit()

        kwargs = {
            "project_id": project_id,
            "payload": {"run_id": str(run_id)},
            "delivery_scope": scope,
            "raise_on_persistence_error": True,
        }
        assert await webhook_service.emit_event("run.completed", **kwargs) == 1
        assert await webhook_service.emit_event("run.completed", **kwargs) == 0

        async with sessions() as db:
            deliveries = (
                await db.execute(
                    select(WebhookDelivery).where(
                        WebhookDelivery.subscription_id == subscription_id
                    )
                )
            ).scalars().all()
            assert len(deliveries) == 1
            assert deliveries[0].delivery_key is not None
            assert deliveries[0].run_id == run_id
        publish.assert_called_once()
    finally:
        async with sessions() as db:
            await _delete_projects(db, project_id)
            await db.commit()
        await engine.dispose()


async def test_stale_webhook_worker_cannot_overwrite_new_lease_in_postgres():
    from app.services.webhook_service import _transition_processing_delivery

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    stale_token = uuid.uuid4()
    current_token = uuid.uuid4()
    try:
        async with sessions() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"webhook-fence-{project_id.hex}",
                    slug=f"webhook-fence-{project_id.hex}",
                )
            )
            db.add(
                WebhookSubscription(
                    id=subscription_id,
                    project_id=project_id,
                    name="fenced webhook",
                    target_url="https://example.com/webhook",
                    events=["run.completed"],
                    enabled=True,
                    has_secret=False,
                    total_delivered=0,
                )
            )
            await db.flush()
            db.add(
                WebhookDelivery(
                    id=delivery_id,
                    subscription_id=subscription_id,
                    event_type="run.completed",
                    event_payload={"run_id": str(uuid.uuid4())},
                    status="PROCESSING",
                    dispatch_token=stale_token,
                )
            )
            await db.commit()

        # A recovery worker owns the row before the original HTTP request
        # returns, invalidating the first worker's token.
        async with sessions() as db:
            row = await db.get(WebhookDelivery, delivery_id)
            row.dispatch_token = current_token
            await db.commit()

        async with sessions() as db:
            changed = await _transition_processing_delivery(
                db,
                delivery_id=delivery_id,
                dispatch_token=stale_token,
                delivery_values={"status": "SUCCESS", "dispatch_token": None},
                subscription_id=subscription_id,
                subscription_values={
                    "total_delivered": WebhookSubscription.total_delivered + 1
                },
            )
            assert changed is False

        async with sessions() as db:
            delivery = await db.get(WebhookDelivery, delivery_id)
            subscription = await db.get(WebhookSubscription, subscription_id)
            assert delivery.status == "PROCESSING"
            assert delivery.dispatch_token == current_token
            assert subscription.total_delivered == 0
    finally:
        async with sessions() as db:
            await _delete_projects(db, project_id)
            await db.commit()
        await engine.dispose()


async def test_broker_outage_preserves_and_recovers_every_run_operation_once(
    monkeypatch,
):
    """Acceptance harness for commit-before-publish and idempotent recovery."""
    from app.services import run_downstream_outbox as outbox

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    published: list[tuple[uuid.UUID, str, uuid.UUID, dict]] = []
    effects: dict[str, int] = {}
    try:
        async with sessions() as db:
            project = Project(
                id=project_id,
                name=f"broker-loss-{project_id.hex}",
                slug=f"broker-loss-{project_id.hex}",
            )
            run = DbTestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"broker-loss-{run_id.hex}",
                status="PASSED",
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                skipped_tests=0,
                broken_tests=0,
                unknown_tests=0,
                pass_rate=100.0,
                primary_suite_name="smoke",
            )
            subscription = WebhookSubscription(
                id=subscription_id,
                project_id=project_id,
                name="broker-loss-acceptance",
                target_url="https://hooks.example.test/testlookup",
                events=["run.completed"],
                enabled=True,
                has_secret=False,
                max_retries=3,
            )
            db.add_all([project, run, subscription])
            await db.flush()
            assert (
                await outbox.stage_finalize_operations(
                    db, run=run, project=project, run_ai=True, ready=True
                )
                == 5
            )
            await db.commit()

        monkeypatch.setattr(outbox, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(
            outbox,
            "_publish_downstream",
            Mock(side_effect=ConnectionError("broker unavailable")),
        )
        failed = await outbox.relay_downstream_outbox(limit=20, max_batches=1)
        assert failed == {"claimed": 5, "published": 0, "failed": 5}

        async with sessions() as db:
            pending = await outbox.downstream_status_for_run(db, run_id=run_id)
            assert pending["status"] == "pending"
            assert len(pending["operations"]) == 5
            assert all(item["status"] == "pending" for item in pending["operations"])
            assert all(
                item["last_error"] == "broker_ConnectionError"
                for item in pending["operations"]
            )
            await db.execute(
                update(RunDownstreamOutbox)
                .where(RunDownstreamOutbox.run_id == run_id)
                .values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await db.commit()

        def _accept(row: RunDownstreamOutbox) -> None:
            published.append(
                (row.id, row.operation, row.dispatch_token, dict(row.payload))
            )

        monkeypatch.setattr(outbox, "_publish_downstream", _accept)
        recovered = await outbox.relay_downstream_outbox(limit=20, max_batches=1)
        assert recovered == {"claimed": 5, "published": 5, "failed": 0}

        # Exercise one real tracked consumer rather than simulating every
        # business handler with the counter below. The synchronous Celery task
        # runs in a helper thread while its async DB work is scheduled back on
        # this test loop, preserving asyncpg's loop affinity. Provider delivery
        # remains stubbed at the next broker boundary; the durable
        # WebhookDelivery child is real PostgreSQL state.
        from app.services import webhook_service
        from app.worker import tasks

        monkeypatch.setattr(webhook_service, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(
            webhook_service,
            "_post_allowed",
            AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            webhook_service,
            "_is_safe_public_url",
            Mock(return_value=(True, "")),
        )
        webhook_post = AsyncMock(
            return_value=SimpleNamespace(status_code=200, text="accepted")
        )

        monkeypatch.setattr(
            webhook_service,
            "get_public_http_client",
            lambda: SimpleNamespace(post=webhook_post),
        )
        monkeypatch.setattr(tasks.deliver_webhook, "delay", Mock())
        running_loop = asyncio.get_running_loop()

        def _run_on_test_loop(coro):
            return asyncio.run_coroutine_threadsafe(coro, running_loop).result()

        monkeypatch.setattr(tasks, "_run_async", _run_on_test_loop)
        webhook_publication = next(
            item for item in published if item[1] == "run_completed_webhook"
        )

        def _invoke_webhook_task():
            outbox_id, _operation, token, operation_payload = webhook_publication
            task = tasks.dispatch_run_completed_webhook
            task.push_request(
                id=f"acceptance-{outbox_id}",
                retries=0,
                headers={
                    "downstream_outbox_id": str(outbox_id),
                    "downstream_dispatch_token": str(token),
                },
            )
            try:
                return task(
                    project_id=operation_payload["project_id"],
                    payload=operation_payload["payload"],
                )
            finally:
                task.pop_request()

        assert await asyncio.to_thread(_invoke_webhook_task) == 1
        effects["run_completed_webhook"] = 1
        async with sessions() as db:
            delivery = (
                await db.execute(
                    select(WebhookDelivery).where(
                        WebhookDelivery.subscription_id == subscription_id,
                        WebhookDelivery.run_id == run_id,
                        WebhookDelivery.event_type == "run.completed",
                    )
                )
            ).scalar_one()
            assert delivery.status == "PENDING"
            assert delivery.delivery_key is not None

        assert await webhook_service.deliver(delivery.id) == {
            "status": "SUCCESS",
            "http_status": 200,
        }
        assert await webhook_service.deliver(delivery.id) == {
            "skipped": "delivery_already_terminal"
        }
        webhook_post.assert_awaited_once()

        published = [
            item for item in published if item[1] != "run_completed_webhook"
        ]

        async def _consume(
            outbox_id: uuid.UUID,
            operation: str,
            token: uuid.UUID,
            payload: dict,
        ) -> None:
            assert await outbox.begin_downstream_execution(
                outbox_id=outbox_id,
                dispatch_token=token,
                task_id=f"acceptance-{operation}",
            )
            effects[operation] = effects.get(operation, 0) + 1
            if operation == "agent_pipeline":
                child_payload = {
                    "test_run_id": str(run_id),
                    "project_id": str(project_id),
                    "build_number": payload["build_number"],
                }
                async with sessions() as db:
                    assert await outbox.stage_downstream_operation(
                        db,
                        run_id=run_id,
                        project_id=project_id,
                        operation="ai_summary_notifications",
                        input_version=outbox.operation_input_version(
                            "ai_summary_notifications", child_payload
                        ),
                        payload=child_payload,
                        queue="default",
                    )
                    await db.commit()
            assert await outbox.complete_downstream_execution(
                outbox_id=outbox_id,
                dispatch_token=token,
            )
            assert not await outbox.begin_downstream_execution(
                outbox_id=outbox_id,
                dispatch_token=token,
                task_id=f"duplicate-{operation}",
            )

        for accepted in list(published):
            await _consume(*accepted)

        published.clear()
        child_relay = await outbox.relay_downstream_outbox(limit=20, max_batches=1)
        assert child_relay == {"claimed": 1, "published": 1, "failed": 0}
        assert published[0][1] == "ai_summary_notifications"
        await _consume(*published[0])

        async with sessions() as db:
            completed = await outbox.downstream_status_for_run(db, run_id=run_id)
        assert completed["status"] == "completed"
        assert {item["operation"] for item in completed["operations"]} == {
            "run_notifications",
            "transition_notifications",
            "suite_comparison",
            "run_completed_webhook",
            "agent_pipeline",
            "ai_summary_notifications",
        }
        assert all(item["status"] == "completed" for item in completed["operations"])
        assert effects == {
            "run_notifications": 1,
            "transition_notifications": 1,
            "suite_comparison": 1,
            "run_completed_webhook": 1,
            "agent_pipeline": 1,
            "ai_summary_notifications": 1,
        }
    finally:
        async with sessions() as db:
            await _delete_projects(db, project_id)
            await db.commit()
        await engine.dispose()
