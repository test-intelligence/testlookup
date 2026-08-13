"""Durable, bounded orchestration for cluster-scoped Investigator children.

The parent owns planning and correlation. Child delivery uses a PostgreSQL
outbox and carries only an investigation UUID through Celery.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AgentChildDispatchOutbox,
    AgentInvestigation,
    AgentPipelineRun,
    AgentStageResult,
    FailureCluster,
    Project,
    TestCase,
    TestStatus,
    TestRun,
)
from app.services.agent_investigation_service import (
    AGENT_ID_INVESTIGATOR,
    HYPOTHESIS_SPECS,
    get_effective_policy,
)
from app.services.agent_planner import (
    build_cluster_investigation_plan,
    cluster_investigation_plan_integrity_status,
)
from app.services.feature_flags import is_enabled
from app.services.evidence_sanitizer import sanitize_reference_text

logger = structlog.get_logger(__name__)

FEATURE_FLAG = "cluster_investigator_children"
ACTIVE_STATUSES = ("queued", "running", "synthesizing")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
OUTBOX_LEASE_SECONDS = 300
MAX_RELAY_BATCH = 50
MAX_JOIN_SECONDS = 300


async def _commit(db: AsyncSession) -> None:
    """Commit one worker-owned orchestration transaction boundary."""
    await db.commit()


def _safe_error(exc: BaseException) -> str:
    safe, _, _ = sanitize_reference_text(
        f"{type(exc).__name__}: cluster child orchestration failed",
        limit=240,
    )
    return safe


def _as_int(value: Any, default: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, 0), maximum)


def _as_float(value: Any, default: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return min(max(number, 0.0), maximum)


def _persisted_cluster_child_settings(
    parent_pipeline: AgentPipelineRun,
    supplied_settings: dict[str, Any],
) -> dict[str, Any]:
    """Return the immutable parent snapshot or fail on caller divergence."""
    metadata = (
        parent_pipeline.execution_metadata
        if isinstance(parent_pipeline.execution_metadata, dict)
        else {}
    )
    persisted = metadata.get("cluster_child_settings")
    if not isinstance(persisted, dict):
        raise ValueError("cluster_child_settings_missing")
    if supplied_settings != persisted:
        raise ValueError("cluster_child_settings_mismatch")
    return dict(persisted)


async def resolve_cluster_child_settings(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Freeze the feature/policy gate and bounded allocation at run start."""
    flag_enabled = await is_enabled(FEATURE_FLAG, db=db, project_id=project_id)
    policy = await get_effective_policy(db, project_id, AGENT_ID_INVESTIGATOR)
    budgets = policy.get("budgets") if isinstance(policy.get("budgets"), dict) else {}
    return {
        "enabled": bool(flag_enabled and policy.get("enabled")),
        "feature_flag_enabled": bool(flag_enabled),
        "policy_enabled": bool(policy.get("enabled")),
        "mode": "shadow",
        "max_children": _as_int(
            budgets.get("max_cluster_children_per_run"), 1, 4
        ),
        "max_members": max(
            _as_int(budgets.get("max_cluster_members_per_child"), 50, 100), 1
        ),
        "max_active_per_project": _as_int(
            budgets.get("max_active_cluster_children_per_project"), 2, 20
        ),
        "max_children_per_day": _as_int(
            budgets.get("max_cluster_children_per_day"), 20, 1000
        ),
        "aggregate_budget": {
            "max_llm_calls": _as_int(
                budgets.get("max_cluster_child_llm_calls_per_parent"), 6, 100
            ),
            "max_tokens": _as_int(
                budgets.get("max_cluster_child_tokens_per_parent"), 12_000, 1_000_000
            ),
            "max_cost_usd": _as_float(
                budgets.get("max_cluster_child_cost_usd_per_parent"), 2.0, 1000.0
            ),
            "max_seconds": _as_int(
                budgets.get("max_cluster_child_seconds_per_parent"),
                180,
                MAX_JOIN_SECONDS,
            ),
        },
    }


def _pending_hypotheses() -> list[dict[str, Any]]:
    return [
        {
            "id": hypothesis_id,
            "title": title,
            "status": "pending",
            "confidence": 0,
            "confidence_basis": "heuristic_estimate",
            "summary": "",
            "evidence": [],
            "started_at": None,
            "completed_at": None,
        }
        for hypothesis_id, title in HYPOTHESIS_SPECS
    ]


def _empty_spend() -> dict[str, Any]:
    return {
        "ledger_version": 2,
        "llm_calls": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "seconds": 0.0,
        "reservations": {},
        "completed_reservations": {},
    }


async def stage_cluster_investigations(
    *,
    parent_pipeline_run_id: str,
    project_id: str,
    run_id: str,
    frozen_settings: dict[str, Any],
) -> dict[str, Any]:
    """Create/reuse selected children, parent task rows, and outbox atomically."""
    parent_id = uuid.UUID(str(parent_pipeline_run_id))
    project_uuid = uuid.UUID(str(project_id))
    run_uuid = uuid.UUID(str(run_id))
    async with AsyncSessionLocal() as db:
        await db.execute(
            select(Project.id).where(Project.id == project_uuid).with_for_update()
        )
        authority = await db.execute(
            select(AgentPipelineRun)
            .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
            .where(
                AgentPipelineRun.id == parent_id,
                AgentPipelineRun.test_run_id == run_uuid,
                AgentPipelineRun.workflow_type == "deep",
                AgentPipelineRun.spawn_depth == 0,
                TestRun.project_id == project_uuid,
            )
            .with_for_update()
        )
        parent_pipeline = authority.scalar_one_or_none()
        if parent_pipeline is None:
            raise ValueError("parent_pipeline_authority_invalid")
        frozen_settings = _persisted_cluster_child_settings(
            parent_pipeline, frozen_settings
        )
        enabled = bool(frozen_settings.get("enabled"))
        aggregate_budget = frozen_settings.get("aggregate_budget") or {}
        if _as_int(
            aggregate_budget.get("max_seconds"), 0, MAX_JOIN_SECONDS
        ) == 0:
            enabled = False
        max_children = (
            _as_int(frozen_settings.get("max_children"), 1, 4)
            if enabled
            else 0
        )
        max_members = max(
            _as_int(frozen_settings.get("max_members"), 50, 100), 1
        )

        cluster_rows = list((await db.execute(
            select(FailureCluster)
            .where(
                FailureCluster.test_run_id == run_uuid,
                FailureCluster.pipeline_run_id == parent_id,
            )
            .order_by(FailureCluster.cluster_id, FailureCluster.id)
            .limit(501)
        )).scalars().all())
        if len(cluster_rows) > 500:
            raise ValueError("failure_cluster_scan_limit_exceeded")
        candidates = [
            {
                "failure_cluster_id": str(row.id),
                "cluster_id": row.cluster_id,
                "member_test_ids": list(row.member_test_ids or []),
            }
            for row in cluster_rows
        ]
        candidate_members = {
            str(uuid.UUID(str(member)))
            for candidate in candidates
            for member in candidate["member_test_ids"]
        }
        authorized_members = {
            str(item)
            for item in (
                await db.execute(
                    select(TestCase.id).where(
                        TestCase.test_run_id == run_uuid,
                        TestCase.id.in_([
                            uuid.UUID(member) for member in candidate_members
                        ]),
                        TestCase.status.in_(
                            (TestStatus.FAILED, TestStatus.BROKEN)
                        ),
                    )
                )
            ).scalars().all()
        }
        if authorized_members != candidate_members:
            raise ValueError("cluster_member_authority_invalid")
        plan = build_cluster_investigation_plan(
            parent_pipeline_run_id=parent_id,
            project_id=project_uuid,
            run_id=run_uuid,
            clusters=candidates,
            aggregate_budget=aggregate_budget,
            max_children=max_children,
            max_members=max_members,
        )
        integrity_status, actual_hash = (
            cluster_investigation_plan_integrity_status(plan)
        )
        if (
            integrity_status != "verified"
            or actual_hash != plan.get("expansion_sha256")
        ):
            raise ValueError("cluster_plan_integrity_failed")

        midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        active_count = int((await db.execute(
            select(func.count(AgentInvestigation.id)).where(
                AgentInvestigation.project_id == project_uuid,
                AgentInvestigation.scope_type == "failure_cluster",
                AgentInvestigation.status.in_(ACTIVE_STATUSES),
            )
        )).scalar() or 0)
        daily_count = int((await db.execute(
            select(func.count(AgentInvestigation.id)).where(
                AgentInvestigation.project_id == project_uuid,
                AgentInvestigation.scope_type == "failure_cluster",
                AgentInvestigation.created_at >= midnight,
            )
        )).scalar() or 0)
        capacity = min(
            max(_as_int(frozen_settings.get("max_active_per_project"), 2, 20) - active_count, 0),
            max(_as_int(frozen_settings.get("max_children_per_day"), 20, 1000) - daily_count, 0),
        )

        dispatched: list[dict[str, Any]] = []
        capacity_skips: list[dict[str, Any]] = []
        new_children = 0
        for task in plan["selected"]:
            existing = (await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.spawn_key == task["spawn_key"]
                )
            )).scalar_one_or_none()
            if existing is None and new_children >= capacity:
                skip_result = {
                    "failure_cluster_id": task["failure_cluster_id"],
                    "task_key": task["task_key"],
                    "spawn_key": task["spawn_key"],
                    "skip_reason": "project_child_capacity_exhausted",
                    "rationale": "project active or daily child budget exhausted",
                }
                capacity_skips.append(skip_result)
                stage = (await db.execute(
                    select(AgentStageResult).where(
                        AgentStageResult.pipeline_run_id == parent_id,
                        AgentStageResult.task_key == task["task_key"],
                    )
                )).scalar_one_or_none()
                if stage is None:
                    stage = AgentStageResult(
                        pipeline_run_id=parent_id,
                        task_key=task["task_key"],
                        capability_id=task["capability_id"],
                        parent_task_key="cluster_investigation_dispatch",
                        failure_cluster_id=uuid.UUID(
                            task["failure_cluster_id"]
                        ),
                        selected=False,
                        required=False,
                        dependencies=list(task["dependencies"]),
                        allocated_budget=dict(task["budget"]),
                        idempotency_key=task["spawn_key"],
                        stage_name="cluster_investigation",
                        status="skipped",
                        skipped_reason="project_child_capacity_exhausted",
                        stop_reason="project_child_capacity_exhausted",
                        completed_at=datetime.now(timezone.utc),
                        route_rationale=skip_result["rationale"],
                        result_data=skip_result,
                    )
                    db.add(stage)
                continue
            if existing is None:
                investigation = AgentInvestigation(
                    project_id=project_uuid,
                    run_id=run_uuid,
                    scope_type="failure_cluster",
                    failure_cluster_id=uuid.UUID(task["failure_cluster_id"]),
                    cluster_scope_sha256=task["cluster_scope_sha256"],
                    cluster_member_test_ids=task["member_test_ids"],
                    parent_pipeline_run_id=parent_id,
                    parent_task_id=(
                        f"pipeline:{parent_id}:task:{task['task_key']}"
                    ),
                    spawn_lineage_id=parent_id,
                    spawn_depth=1,
                    spawn_key=task["spawn_key"],
                    selection_reason=task["rationale"],
                    status="queued",
                    mode="shadow",
                    triggered_by="auto:cluster_child",
                    budget=dict(task["budget"]),
                    spend=_empty_spend(),
                    hypotheses=_pending_hypotheses(),
                )
                db.add(investigation)
                await db.flush()
                db.add(AgentChildDispatchOutbox(
                    spawn_key=task["spawn_key"],
                    investigation_id=investigation.id,
                    parent_pipeline_run_id=parent_id,
                    project_id=project_uuid,
                    run_id=run_uuid,
                    status="pending",
                ))
                new_children += 1
            else:
                investigation = existing
                if (
                    investigation.parent_pipeline_run_id != parent_id
                    or investigation.failure_cluster_id
                    != uuid.UUID(task["failure_cluster_id"])
                    or investigation.cluster_scope_sha256
                    != task["cluster_scope_sha256"]
                    or investigation.spawn_depth != 1
                ):
                    raise ValueError("spawn_key_authority_conflict")

            stage = (await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == parent_id,
                    AgentStageResult.task_key == task["task_key"],
                )
            )).scalar_one_or_none()
            if stage is None:
                db.add(AgentStageResult(
                    pipeline_run_id=parent_id,
                    task_key=task["task_key"],
                    capability_id=task["capability_id"],
                    parent_task_key="cluster_investigation_dispatch",
                    failure_cluster_id=uuid.UUID(task["failure_cluster_id"]),
                    selected=True,
                    required=False,
                    dependencies=list(task["dependencies"]),
                    allocated_budget=dict(task["budget"]),
                    idempotency_key=task["spawn_key"],
                    stage_name="cluster_investigation",
                    status=(
                        "completed"
                        if investigation.status == "completed"
                        else "pending"
                    ),
                    route_rationale=task["rationale"],
                ))
            elif stage.selected is False and stage.status == "skipped":
                stage.selected = True
                stage.status = (
                    "completed"
                    if investigation.status == "completed"
                    else "pending"
                )
                stage.skipped_reason = None
                stage.stop_reason = None
                stage.completed_at = None
                stage.route_rationale = task["rationale"]
                stage.result_data = None
            dispatched.append({
                **task,
                "investigation_id": str(investigation.id),
                "status": investigation.status,
            })

        await _commit(db)
        return {
            "schema_version": 1,
            "status": (
                "capacity_degraded" if capacity_skips else "dispatched"
            ),
            "planner_plan": plan,
            "planner_sha256": plan["expansion_sha256"],
            "selected_count": int(plan["selected_count"]),
            "dispatched_count": len(dispatched),
            "dispatch_children": dispatched,
            "dispatch_capacity_skips": capacity_skips,
        }


async def relay_child_dispatch_outbox(
    *, parent_pipeline_run_id: str | None = None
) -> dict[str, int]:
    """Publish a bounded batch; stale sending rows are retryable."""
    now = datetime.now(timezone.utc)
    sent_recovery_cutoff = now - timedelta(seconds=OUTBOX_LEASE_SECONDS)
    claimed: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    async with AsyncSessionLocal() as db:
        predicates = [
            or_(
                AgentChildDispatchOutbox.status == "pending",
                and_(
                    AgentChildDispatchOutbox.status == "sending",
                    AgentChildDispatchOutbox.next_attempt_at <= now,
                ),
                and_(
                    AgentChildDispatchOutbox.status == "sent",
                    AgentChildDispatchOutbox.sent_at <= sent_recovery_cutoff,
                    AgentInvestigation.status == "queued",
                ),
            ),
            or_(
                AgentChildDispatchOutbox.next_attempt_at.is_(None),
                AgentChildDispatchOutbox.next_attempt_at <= now,
            ),
        ]
        if parent_pipeline_run_id:
            predicates.append(
                AgentChildDispatchOutbox.parent_pipeline_run_id
                == uuid.UUID(str(parent_pipeline_run_id))
            )
        rows = list((await db.execute(
            select(AgentChildDispatchOutbox)
            .join(
                AgentInvestigation,
                AgentInvestigation.id
                == AgentChildDispatchOutbox.investigation_id,
            )
            .where(*predicates)
            .order_by(
                AgentChildDispatchOutbox.created_at,
                AgentChildDispatchOutbox.id,
            )
            .with_for_update(skip_locked=True)
            .limit(MAX_RELAY_BATCH)
        )).scalars().all())
        for row in rows:
            row.status = "sending"
            row.attempts = int(row.attempts or 0) + 1
            row.next_attempt_at = now + timedelta(seconds=OUTBOX_LEASE_SECONDS)
            row.sent_at = None
            row.last_error = None
            claimed.append((row.id, row.investigation_id, row.spawn_key))
        await _commit(db)

    sent = failed = 0
    from app.worker.tasks import run_agent_child_investigation

    for outbox_id, investigation_id, spawn_key in claimed:
        try:
            run_agent_child_investigation.apply_async(
                args=[str(investigation_id)],
                queue="agent_children",
                task_id=f"cluster-child-{spawn_key}",
            )
            async with AsyncSessionLocal() as db:
                row = (await db.execute(
                    select(AgentChildDispatchOutbox)
                    .where(AgentChildDispatchOutbox.id == outbox_id)
                    .with_for_update()
                )).scalar_one_or_none()
                if row is not None and row.status == "sending":
                    row.status = "sent"
                    row.sent_at = datetime.now(timezone.utc)
                    row.next_attempt_at = None
                    await _commit(db)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            async with AsyncSessionLocal() as db:
                observed = (await db.execute(
                    select(AgentChildDispatchOutbox)
                    .where(AgentChildDispatchOutbox.id == outbox_id)
                )).scalar_one_or_none()
                investigation = None
                if observed is not None:
                    # Match timeout/cancel lock order: investigation -> outbox.
                    investigation = (await db.execute(
                        select(AgentInvestigation)
                        .where(
                            AgentInvestigation.id == observed.investigation_id
                        )
                        .with_for_update()
                    )).scalar_one_or_none()
                row = (await db.execute(
                    select(AgentChildDispatchOutbox)
                    .where(AgentChildDispatchOutbox.id == outbox_id)
                    .with_for_update()
                )).scalar_one_or_none()
                if row is not None and row.status == "sending":
                    permanently_failed = row.attempts >= 10
                    row.status = "failed" if permanently_failed else "pending"
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(2 ** min(row.attempts, 8), 300)
                    )
                    row.last_error = _safe_error(exc)
                    if permanently_failed:
                        row.next_attempt_at = None
                        if (
                            investigation is not None
                            and investigation.status == "queued"
                        ):
                            investigation.status = "failed"
                            investigation.completed_at = datetime.now(
                                timezone.utc
                            )
                            investigation.error = (
                                "cluster_child_dispatch_exhausted"
                            )
                            task_key = str(
                                investigation.parent_task_id or ""
                            ).split(":task:", 1)[-1]
                            stage = (await db.execute(
                                select(AgentStageResult).where(
                                    AgentStageResult.pipeline_run_id
                                    == row.parent_pipeline_run_id,
                                    AgentStageResult.task_key == task_key,
                                )
                            )).scalar_one_or_none()
                            if stage is not None:
                                stage.status = "failed"
                                stage.stop_reason = (
                                    "cluster_child_dispatch_exhausted"
                                )
                                stage.completed_at = datetime.now(
                                    timezone.utc
                                )
                    await _commit(db)
            logger.warning(
                "cluster_child_dispatch_failed", error_type=type(exc).__name__
            )
    return {"claimed": len(claimed), "sent": sent, "failed": failed}


async def wait_for_cluster_children(
    *,
    parent_pipeline_run_id: str,
    timeout_seconds: int,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    """Bounded join; timeout cooperatively cancels unfinished children."""
    parent_id = uuid.UUID(str(parent_pipeline_run_id))
    timeout = min(max(int(timeout_seconds), 0), MAX_JOIN_SECONDS)
    await relay_child_dispatch_outbox(parent_pipeline_run_id=str(parent_id))
    deadline = asyncio.get_running_loop().time() + timeout
    rows: list[AgentInvestigation] = []
    while True:
        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(
                select(AgentInvestigation)
                .where(
                    AgentInvestigation.parent_pipeline_run_id == parent_id,
                    AgentInvestigation.scope_type == "failure_cluster",
                )
                .order_by(
                    AgentInvestigation.failure_cluster_id,
                    AgentInvestigation.id,
                )
                .limit(20)
            )).scalars().all())
        if not rows or all(row.status in TERMINAL_STATUSES for row in rows):
            break
        if asyncio.get_running_loop().time() >= deadline:
            async with AsyncSessionLocal() as db:
                timed_out = list((await db.execute(
                    select(AgentInvestigation)
                    .where(
                        AgentInvestigation.parent_pipeline_run_id == parent_id,
                        AgentInvestigation.scope_type == "failure_cluster",
                        AgentInvestigation.status.in_(ACTIVE_STATUSES),
                    )
                    .with_for_update()
                )).scalars().all())
                queued_ids: list[uuid.UUID] = []
                for row in timed_out:
                    if row.status == "queued":
                        row.status = "cancelled"
                        row.completed_at = datetime.now(timezone.utc)
                        row.error = None
                        queued_ids.append(row.id)
                    else:
                        row.cancel_requested = True
                    row.cancelled_by = "parent_join_timeout"
                if queued_ids:
                    outbox_rows = list((await db.execute(
                        select(AgentChildDispatchOutbox).where(
                            AgentChildDispatchOutbox.investigation_id.in_(
                                queued_ids
                            ),
                            AgentChildDispatchOutbox.status.in_(
                                ("pending", "sending", "sent")
                            ),
                        ).with_for_update()
                    )).scalars().all())
                    for outbox in outbox_rows:
                        outbox.status = "failed"
                        outbox.next_attempt_at = None
                        outbox.last_error = "parent_join_timeout"
                await _commit(db)
            break
        await asyncio.sleep(max(min(poll_seconds, 5.0), 0.05))

    children: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(
            select(AgentInvestigation)
            .where(
                AgentInvestigation.parent_pipeline_run_id == parent_id,
                AgentInvestigation.scope_type == "failure_cluster",
            )
            .order_by(
                AgentInvestigation.failure_cluster_id,
                AgentInvestigation.id,
            )
            .limit(20)
        )).scalars().all())
        for row in rows:
            terminal = row.status in TERMINAL_STATUSES
            stop_reason = (
                "cluster_child_join_timeout"
                if getattr(row, "cancelled_by", None) == "parent_join_timeout"
                else (
                    "cluster_child_dispatch_exhausted"
                    if getattr(row, "error", None)
                    == "cluster_child_dispatch_exhausted"
                    else (
                        None
                        if terminal and row.status == "completed"
                        else (
                            "cluster_child_join_timeout"
                            if not terminal
                            else f"cluster_child_{row.status}"
                        )
                    )
                )
            )
            stage = (await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == parent_id,
                    AgentStageResult.task_key
                    == str(row.parent_task_id).split(":task:", 1)[-1],
                )
            )).scalar_one_or_none()
            child_pipeline_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"testlookup:investigation:{row.id}"
            )
            result_data = {
                "investigation_id": str(row.id),
                "child_pipeline_run_id": str(child_pipeline_id),
                "failure_cluster_id": str(row.failure_cluster_id),
                "cluster_scope_sha256": row.cluster_scope_sha256,
                "status": row.status,
                "stop_reason": stop_reason,
                "spend": dict(row.spend or {}),
                "verdict": dict(row.verdict or {}),
            }
            if stage is not None:
                stage.status = (
                    "completed"
                    if row.status == "completed"
                    else (
                        "cancelled"
                        if row.status == "cancelled"
                        else ("failed" if terminal else "partial")
                    )
                )
                stage.stop_reason = stop_reason
                stage.completed_at = datetime.now(timezone.utc)
                stage.result_data = result_data
            children.append(result_data)
        await _commit(db)
    completed = sum(item["status"] == "completed" for item in children)
    degraded = any(item["status"] != "completed" for item in children)
    return {
        "status": "degraded" if degraded else "complete",
        "selected_count": len(children),
        "completed_count": completed,
        "children": children,
        "stop_reasons": sorted({
            item["stop_reason"]
            for item in children
            if item.get("stop_reason")
        }),
    }


async def cancel_cluster_children(
    parent_pipeline_run_id: str,
    *,
    reason: str,
) -> int:
    """Stop undispatched work and cooperatively cancel active descendants."""
    parent_id = uuid.UUID(str(parent_pipeline_run_id))
    safe_reason = sanitize_reference_text(reason, limit=120)[0]
    async with AsyncSessionLocal() as db:
        investigations = list((await db.execute(
            select(AgentInvestigation)
            .where(
                AgentInvestigation.parent_pipeline_run_id == parent_id,
                AgentInvestigation.scope_type == "failure_cluster",
                AgentInvestigation.status.in_(ACTIVE_STATUSES),
            )
            .with_for_update()
            .limit(20)
        )).scalars().all())
        for row in investigations:
            if row.status == "queued":
                row.status = "cancelled"
                row.completed_at = datetime.now(timezone.utc)
                row.error = None
            else:
                row.cancel_requested = True
            row.cancelled_by = safe_reason
        outbox_rows = list((await db.execute(
            select(AgentChildDispatchOutbox)
            .where(
                AgentChildDispatchOutbox.parent_pipeline_run_id == parent_id,
                AgentChildDispatchOutbox.status.in_(("pending", "sending")),
            )
            .with_for_update()
            .limit(MAX_RELAY_BATCH)
        )).scalars().all())
        for row in outbox_rows:
            row.status = "failed"
            row.next_attempt_at = None
            row.last_error = safe_reason
        await _commit(db)
        return len(investigations)
