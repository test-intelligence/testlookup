"""
Agent pipeline management endpoints.

Provides visibility into running/completed pipelines and allows manual triggering.
"""
import inspect
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role
from app.db.postgres import get_db
from app.models.postgres import AgentPipelineRun, AgentStageResult, TestRun, UserRole
from app.models.schemas import (
    AgentPipelineResponse,
    AgentRunSummaryResponse,
    PipelineTimelineResponse,
    PipelineTimelineSummary,
    PipelineTimelineEventResponse,
    TriggerPipelineRequest,
)
from app.services.run_summary_service import build_fallback_summary, normalize_summary_doc

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"])


async def _resolve_maybe_awaitable(value: Any) -> Any:
    """Support both async collaborators and synchronous test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


@router.get("/pipelines", response_model=list[AgentPipelineResponse])
async def list_pipelines(
    run_id: Optional[uuid.UUID] = Query(None),
    project_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(get_current_active_user),
):
    """List agent pipeline runs, optionally filtered by test run, project, or status."""
    q = select(AgentPipelineRun)
    if run_id:
        q = q.where(AgentPipelineRun.test_run_id == run_id)
    if project_id:
        q = q.join(TestRun, AgentPipelineRun.test_run_id == TestRun.id).where(
            TestRun.project_id == project_id
        )
    if status:
        q = q.where(AgentPipelineRun.status == status)
    q = q.order_by(AgentPipelineRun.created_at.desc()).limit(limit)

    result = await db.execute(q)
    return result.scalars().all()


@router.get("/pipelines/{pipeline_id}", response_model=AgentPipelineResponse)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(get_current_active_user),
):
    """Get a single pipeline run with all stage results."""
    result = await db.execute(
        select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_id)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(404, detail="Pipeline run not found")
    return pipeline


@router.post("/pipelines/trigger", status_code=202)
async def trigger_pipeline(
    payload: TriggerPipelineRequest,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Manually trigger the agent pipeline for an existing test run.
    Returns 202 Accepted — pipeline runs asynchronously via Celery.
    """
    # Verify the run exists
    run_result = await db.execute(select(TestRun).where(TestRun.id == payload.test_run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, detail="TestRun not found")

    from app.worker.tasks import run_agent_pipeline
    task = run_agent_pipeline.delay(
        test_run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number,
        workflow_type="offline",
    )

    return {"message": "Pipeline queued", "task_id": task.id, "run_id": str(run.id)}


@router.get("/pipelines/{pipeline_id}/stages", response_model=list[dict])
async def get_pipeline_stages(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(get_current_active_user),
):
    """Get detailed stage results for a pipeline run."""
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
    _: Any = Depends(get_current_active_user),
):
    """
    Get the full agent timeline with per-stage observability data:
    tokens, cost, latency, confidence, evidence count, route rationale,
    error taxonomy, fallback status, and alerts.
    """
    from app.services.agent_cost_service import check_alerts, get_pipeline_cost_summary
    from app.services.pipeline_event_log import get_pipeline_timeline as get_pipeline_events

    # Verify pipeline exists
    pipeline_result = await db.execute(
        select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_id)
    )
    pipeline = pipeline_result.scalar_one_or_none()
    if not pipeline:
        from fastapi import HTTPException
        raise HTTPException(404, detail="Pipeline run not found")

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
    }


@router.get("/active-runs")
async def get_active_live_runs(_: Any = Depends(get_current_active_user)):
    """Get all currently monitored live test runs."""
    from app.agents.live_monitor import LiveMonitorAgent
    return {"active_runs": await LiveMonitorAgent.get_active_runs()}


@router.get("/active-runs/{run_id}")
async def get_live_run_state(
    run_id: str,
    _: Any = Depends(get_current_active_user),
):
    """Get the current state for a single live test run."""
    from app.agents.live_monitor import LiveMonitorAgent
    state = await LiveMonitorAgent.get_run_state(run_id)
    if not state:
        raise HTTPException(404, detail="Live run not found or already completed")
    return state


@router.get("/runs/{run_id}/pipeline-status")
async def get_pipeline_status(
    run_id: str,
    workflow_type: str = Query(default="deep", pattern="^(offline|deep|live)$"),
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(get_current_active_user),
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
        "started_at": pipeline.created_at.isoformat() if pipeline.created_at else None,
        "completed_at": pipeline.completed_at.isoformat() if pipeline.completed_at else None,
        "error": pipeline.error,
        "stage_summary": stage_summary,
    }


@router.get("/runs/{run_id}/summary", response_model=AgentRunSummaryResponse)
async def get_run_summary(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(get_current_active_user),
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


@router.post("/defect-command", status_code=200)
async def defect_command(
    cluster_id: str,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    project_key: Optional[str] = None,
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Run the Defect Commander on a failure cluster.
    Scores the cluster on 7 criticality dimensions, generates a Jira-ready
    defect description, and optionally creates a Jira ticket.
    """
    from app.agents.defect_commander import run_defect_commander
    result = await run_defect_commander(
        cluster_id=cluster_id,
        test_run_id=str(run_id),
        project_id=str(project_id),
        project_key=project_key,
    )
    return result


@router.post("/regression-watch", status_code=200)
async def regression_watch(
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """
    Run the Regression Watchman on an existing test run.
    Returns classification of each failure cluster as:
      - new_regression | known_flaky_recurrence | environmental_anomaly
    """
    from app.agents.regression_watchman import run_regression_watchman
    result = await run_regression_watchman(
        test_run_id=str(run_id),
        project_id=str(project_id),
    )
    return {"run_id": str(run_id), "classifications": result}
