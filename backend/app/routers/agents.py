"""
Agent pipeline management endpoints.

Provides visibility into running/completed pipelines and allows manual triggering.
"""
import asyncio
import inspect
import logging
import uuid
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.workflow_run_state import PUBLIC_STATUS, normalize_status, public_status
from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import (
    AgentInvestigation,
    AgentPipelineRun,
    AgentStageResult,
    Project,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import (
    AgentPipelineResponse,
    AgentRunSummaryResponse,
    PipelineEventLogHealthResponse,
    PipelineReplayResponse,
    PipelineTimelineResponse,
    PipelineTimelineSummary,
    PipelineTimelineEventResponse,
    TriggerPipelineRequest,
)
from app.models.agentic_runtime import AgenticRunV1
from app.services import runs_service
from app.services.run_summary_service import build_fallback_summary, normalize_summary_doc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"])
def _status_filter_values(status: str) -> list[str]:
    """Map a ``?status=`` filter to stored values. Public names expand to
    their internal set; internal (and legacy ``partial``) names pass through
    normalisation so a stale client keeps getting rows, not an empty page."""
    wanted = str(status or "").strip().lower()
    public = [s.value for s, pub in PUBLIC_STATUS.items() if pub == wanted]
    if public:
        return public
    return [normalize_status(wanted).value]


def _attach_public_status(pipelines: list[AgentPipelineRun]) -> None:
    """Stamp the four-value public projection onto each ORM row so the
    response serialiser (``from_attributes``) picks it up. Runs AFTER the
    effective-status derivation so a derived ``failed`` projects as failed."""
    for p in pipelines:
        p.public_status = public_status(p.status)  # type: ignore[attr-defined]
async def _attach_run_context(
    db: AsyncSession, pipelines: list[AgentPipelineRun],
) -> None:
    """Attach owning-run context (build number, Run #N, suite) to each pipeline.

    The /agents cards otherwise show only a workflow type — users can't tell
    which run or suite a pipeline analysed. We set non-mapped attributes on the
    ORM rows in place; the
    ``from_attributes`` response model reads them straight off. ``run_seq`` uses
    the same per-(project, primary_suite_name) numbering as /runs and /live so
    "Run #N" matches everywhere. All fields stay None for legacy rows whose
    TestRun is missing. The TestRun lookup is bounded to the ids of the
    already-tenant-scoped pipelines, so it adds no cross-tenant exposure.
    """
    run_ids = list({p.test_run_id for p in pipelines})
    if not run_ids:
        return
    rows = (
        await db.execute(
            select(TestRun.id, TestRun.build_number, TestRun.primary_suite_name)
            .where(TestRun.id.in_(run_ids))
        )
    ).all()
    ctx = {r.id: r for r in rows}
    seq_map = await runs_service.fetch_run_seq_map(db, run_ids)
    for p in pipelines:
        row = ctx.get(p.test_run_id)
        p.build_number = row.build_number if row else None
        p.suite_name = row.primary_suite_name if row else None
        p.run_seq = seq_map.get(str(p.test_run_id))


async def _resolve_maybe_awaitable(value: Any) -> Any:
    """Support both async collaborators and synchronous test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _load_pipeline_or_404(
    db: AsyncSession, pipeline_id: uuid.UUID,
) -> AgentPipelineRun:
    pipeline = (
        await db.execute(
            select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_id)
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(404, detail="Pipeline run not found")
    return pipeline


async def _require_pipeline_access(
    db: AsyncSession, current_user: Any, pipeline: AgentPipelineRun,
) -> None:
    """Tenant gate for a single pipeline.

    Pipelines carry no ``project_id`` of their own — access is derived from the
    owning test run's project. Without this check every ``/pipelines/{id}/*``
    read endpoint is a cross-tenant IDOR (status, stages, cost, decision
    timeline, replay) since the only other guard is ``get_current_active_user``.
    Mirrors the ``require_run_access`` chain used by the ``/runs/{id}/*`` siblings.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is None:  # admin → all projects
        return
    run = await db.get(TestRun, pipeline.test_run_id)
    if run is None or run.project_id not in accessible:
        raise HTTPException(
            403, detail="You do not have access to this pipeline run"
        )


@router.get("/pipelines", response_model=list[AgentPipelineResponse])
async def list_pipelines(
    run_id: Optional[uuid.UUID] = Query(None),
    project_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """List agent pipeline runs, optionally filtered by test run, project, or status."""
    # Tenant scope: non-admins only ever see pipelines whose run belongs to a
    # project they can access. A caller-supplied ``project_id`` they don't have
    # is rejected outright rather than silently returning an empty list.
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and project_id is not None and project_id not in accessible:
        raise HTTPException(403, detail="You do not have access to this project")

    q = select(AgentPipelineRun)
    if run_id:
        q = q.where(AgentPipelineRun.test_run_id == run_id)
    # The join and life-cycle filter are UNCONDITIONAL: previously both lived
    # inside the `project_id is not None or accessible is not None` branch, so
    # an ADMIN with no project pinned got neither. Measured live, 861 of 874
    # pipeline runs belonged to deleted projects, and with the default
    # ``limit=20`` the 13 real ones were crowded out entirely.
    q = q.join(TestRun, AgentPipelineRun.test_run_id == TestRun.id).where(
        TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True)))
    )
    if project_id is not None:
        q = q.where(TestRun.project_id == project_id)
    if accessible is not None:
        q = q.where(TestRun.project_id.in_(accessible))
    # The filter and the response agree: there is no derived status (E7.3),
    # so ``?status=running`` returns exactly the rows stored as running.
    if status:
        # Accept both the internal vocabulary and the public one (E7.1):
        # ``?status=in_progress`` is every non-terminal internal state.
        q = q.where(AgentPipelineRun.status.in_(_status_filter_values(status)))
    q = q.order_by(AgentPipelineRun.created_at.desc()).limit(limit)

    result = await db.execute(q)
    pipelines = list(result.scalars().all())

    # E7.3: no read-time status derivation. The stored status IS the status.
    # A worker that stops heartbeating has its row reclaimed by the reaper
    # within one lease; a failed stage raises inside the node, so the
    # pipeline's own error handler terminalises the run. The old override
    # guessed from a fixed 30-minute age and disagreed with the database in
    # between -- the same row read as running in SQL and failed in the API.
    _attach_public_status(pipelines)
    await _attach_run_context(db, pipelines)
    return pipelines


@router.get("/pipelines/{pipeline_id}", response_model=AgentPipelineResponse)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Get a single pipeline run with all stage results."""
    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)

    _attach_public_status([pipeline])
    await _attach_run_context(db, [pipeline])
    return pipeline


@router.post("/pipelines/trigger", status_code=202)
async def trigger_pipeline(
    payload: TriggerPipelineRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Manually trigger the agent pipeline for an existing test run.
    Returns 202 Accepted — pipeline runs asynchronously via Celery.
    """
    # Verify the run exists AND that the caller may act on its project.
    # ``require_role(QA_ENGINEER)`` gates by role, never by membership, so
    # without this a QA engineer of one project could start the agent pipeline
    # on another project's run -- spending that tenant's LLM budget and writing
    # agent results into their project.
    run_result = await db.execute(select(TestRun).where(TestRun.id == payload.test_run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, detail="TestRun not found")
    await resolve_project_scope(db, current_user, str(run.project_id))

    # E7.2: the state machine, not the Redis admission lock, decides whether a
    # new trigger is accepted. A run that is pending / running / retry_wait is
    # returned as-is (200) instead of queueing a second concurrent pipeline.
    existing = await _latest_in_progress_pipeline(db, run.id, "offline")
    if existing is not None:
        return JSONResponse(
            status_code=200,
            content={
                "message": "Pipeline already in progress",
                "pipeline_run_id": str(existing.id),
                "status": existing.status,
                "public_status": public_status(existing.status),
                "attempt": existing.attempt,
                "next_retry_at": existing.next_retry_at.isoformat() if existing.next_retry_at else None,
                "run_id": str(run.id),
            },
        )

    from app.worker.tasks import run_agent_pipeline
    task = run_agent_pipeline.delay(
        test_run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number,
        workflow_type="offline",
    )

    return {"message": "Pipeline queued", "task_id": task.id, "run_id": str(run.id)}


async def _latest_in_progress_pipeline(
    db: AsyncSession, test_run_id: uuid.UUID, workflow_type: str
) -> Optional[AgentPipelineRun]:
    """The newest non-terminal pipeline for (run, workflow), or None."""
    in_progress = [s.value for s, pub in PUBLIC_STATUS.items() if pub == "in_progress"]
    result = await db.execute(
        select(AgentPipelineRun)
        .where(
            AgentPipelineRun.test_run_id == test_run_id,
            AgentPipelineRun.workflow_type == workflow_type,
            AgentPipelineRun.status.in_(in_progress),
        )
        .order_by(AgentPipelineRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Bulk trigger ────────────────────────────────────────────────────────────

class BulkTriggerRequest(BaseModel):
    run_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=2000)
    workflow_type: Literal["offline", "deep"] = "offline"


class BulkTriggerResponse(BaseModel):
    queued: int
    not_found: int
    workflow_type: str
    not_found_ids: List[str] = Field(default_factory=list)


@router.post("/pipelines/bulk-trigger", response_model=BulkTriggerResponse, status_code=202)
async def bulk_trigger_pipelines(
    payload: BulkTriggerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Queue the agent pipeline for many runs in one HTTP call.

    Backs the /runs bulk-action UI ("Trigger all FAILED", multi-row select).
    Replaces the previous client-side fan-out — for a 500-run trigger this
    is one round-trip instead of 500. Tasks are queued in batches of 25 with
    a tiny inter-batch sleep so the Celery broker isn't slammed in a single
    burst. Celery's existing dedup on (run_id, workflow_type) for 7200s
    handles re-triggers gracefully, so this endpoint doesn't need to dedup
    itself — repeats just resolve to ``{duplicate: true}`` per task.
    """
    from app.worker.tasks import run_agent_pipeline

    # One DB roundtrip to resolve every run + reject unknown ids.
    # Returning the not-found list lets the UI tell the user *which* IDs
    # were skipped vs which actually queued.
    # Scope BEFORE fanning out. The single-run sibling above checks membership;
    # this one accepted up to 2000 ids and filtered on none of them, so one call
    # could queue pipelines across every project on the deployment. A run the
    # caller cannot access is reported as not-found rather than as forbidden --
    # the response already carries a not_found list, and distinguishing the two
    # would turn it into an existence oracle.
    accessible = await get_accessible_project_ids(db, current_user)
    stmt = select(TestRun.id, TestRun.project_id, TestRun.build_number).where(
        TestRun.id.in_(payload.run_ids)
    )
    if accessible is not None:
        stmt = stmt.where(TestRun.project_id.in_(accessible))
    result = await db.execute(stmt)
    found = list(result.all())
    found_ids = {row[0] for row in found}
    not_found_ids = [str(rid) for rid in payload.run_ids if rid not in found_ids]

    BATCH_SIZE = 25
    INTER_BATCH_SLEEP_S = 0.05  # 50ms between bursts; barely noticeable, gentle on broker
    queued = 0

    for i in range(0, len(found), BATCH_SIZE):
        batch = found[i : i + BATCH_SIZE]
        for run_id, project_id, build_number in batch:
            try:
                run_agent_pipeline.apply_async(
                    kwargs={
                        "test_run_id": str(run_id),
                        "project_id": str(project_id),
                        "build_number": build_number,
                        "workflow_type": payload.workflow_type,
                    },
                    queue="ai_analysis",
                )
                queued += 1
            except Exception as exc:  # pragma: no cover - broker errors are exceptional
                logger.warning(
                    "bulk_trigger: queue failed for run %s: %s", run_id, exc,
                )
        if i + BATCH_SIZE < len(found):
            await asyncio.sleep(INTER_BATCH_SLEEP_S)

    logger.info(
        "bulk_trigger_pipelines: queued=%d not_found=%d workflow=%s",
        queued, len(not_found_ids), payload.workflow_type,
    )

    return BulkTriggerResponse(
        queued=queued,
        not_found=len(not_found_ids),
        workflow_type=payload.workflow_type,
        not_found_ids=not_found_ids[:25],  # cap echo to keep response small
    )


@router.get("/pipelines/{pipeline_id}/stages", response_model=list[dict])
async def get_pipeline_stages(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Get detailed stage results for a pipeline run."""
    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)
    result = await db.execute(
        select(AgentStageResult)
        .where(AgentStageResult.pipeline_run_id == pipeline_id)
        .order_by(AgentStageResult.started_at)
    )
    stages = result.scalars().all()
    return [
        {
            "stage_name": s.stage_name,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "result_data": s.result_data,
            "error": s.error,
            # Phase 6: Observability fields
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "total_tokens": s.total_tokens,
            "llm_calls_count": s.llm_calls_count,
            "cost_usd": s.cost_usd,
            "error_category": s.error_category,
            "fallback_used": s.fallback_used,
            "confidence_score": s.confidence_score,
            "evidence_count": s.evidence_count,
            "route_rationale": s.route_rationale,
        }
        for s in stages
    ]


@router.get("/pipelines/{pipeline_id}/timeline", response_model=PipelineTimelineResponse)
async def get_pipeline_timeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """
    Get the full agent timeline with per-stage observability data:
    tokens, cost, latency, confidence, evidence count, route rationale,
    error taxonomy, fallback status, and alerts.
    """
    from app.services.agent_cost_service import (
        build_agent_observability_summary,
        check_alerts,
        get_pipeline_cost_summary,
    )
    from app.services.pipeline_event_log import get_pipeline_timeline as get_pipeline_events

    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)

    cost_summary = await _resolve_maybe_awaitable(
        get_pipeline_cost_summary(db, str(pipeline_id))
    )
    alerts = await _resolve_maybe_awaitable(check_alerts(db, str(pipeline_id)))
    stage_result = await db.execute(
        select(AgentStageResult)
        .where(AgentStageResult.pipeline_run_id == pipeline_id)
        .order_by(AgentStageResult.started_at, AgentStageResult.stage_name)
    )
    stages = stage_result.scalars().all()
    events = await _resolve_maybe_awaitable(get_pipeline_events(str(pipeline_id)))
    from app.services.pipeline_replay_service import build_replay_integrity_summary

    replay_integrity = build_replay_integrity_summary(pipeline, list(stages), list(events))
    agent_observability = build_agent_observability_summary(
        list(stages),
        cost_summary=cost_summary,
        alerts=alerts,
    )

    # Pipeline-level timing
    pipeline_duration = None
    if pipeline.started_at and pipeline.completed_at:
        pipeline_duration = round(
            (pipeline.completed_at - pipeline.started_at).total_seconds(), 2
        )

    completed = sum(1 for s in stages if s.status == "completed")
    running = sum(1 for s in stages if s.status == "running")
    failed = sum(1 for s in stages if s.status == "failed")
    skipped = sum(1 for s in stages if s.status == "skipped")
    pending = sum(1 for s in stages if s.status == "pending")
    progress = round(((completed + failed + skipped) / len(stages)) * 100, 1) if stages else 0.0

    return {
        "schema_version": 2,
        "pipeline_run_id": str(pipeline_id),
        "workflow_type": pipeline.workflow_type,
        "status": pipeline.status,
        "started_at": pipeline.started_at.isoformat() if pipeline.started_at else None,
        "completed_at": pipeline.completed_at.isoformat() if pipeline.completed_at else None,
        "duration_seconds": pipeline_duration,
        "cost_summary": cost_summary,
        "agent_observability": agent_observability,
        "alerts": alerts,
        "stages": [
            {
                "stage_name": s.stage_name,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "result_data": s.result_data,
                "error": s.error,
                "skipped_reason": s.skipped_reason,
                "execution_path": s.execution_path,
                "fallback_used": s.fallback_used,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "total_tokens": s.total_tokens,
                "llm_calls_count": s.llm_calls_count,
                "cost_usd": s.cost_usd,
                "error_category": s.error_category,
                "confidence_score": s.confidence_score,
                "evidence_count": s.evidence_count,
                "route_rationale": s.route_rationale,
            }
            for s in stages
        ],
        "events": [
            PipelineTimelineEventResponse(
                event_type=event.get("event_type", "unknown"),
                stage_name=event.get("stage_name"),
                test_case_id=event.get("test_case_id"),
                timestamp=event.get("timestamp"),
                detail=event.get("detail") or {},
            ).model_dump(mode="json")
            for event in events
        ],
        "summary": PipelineTimelineSummary(
            total_stages=len(stages),
            completed_stages=completed,
            running_stages=running,
            failed_stages=failed,
            skipped_stages=skipped,
            pending_stages=pending,
            progress_percent=progress,
        ).model_dump(mode="json"),
        "replay_integrity": replay_integrity,
    }


@router.get("/pipelines/{pipeline_id}/agentic-runtime", response_model=AgenticRunV1)
async def get_agentic_runtime(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Return the versioned unified-runtime projection for one pipeline.

    This additive adapter keeps the established timeline API stable while
    exposing selected/skipped capabilities, parent-child task identity,
    allocated budgets, actual usage and terminal stop reasons in one model.
    """
    from app.services.agentic_runtime_service import (
        MAX_RUNTIME_CHILDREN,
        MAX_RUNTIME_TASKS,
        build_recursive_agentic_run_projection,
    )

    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)
    result = await db.execute(
        select(AgentStageResult)
        .where(AgentStageResult.pipeline_run_id == pipeline_id)
        .order_by(AgentStageResult.started_at, AgentStageResult.stage_name)
        .limit(201)
    )
    parent_stages = list(result.scalars().all())

    # Parent-linked investigations are additive: until the cluster-orchestration
    # migration adds the mapped column this endpoint preserves its legacy shape.
    child_investigations: list[Any] = []
    parent_link = getattr(AgentInvestigation, "parent_pipeline_run_id", None)
    if parent_link is not None:
        child_result = await db.execute(
            select(AgentInvestigation)
            .join(TestRun, TestRun.id == AgentInvestigation.run_id)
            .where(parent_link == pipeline_id)
            .where(
                AgentInvestigation.run_id == pipeline.test_run_id,
                AgentInvestigation.project_id == TestRun.project_id,
            )
            .order_by(AgentInvestigation.created_at, AgentInvestigation.id)
            .limit(MAX_RUNTIME_CHILDREN + 1)
        )
        child_investigations = list(child_result.scalars().all())

    child_ids_by_investigation: dict[str, uuid.UUID] = {}
    for investigation in child_investigations:
        if str(getattr(investigation, "run_id", "")) != str(pipeline.test_run_id):
            continue
        explicit_id = (
            getattr(investigation, "child_pipeline_run_id", None)
            or getattr(investigation, "pipeline_run_id", None)
        )
        try:
            child_pipeline_id = (
                uuid.UUID(str(explicit_id)) if explicit_id
                else uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"testlookup:investigation:{investigation.id}",
                )
            )
        except (TypeError, ValueError, AttributeError):
            continue
        child_ids_by_investigation[str(investigation.id)] = child_pipeline_id

    child_pipelines: dict[uuid.UUID, Any] = {}
    stages_by_pipeline: dict[uuid.UUID, list[Any]] = {}
    pipeline_ids = list(dict.fromkeys(child_ids_by_investigation.values()))
    if pipeline_ids:
        pipeline_result = await db.execute(
            select(AgentPipelineRun).where(AgentPipelineRun.id.in_(pipeline_ids))
        )
        child_pipelines = {
            row.id: row for row in pipeline_result.scalars().all()
            if str(row.test_run_id) == str(pipeline.test_run_id)
            and str(getattr(row, "parent_pipeline_run_id", ""))
            == str(pipeline.id)
        }
        authorized_ids = list(child_pipelines)
        if authorized_ids:
            stage_result = await db.execute(
                select(AgentStageResult)
                .where(AgentStageResult.pipeline_run_id.in_(authorized_ids))
                .order_by(
                    AgentStageResult.pipeline_run_id,
                    AgentStageResult.started_at,
                    AgentStageResult.stage_name,
                )
                .limit((MAX_RUNTIME_CHILDREN + 1) * MAX_RUNTIME_TASKS)
            )
            for stage in stage_result.scalars().all():
                stages_by_pipeline.setdefault(stage.pipeline_run_id, []).append(stage)

    children: list[tuple[Any, list[Any], Any]] = []
    for investigation in child_investigations:
        child_id = child_ids_by_investigation.get(str(investigation.id))
        child_pipeline = child_pipelines.get(child_id) if child_id else None
        if child_pipeline is None:
            continue
        children.append((
            child_pipeline,
            stages_by_pipeline.get(child_pipeline.id, []),
            investigation,
        ))
    return build_recursive_agentic_run_projection(pipeline, parent_stages, children)


@router.get("/event-log/health", response_model=PipelineEventLogHealthResponse)
async def get_pipeline_event_log_health(
    _: Any = Depends(require_role(UserRole.QA_LEAD)),
):
    """Return write-health details for the pipeline audit event log."""
    from app.services.pipeline_event_log import get_event_log_health

    health = get_event_log_health()
    status = "degraded" if health.get("write_failure_count", 0) else "healthy"
    return {
        "status": status,
        **health,
    }


@router.get("/pipelines/{pipeline_id}/replay", response_model=PipelineReplayResponse)
async def get_pipeline_replay(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Return a deterministic replay document for audit reconstruction."""
    from app.services.pipeline_replay_service import build_pipeline_replay

    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)

    replay = await build_pipeline_replay(db, pipeline_id)
    if replay is None:
        raise HTTPException(404, detail="Pipeline run not found")
    return replay


@router.get("/active-runs")
async def get_active_live_runs(
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Get all currently monitored live test runs.

    Enriches each Redis-state row with ``run_seq`` (per-(project,
    primary_suite_name) human-readable run number) by resolving each
    run's canonical TestRun.id. ``run_seq`` is null when the run's
    TestRun row doesn't exist yet — the Phase 4.5 incremental drain
    creates it within ~30s of the first event, so very-new active
    sessions fall through to the SDK ``build_number`` on the UI.

    Tenant-scoped: a non-ADMIN caller only sees live runs whose project
    they can access. ``RedisLiveRunState`` stores ``project_id`` on every
    state row, so the filter is in-memory (no DB round trip). Without this,
    the endpoint leaked every tenant's live run (slug, build number, counts,
    timing) to any authenticated user — the ``/active-runs/{run_id}`` sibling
    already gates on ``require_run_access`` but the list did not.
    """
    import uuid as _uuid
    from app.streams.live_run_state import RedisLiveRunState
    from app.services.stream_service import canonical_test_run_uuid
    from app.services.runs_service import fetch_run_seq_map

    active = await RedisLiveRunState.get_all_active()

    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:  # None = ADMIN → unrestricted
        allowed = {str(p) for p in accessible}
        # Rows without a resolvable project_id are dropped for non-admins —
        # we can't prove ownership, so we don't leak them.
        active = [r for r in active if (r.get("project_id") or "") in allowed]

    if active:
        run_uuids: list[_uuid.UUID] = []
        slug_to_uuid: dict[str, str] = {}
        for r in active:
            slug = r.get("run_id")
            if not slug:
                continue
            try:
                u = canonical_test_run_uuid(slug)
                run_uuids.append(u)
                slug_to_uuid[slug] = str(u)
            except Exception:
                continue
        seq_map = await fetch_run_seq_map(db, run_uuids) if run_uuids else {}
        for r in active:
            slug = r.get("run_id") or ""
            canonical = slug_to_uuid.get(slug)
            r["run_seq"] = seq_map.get(canonical) if canonical else None
    return {"active_runs": active}


@router.get("/active-runs/{run_id}")
async def get_live_run_state(
    run_id: str,
    _: Any = Depends(require_run_access()),
):
    """Get the current state for a single live test run."""
    from app.streams.live_run_state import RedisLiveRunState
    state = await RedisLiveRunState.get(run_id)
    if not state:
        raise HTTPException(404, detail="Live run not found or already completed")
    return state


@router.get("/runs/{run_id}/pipeline-status")
async def get_pipeline_status(
    run_id: str,
    workflow_type: str = Query(default="deep", pattern="^(offline|deep|live)$"),
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    WF-1: Return the latest pipeline execution status for a run and workflow type.

    Enables the frontend to know when deep/offline analysis ran, whether it
    completed fully, partially, or failed, without scanning the full timeline.
    """
    import uuid as _uuid
    from app.models.postgres import AgentPipelineRun, AgentStageResult

    try:
        run_uuid = _uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(400, detail="Invalid run_id")

    # Find latest pipeline for this run + workflow_type
    result = await db.execute(
        select(AgentPipelineRun)
        .where(
            AgentPipelineRun.test_run_id == run_uuid,
            AgentPipelineRun.workflow_type == workflow_type,
        )
        .order_by(AgentPipelineRun.created_at.desc())
        .limit(1)
    )
    pipeline = result.scalar_one_or_none()

    if not pipeline:
        return {
            "pipeline_run_id": None,
            "workflow_type": workflow_type,
            "status": "never_run",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "stage_summary": {"completed": 0, "failed": 0, "skipped": 0, "pending": 0},
        }

    # Get stage summary counts
    stages_result = await db.execute(
        select(AgentStageResult).where(AgentStageResult.pipeline_run_id == pipeline.id)
    )
    stages = stages_result.scalars().all()

    stage_summary = {"completed": 0, "failed": 0, "skipped": 0, "pending": 0}
    for s in stages:
        status = (s.status or "pending").lower()
        if status == "completed":
            stage_summary["completed"] += 1
        elif status == "failed":
            stage_summary["failed"] += 1
        elif status == "skipped":
            stage_summary["skipped"] += 1
        else:
            stage_summary["pending"] += 1

    return {
        "pipeline_run_id": str(pipeline.id),
        "workflow_type": pipeline.workflow_type,
        "status": pipeline.status or "pending",
        "public_status": public_status(pipeline.status or "pending"),
        "degraded": (pipeline.execution_metadata or {}).get("stage_quality") == "degraded",
        "started_at": pipeline.created_at.isoformat() if pipeline.created_at else None,
        "completed_at": pipeline.completed_at.isoformat() if pipeline.completed_at else None,
        "error": pipeline.error,
        "stage_summary": stage_summary,
    }


@router.get("/runs/{run_id}/summary", response_model=AgentRunSummaryResponse)
async def get_run_summary(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Retrieve the AI-generated markdown summary for a test run (all 4 layers if available)."""
    from app.db.mongo import Collections, get_mongo_db

    mongo_db = get_mongo_db()

    doc = await mongo_db[Collections.RUN_SUMMARIES].find_one({"test_run_id": run_id})
    if not doc:
        try:
            doc = await mongo_db[Collections.RUN_SUMMARIES].find_one({"test_run_id": uuid.UUID(run_id)})
        except ValueError:
            doc = None
    if not doc:
        fallback = await build_fallback_summary(db, run_id)
        if not fallback:
            raise HTTPException(404, detail="No summary found for this run")
        return fallback

    doc.pop("_id", None)
    summary = normalize_summary_doc(run_id, doc)
    if summary.executive_summary or summary.markdown_report:
        return summary

    fallback = await build_fallback_summary(db, run_id)
    if not fallback:
        raise HTTPException(404, detail="No summary found for this run")
    return fallback


async def _authorize_run_and_project(
    db: AsyncSession,
    current_user: User,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
) -> uuid.UUID:
    """Verify the caller may act on ``run_id``, and that it lives in ``project_id``.

    These two endpoints take ``run_id`` and ``project_id`` as **query** params,
    which defeats both of the usual guards:

    * ``require_run_access()`` reads ``run_id`` from ``request.path_params`` and
      returns the caller unchanged when it is absent -- so attaching it here
      would be a silent no-op, not a check.
    * ``tests/test_architectural_authorization.py`` matches scoped **path**
      params, so it declares a route with none of them protected by default.

    So both routes ran on a bare ``require_role(QA_ENGINEER)``: a role check,
    never a membership check. Any QA_ENGINEER could pass another tenant's
    ``run_id`` and have the agent load that run's failure clusters -- returning
    their classifications directly (``/regression-watch``), or generating a
    defect from their failure text and persisting it into a project of the
    caller's choosing (``/defect-command``), which copies the data across the
    tenant boundary where correctly-scoped endpoints will then serve it.

    The project is derived from the run rather than trusted from the caller;
    a mismatch is rejected instead of silently honoured.
    """
    result = await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    owning_project_id = result.scalar_one_or_none()
    if owning_project_id is None:
        raise HTTPException(status_code=404, detail="Test run not found")

    # Raises 403 for a non-member; ADMIN passes through.
    await resolve_project_scope(db, current_user, str(owning_project_id))

    if owning_project_id != project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id does not match the project that owns this run",
        )
    return owning_project_id


@router.post("/defect-command", status_code=200)
async def defect_command(
    cluster_id: str,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    project_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Run the Defect Commander on a failure cluster.
    Scores the cluster on 7 criticality dimensions, generates a Jira-ready
    defect description, and optionally creates a Jira ticket.
    """
    scoped_project_id = await _authorize_run_and_project(
        db, current_user, run_id, project_id
    )

    from app.agents.defect_commander import run_defect_commander
    result = await run_defect_commander(
        cluster_id=cluster_id,
        test_run_id=str(run_id),
        project_id=str(scoped_project_id),
        project_key=project_key,
    )
    return result


@router.post("/regression-watch", status_code=200)
async def regression_watch(
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Run the Regression Watchman on an existing test run.
    Returns classification of each failure cluster as:
      - new_regression | known_flaky_recurrence | environmental_anomaly
    """
    scoped_project_id = await _authorize_run_and_project(
        db, current_user, run_id, project_id
    )

    from app.agents.regression_watchman import run_regression_watchman
    result = await run_regression_watchman(
        test_run_id=str(run_id),
        project_id=str(scoped_project_id),
    )
    return {"run_id": str(run_id), "classifications": result}
