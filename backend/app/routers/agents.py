"""
Agent pipeline management endpoints.

Provides visibility into running/completed pipelines and allows manual triggering.
"""
import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.deep_investigation_gate import require_deep_investigation
from app.services.pipeline_cancellation import request_cancel
from app.services.pipeline_retry_config import decide_retry_mode
from app.services.review_request_service import REVIEW_REJECTED_ERROR_PREFIX
from app.services.workflow_run_state import (
    PUBLIC_STATUS,
    PipelineRunStatus,
    is_resumable,
    is_terminal,
    normalize_status,
    public_status,
)
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
    ReviewRequest,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import (
    AgentPipelineResponse,
    AgentRunSummaryResponse,
    BulkJobRead,
    BulkRunFilter,
    ReviewBlock,
    PipelineCompareResponse,
    PipelineEventLogHealthResponse,
    PipelineReplayResponse,
    PipelineTimelineResponse,
    PipelineTimelineSummary,
    PipelineTimelineEventResponse,
    TriggerPipelineRequest,
)
from app.models.agentic_runtime import AgenticRunV1
from app.core.agent_ops_vocab import (
    BULK_FILTER_CAP,
    BULK_IDS_OR_FILTER,
    BULK_JOB_ALREADY_FINISHED,
    BULK_JOB_NOT_FOUND,
    BULK_NOTHING_TO_TRIGGER,
    COMPARE_CROSS_PROJECT,
    COMPARE_SAME_ID,
)
from app.services import (
    agent_catalog,
    bulk_pipeline_job_service,
    inline_invocation,
    pipeline_admission,
    pipeline_compare_service,
    runs_service,
)
from app.services.activity.service import ActorRef, record as record_activity
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


async def _attach_review_summaries(
    db: AsyncSession, pipelines: list[AgentPipelineRun],
) -> None:
    """Attach the live report review state and settlement time in one query.

    The pipeline cards need this projection to distinguish ``passed`` after a
    human acceptance from a non-report pipeline that passed automatically.
    Only pipeline-run report subjects belong here: invocation and capability
    reviews may share a pipeline id but govern a different public subject.
    """
    pipeline_ids = [pipeline.id for pipeline in pipelines]
    if not pipeline_ids:
        return
    rows = (
        await db.execute(
            select(
                ReviewRequest.pipeline_run_id,
                ReviewRequest.state,
                ReviewRequest.reviewed_at,
            )
            .where(
                ReviewRequest.pipeline_run_id.in_(pipeline_ids),
                ReviewRequest.kind == "report",
                # W23c: an inline-input invocation's pipeline is reviewed as the invocation.
                ReviewRequest.subject_type.in_(("pipeline_run", "invocation")),
                ReviewRequest.state != "superseded",
            )
            .order_by(ReviewRequest.created_at.desc())
        )
    ).all()
    by_pipeline: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        if row.pipeline_run_id is not None:
            by_pipeline.setdefault(
                row.pipeline_run_id,
                {"state": row.state, "settled_at": row.reviewed_at},
            )
    for pipeline in pipelines:
        pipeline.review_summary = by_pipeline.get(pipeline.id)  # type: ignore[attr-defined]


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


@router.get("/catalog", response_model=list[agent_catalog.AgentCatalogEntry])
async def list_agent_catalog(
    _: Any = Depends(get_current_active_user),
):
    """Discover the agents (E1.1): every capability in the registry.

    Clients read agent ids here instead of hard-coding stage names. Declared
    first in this router: the planned ``/agents/{agent_id}/invoke`` route must
    come after every literal ``/agents/...`` path (architecture section 3.5).
    """
    return agent_catalog.list_catalog()


@router.get("/catalog/{agent_id}", response_model=agent_catalog.AgentCatalogDetail)
async def get_agent_catalog_entry(
    agent_id: str,
    _: Any = Depends(get_current_active_user),
):
    """One agent, with its generated input wrapper and JSON Schemas (E1.1)."""
    if not agent_catalog.AGENT_ID_PATTERN.match(agent_id):
        raise HTTPException(
            status_code=422,
            detail="agent_id must look like agent.<name>.v<version>, for example agent.summary.v1",
        )
    detail = agent_catalog.get_catalog_detail(agent_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    return detail


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
    await _attach_review_summaries(db, pipelines)
    return pipelines


@router.get("/pipelines/compare", response_model=PipelineCompareResponse)
async def compare_pipelines(
    a: uuid.UUID = Query(...),
    b: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_active_user),
):
    """Compare two pipeline runs of one project stage by stage (W14 AGT-05).

    Declared BEFORE ``get_pipeline``: FastAPI matches in registration order,
    and the UUID route would otherwise read ``compare`` as a pipeline id.
    BOTH query ids pass the same 404 / 403 gate as ``/pipelines/{id}``.
    Writes nothing.
    """
    if a == b:
        raise HTTPException(422, detail=COMPARE_SAME_ID)
    first = await _load_pipeline_or_404(db, a)
    second = await _load_pipeline_or_404(db, b)
    await _require_pipeline_access(db, current_user, first)
    await _require_pipeline_access(db, current_user, second)
    run_a = await db.get(TestRun, first.test_run_id)
    run_b = await db.get(TestRun, second.test_run_id)
    if run_a is None or run_b is None:
        raise HTTPException(404, detail="Pipeline run not found")
    if run_a.project_id != run_b.project_id:
        raise HTTPException(422, detail=COMPARE_CROSS_PROJECT)
    return await pipeline_compare_service.compare_pipelines(db, first, second)


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
    await _attach_review_summaries(db, [pipeline])
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

    from app.services import workflow_definition_service as workflow_svc

    # W14 AGT-08: a request that names NO workflow is not pinned here. The
    # task gets ``workflow_id=None`` and the worker resolves the project's
    # default for the offline base at task start (``_create_pipeline_run``,
    # the one place every trigger path meets), falling back to the built-in.
    # A NAMED workflow is resolved here exactly as before.
    workflow_id: Optional[str] = None
    workflow_version: Optional[int] = None
    workflow_ref: Optional[str] = None
    if getattr(payload, "workflow_id", None) is None:
        workflow_type = "offline"
    else:
        requested_workflow_id = getattr(payload, "workflow_id", None) or "offline"
        try:
            selected_workflow = await workflow_svc.get_published_definition(
                db,
                run.project_id,
                requested_workflow_id,
                getattr(payload, "workflow_version", None),
            )
        except workflow_svc.WorkflowNotFound:
            raise HTTPException(404, detail="Workflow definition not found") from None
        except workflow_svc.WorkflowNotPublished:
            raise HTTPException(409, detail="Workflow definition is not published") from None
        selected_item = (
            selected_workflow
            if isinstance(selected_workflow, dict)
            else workflow_svc.serialize(selected_workflow)
        )
        workflow_type = str(selected_item["base"])
        workflow_id = str(selected_item["workflow_id"])
        workflow_version = int(selected_item["version"])
        workflow_ref = f"{workflow_id}@{workflow_version}"

    # E7.2: the state machine, not the Redis admission lock, decides whether a
    # new trigger is accepted. A run that is pending / running / retry_wait is
    # returned as-is (200) instead of queueing a second concurrent pipeline.
    existing = await _latest_in_progress_pipeline(db, run.id, workflow_type)
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

    # AIC-01: ``workflow_id: "deep"`` (or a custom workflow on the deep base)
    # starts a deep investigation like POST /deep-investigate does, so the
    # same Settings > AI gate applies. After the "already in progress" answer
    # (that queues nothing), before anything is queued.
    if workflow_type == "deep":
        await require_deep_investigation(db)

    from app.worker.tasks import run_agent_pipeline
    task = run_agent_pipeline.delay(
        test_run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number,
        workflow_type=workflow_type,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        requested_by=str(current_user.id),
    )

    # Attempt mode: this handler never commits the request session, and the
    # thing worth recording is that a dispatch was issued, whatever it does.
    await record_activity(
        db,
        project_id=run.project_id,
        event_type="analysis.triggered",
        actor=ActorRef.from_user(current_user),
        entity_id=run.id,
        entity_label=f"Build {run.build_number}",
        context={
            "workflow_type": workflow_type,
            "workflow_ref": workflow_ref if workflow_ref is not None else f"default:{workflow_type}",
            "task_id": task.id,
        },
    )
    if workflow_ref is None:
        return {
            "message": "Pipeline queued",
            "task_id": task.id,
            "run_id": str(run.id),
            "workflow_ref": None,
            "workflow_source": "resolved_at_start",
        }
    return {
        "message": "Pipeline queued",
        "task_id": task.id,
        "run_id": str(run.id),
        "workflow_ref": workflow_ref,
    }


async def _latest_in_progress_pipeline(
    db: AsyncSession, test_run_id: uuid.UUID, workflow_type: str
) -> Optional[AgentPipelineRun]:
    """The newest non-terminal pipeline for (run, workflow), or None.

    A module-level name tests patch; the read is ``pipeline_admission``'s
    (shared with the deep trigger)."""
    return await pipeline_admission.latest_in_progress_pipeline(db, test_run_id, workflow_type)


# ── Manual retry / cancel (E7.4) ─────────────────────────────────────────────


class RetryPipelineResponse(BaseModel):
    """What a manual retry did. ``mode`` is ``resume`` or ``rerun``."""

    mode: str
    # ``None`` for a rerun: the new run's id is minted by the worker when it
    # creates the row, so the caller follows ``links.poll`` to find it rather
    # than being handed a placeholder that looks like an id.
    pipeline_run_id: Optional[str] = None
    rerun_of: Optional[str] = None
    attempt: int
    max_attempts: int
    reason: str
    status: str
    public_status: str
    links: dict = Field(default_factory=dict)


async def _record_retry_activity(db, run, current_user, pipeline, plan, *, mode: str) -> None:
    """One ledger row per manual retry, naming whether it resumed or reran.

    ``mode`` is in the summary because the two are materially different to a
    reader of the feed: a resume keeps the run's completed stages, a rerun
    throws them away and spends a whole new pipeline.
    """
    await record_activity(
        db,
        project_id=run.project_id,
        event_type="analysis.retried",
        actor=ActorRef.from_user(current_user),
        entity_id=run.id,
        entity_label=run.build_number,
        context={
            "pipeline_run_id": str(pipeline.id),
            "workflow_type": pipeline.workflow_type,
            "mode": mode,
            "reason": plan.reason,
            "attempt": int(pipeline.attempt or 1),
        },
    )


@router.post("/pipelines/{pipeline_id}/retry", status_code=202)
async def retry_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Retry a finished-but-unsuccessful pipeline run (E7.4).

    Two outcomes, decided by configuration rather than by the caller:

    * **resume** — the configuration this run froze at start still matches the
      live one, so the same row is replayed and its completed stages are kept.
    * **rerun** — the configuration changed (a narrowed tool allowlist, a
      different model, an ``AI_OFFLINE_MODE`` flip), so a NEW run starts with
      ``rerun_of`` pointing here. Replaying checkpoints authorised under the old
      configuration could re-enter a tool the new one forbids, and the operator
      who changed the config is retrying precisely because they want the change
      to take effect.

    Refuses (409) when the run is still in progress, when it finished
    successfully, was rejected in review, or is at its attempt ceiling. A
    review rejection is terminal, even if configuration changed; start a new
    run explicitly to produce a fresh proposal. The rejection and ceiling responses
    carry ``links.rerun`` so the caller can deliberately start a fresh run
    rather than being told only "no".
    """
    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)

    current = normalize_status(pipeline.status)
    if (pipeline.error or "").startswith(REVIEW_REJECTED_ERROR_PREFIX):
        raise HTTPException(
            409,
            detail={
                "message": "Pipeline was rejected in review; start a new run instead",
                "reason": "review_rejected",
                "pipeline_run_id": str(pipeline.id),
                "status": current.value,
                "public_status": public_status(current),
                "links": {"rerun": "/api/v1/agents/pipelines/trigger"},
            },
        )
    if not is_terminal(current):
        raise HTTPException(
            409,
            detail={
                "message": "Pipeline is still in progress",
                "pipeline_run_id": str(pipeline.id),
                "status": current.value,
                "public_status": public_status(current),
            },
        )
    if bool(getattr(pipeline, "cancel_requested", False)):
        # E7.4: a stopped run is never resumed, by any path; the worker's claim
        # refuses it. Answering "resume" here queued a task that did nothing
        # while the Agents page said "Resumed (attempt 2/5)" (W04 local walk).
        raise HTTPException(
            409,
            detail={
                "message": "This run was stopped, and a stopped run is not resumed. Start a new run instead.",
                "reason": "cancelled",
                "pipeline_run_id": str(pipeline.id),
                "status": current.value,
                "public_status": public_status(current),
                "links": {"rerun": "/api/v1/agents/pipelines/trigger"},
            },
        )
    if not is_resumable(pipeline.status, pipeline.execution_metadata):
        # A clean ``completed``/``passed`` run. Retrying it would silently
        # discard a good result; the caller wants a fresh trigger instead.
        raise HTTPException(
            409,
            detail={
                "message": "Pipeline finished successfully; nothing to retry",
                "pipeline_run_id": str(pipeline.id),
                "status": current.value,
                "public_status": public_status(current),
                "links": {"rerun": "/api/v1/agents/pipelines/trigger"},
            },
        )

    attempt = int(pipeline.attempt or 1)
    max_attempts = int(pipeline.max_attempts or 5)
    if attempt >= max_attempts:
        raise HTTPException(
            409,
            detail={
                "message": (
                    f"Pipeline reached its attempt ceiling ({attempt}/{max_attempts}). "
                    "Start a new run instead."
                ),
                "pipeline_run_id": str(pipeline.id),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "status": current.value,
                "public_status": public_status(current),
                "links": {"rerun": "/api/v1/agents/pipelines/trigger"},
            },
        )

    # Another pipeline for the same (run, workflow) may already be in flight --
    # an automatic retry that fired between the read above and this click.
    in_flight = await _latest_in_progress_pipeline(
        db, pipeline.test_run_id, pipeline.workflow_type
    )
    if in_flight is not None:
        raise HTTPException(
            409,
            detail={
                "message": "Another pipeline for this run is already in progress",
                "pipeline_run_id": str(in_flight.id),
                "status": in_flight.status,
                "public_status": public_status(in_flight.status),
            },
        )

    from app.agents.workflow import _resolve_analysis_mode_snapshot
    from app.services.ai_config_resolver import get_effective_ai_config
    from app.services.pipeline_ai_scope import build_ai_config_snapshot

    # Both reads go through the REQUEST session: this handler already holds a
    # connection (the guard's), and a second session here starves the 2+1 pool.
    # The live threshold/timeout are compared with the frozen ones, so an
    # operator who changed them and presses Retry gets a rerun under the new
    # values instead of a resume that replays the old ones (W04 §1.3).
    plan = decide_retry_mode(
        pipeline.execution_metadata,
        await _resolve_analysis_mode_snapshot(db),
        current_ai_config=build_ai_config_snapshot(
            await get_effective_ai_config(db=db, fresh=True), {}
        ),
    )

    run = await db.get(TestRun, pipeline.test_run_id)
    if run is None:
        raise HTTPException(404, detail="TestRun not found for this pipeline")

    if plan.is_rerun:
        # AIC-01: a rerun of a deep pipeline is a NEW deep investigation, so the
        # Settings > AI gate applies (before anything is queued). A same-id
        # resume below replays accepted work under its frozen snapshot and is
        # never gated.
        if pipeline.workflow_type == "deep":
            await require_deep_investigation(db)

        from app.worker.tasks import run_agent_pipeline

        frozen_metadata = dict(pipeline.execution_metadata or {})

        run_agent_pipeline.delay(
            test_run_id=str(run.id),
            project_id=str(run.project_id),
            build_number=run.build_number,
            workflow_type=pipeline.workflow_type,
            workflow_id=frozen_metadata.get("workflow_id"),
            workflow_version=frozen_metadata.get("workflow_version"),
            rerun_of=str(pipeline.id),
            requested_by=str(current_user.id),
        )
        logger.info(
            "pipeline retry -> rerun (%s) for %s", plan.reason, pipeline.id
        )
        await _record_retry_activity(db, run, current_user, pipeline, plan, mode="rerun")
        return RetryPipelineResponse(
            mode="rerun",
            pipeline_run_id=None,
            rerun_of=str(pipeline.id),
            attempt=1,
            max_attempts=max_attempts,
            reason=plan.reason,
            status=PipelineRunStatus.PENDING.value,
            public_status=public_status(PipelineRunStatus.PENDING),
            links={
                "poll": f"/api/v1/agents/pipelines?run_id={run.id}",
                "replaces": f"/api/v1/agents/pipelines/{pipeline.id}",
            },
        )

    from app.worker.tasks import resume_agent_pipeline

    # No ``expected_attempt``: that guard exists so a STALE scheduled retry
    # cannot resurrect a run. This resume is being asked for now, by a person,
    # against the row they just read.
    resume_agent_pipeline.apply_async(
        kwargs={"pipeline_run_id": str(pipeline.id), "build_number": "manual-retry"},
        queue="ai_analysis",
    )
    logger.info("pipeline retry -> resume for %s", pipeline.id)
    await _record_retry_activity(db, run, current_user, pipeline, plan, mode="resume")
    return RetryPipelineResponse(
        mode="resume",
        pipeline_run_id=str(pipeline.id),
        rerun_of=None,
        attempt=attempt + 1,
        max_attempts=max_attempts,
        reason=plan.reason,
        status=current.value,
        public_status=public_status(current),
        links={"poll": f"/api/v1/agents/pipelines/{pipeline.id}"},
    )


@router.post("/pipelines/{pipeline_id}/cancel", status_code=200)
async def cancel_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Cancel a pipeline run (E7.4).

    A ``pending`` or ``retry_wait`` run has no live worker and stops here and
    now. A ``running`` run is *asked* to stop: the flag is set and the worker
    terminalises itself at its next stage boundary, so its in-flight writes
    land coherently instead of being orphaned under a row the API already
    declared dead.

    Cancellation is sticky either way — an automatic retry can never bring a
    cancelled run back. See ``app/services/pipeline_cancellation.py`` for why
    both orderings of the cancel-vs-retry race end ``failed``.
    """
    pipeline = await _load_pipeline_or_404(db, pipeline_id)
    await _require_pipeline_access(db, current_user, pipeline)

    outcome = await request_cancel(
        db, pipeline.id, requested_by=getattr(current_user, "email", None)
    )
    if not outcome.accepted:
        raise HTTPException(
            409,
            detail={
                "message": "Pipeline has already finished",
                "pipeline_run_id": str(pipeline.id),
                **outcome.as_dict(),
            },
        )
    run = await db.get(TestRun, pipeline.test_run_id)
    if run is not None:
        await record_activity(
            db,
            project_id=run.project_id,
            event_type="analysis.cancelled",
            actor=ActorRef.from_user(current_user),
            entity_id=run.id,
            entity_label=run.build_number,
            context={
                "pipeline_run_id": str(pipeline.id),
                "workflow_type": pipeline.workflow_type,
                "from_status": outcome.status,
                "terminal": outcome.terminal,
                "reason": outcome.reason,
            },
        )
    # The router owns the commit (transaction-boundary discipline).
    await db.commit()
    return {"pipeline_run_id": str(pipeline.id), **outcome.as_dict()}


# ── Bulk trigger ────────────────────────────────────────────────────────────

class BulkTriggerRequest(BaseModel):
    # W14 AGT-12: exactly one of ``run_ids`` / ``run_filter`` (judged in the
    # handler with one fixed sentence each); ``client_token`` makes a retried
    # or double-clicked request queue the batch once (10 minutes).
    run_ids: Optional[List[uuid.UUID]] = Field(None, min_length=1, max_length=BULK_FILTER_CAP)
    workflow_type: Literal["offline", "deep"] = "offline"
    run_filter: Optional[BulkRunFilter] = None
    client_token: Optional[str] = Field(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class BulkTriggerResponse(BaseModel):
    queued: int
    not_found: int
    workflow_type: str
    not_found_ids: List[str] = Field(default_factory=list)
    requested: int = 0
    skipped_in_progress: int = 0
    skipped_in_progress_ids: List[str] = Field(default_factory=list)   # <= 25
    source: str = "selection"                                          # BULK_SOURCES
    matched: Optional[int] = None                                      # filter: the COUNT of matching runs
    truncated: bool = False                                            # filter: more matched than BULK_FILTER_CAP
    bulk_job_id: Optional[str] = None
    tracking: bool = False                                             # False: Redis was unavailable
    replayed: bool = False                                             # the client_token's earlier answer


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
    burst.

    W14 AGT-12: the runs are ``run_ids`` OR the Runs list's own filter
    (``run_filter``, resolved by the list's predicates, newest 2,000); a run
    whose pipeline is still in progress is skipped and counted (the worker's
    admission keys never held a person's trigger); the answer carries a
    ``bulk_job_id`` whose progress ``GET /pipelines/bulk-jobs/{id}`` counts
    from the pipeline rows. ``client_token`` makes a retry queue nothing twice.
    """
    from app.worker.tasks import run_agent_pipeline

    # W14 AGT-12: exactly one source of runs, judged before any statement.
    if payload.run_ids is not None and payload.run_filter is not None:
        raise HTTPException(422, detail=BULK_IDS_OR_FILTER)
    if payload.run_ids is None and payload.run_filter is None:
        raise HTTPException(422, detail=BULK_NOTHING_TO_TRIGGER)

    # AIC-01: a bulk deep trigger obeys the same Settings > AI gate as the
    # single route (it applied none), refused BEFORE the scope query and the
    # fan-out so nothing is queued. ``offline`` is unaffected.
    if payload.workflow_type == "deep":
        await require_deep_investigation(db)

    # W14 AGT-12: a retried / double-clicked request (same client_token within
    # 10 minutes) answers the first request's job instead of queueing again.
    bulk_job_id = str(uuid.uuid4())
    if payload.client_token:
        earlier = await bulk_pipeline_job_service.claim_client_token(
            current_user.id, payload.client_token, bulk_job_id,
        )
        if earlier is not None:
            return await _bulk_replayed_answer(earlier, payload.workflow_type)

    source = "filter" if payload.run_filter is not None else "selection"
    matched: Optional[int] = None
    truncated = False
    not_found_ids: list[str] = []
    if payload.run_filter is not None:
        # The Runs list's own filter: its predicates, the caller's projects,
        # newest first, at most BULK_FILTER_CAP (``matched`` is the COUNT).
        found, matched, truncated = await bulk_pipeline_job_service.resolve_run_filter(
            db, current_user, payload.run_filter,
        )
        requested = len(found)
    else:
        # One DB roundtrip to resolve every run + reject unknown ids.
        # Returning the not-found list lets the UI tell the user *which* IDs
        # were skipped vs which actually queued.
        # Scope BEFORE fanning out. The single-run sibling above checks membership;
        # this one accepted up to 2000 ids and filtered on none of them, so one call
        # could queue pipelines across every project on the deployment. A run the
        # caller cannot access is reported as not-found rather than as forbidden --
        # the response already carries a not_found list, and distinguishing the two
        # would turn it into an existence oracle.
        run_ids = list(payload.run_ids or [])
        accessible = await get_accessible_project_ids(db, current_user)
        stmt = select(TestRun.id, TestRun.project_id, TestRun.build_number).where(
            TestRun.id.in_(run_ids)
        )
        if accessible is not None:
            stmt = stmt.where(TestRun.project_id.in_(accessible))
        result = await db.execute(stmt)
        found = [(row[0], row[1], row[2]) for row in result.all()]
        found_ids = {row[0] for row in found}
        not_found_ids = [str(rid) for rid in run_ids if rid not in found_ids]
        requested = len(run_ids)

    # ONE grouped statement: a run whose pipeline of this workflow is still in
    # progress is skipped (counted and echoed), not queued a second time.
    in_progress = await pipeline_admission.runs_with_in_progress_pipeline(
        db, [row[0] for row in found], payload.workflow_type,
    )
    to_queue = [row for row in found if row[0] not in in_progress]
    skipped_rows = [row for row in found if row[0] in in_progress]
    planned_per_project: dict[uuid.UUID, int] = {}
    for _run_id, project_id, _build in to_queue:
        planned_per_project[project_id] = planned_per_project.get(project_id, 0) + 1

    # The job record BEFORE the fan-out (a fast worker must find it). Redis
    # down: untracked, and the batch still queues exactly as before W14.
    tracking = await bulk_pipeline_job_service.create_job(
        job_id=bulk_job_id, requested_by=current_user.id, workflow_type=payload.workflow_type,
        source=source, requested=requested, queued_rows=to_queue, skipped=len(skipped_rows),
        not_found=len(not_found_ids), matched=matched, truncated=truncated,
        queued_per_project=planned_per_project,
    )
    stamp = {"bulk_job_id": bulk_job_id} if tracking else {}

    BATCH_SIZE = 25
    INTER_BATCH_SLEEP_S = 0.05  # 50ms between bursts; barely noticeable, gentle on broker
    queued_ids: list[uuid.UUID] = []
    queued_per_project: dict[uuid.UUID, int] = {}

    for i in range(0, len(to_queue), BATCH_SIZE):
        batch = to_queue[i : i + BATCH_SIZE]
        for run_id, project_id, build_number in batch:
            try:
                run_agent_pipeline.apply_async(
                    kwargs={
                        "test_run_id": str(run_id),
                        "project_id": str(project_id),
                        "build_number": build_number,
                        "workflow_type": payload.workflow_type,
                        "requested_by": str(current_user.id),
                        **stamp,
                    },
                    queue="ai_analysis",
                )
                queued_ids.append(run_id)
                queued_per_project[project_id] = queued_per_project.get(project_id, 0) + 1
            except Exception as exc:  # pragma: no cover - broker errors are exceptional
                logger.warning(
                    "bulk_trigger: queue failed for run %s: %s", run_id, type(exc).__name__,
                )
        if i + BATCH_SIZE < len(to_queue):
            await asyncio.sleep(INTER_BATCH_SLEEP_S)
    if tracking and len(queued_ids) != len(to_queue):
        await bulk_pipeline_job_service.record_queued(bulk_job_id, queued_ids, queued_per_project)

    logger.info(
        "bulk_trigger_pipelines: queued=%d not_found=%d skipped_in_progress=%d workflow=%s",
        len(queued_ids), len(not_found_ids), len(skipped_rows), payload.workflow_type,
    )

    # One row per project, not per run: a 2000-run trigger would otherwise
    # bury the feed. Counted from what actually queued, not what was asked.
    skipped_per_project: dict[uuid.UUID, int] = {}
    for _run_id, project_id, _build in skipped_rows:
        skipped_per_project[project_id] = skipped_per_project.get(project_id, 0) + 1
    actor = ActorRef.from_user(current_user)
    for project_id, count in queued_per_project.items():
        await record_activity(
            db,
            project_id=project_id,
            event_type="analysis.bulk_triggered",
            actor=actor,
            entity_id=project_id,
            context={
                "count": count,
                "workflow_type": payload.workflow_type,
                "bulk_job_id": bulk_job_id if tracking else None,
                "source": source,
                "skipped_in_progress": skipped_per_project.get(project_id, 0),
            },
        )

    return BulkTriggerResponse(
        queued=len(queued_ids),
        not_found=len(not_found_ids),
        workflow_type=payload.workflow_type,
        not_found_ids=not_found_ids[:25],  # cap echo to keep response small
        requested=requested,
        skipped_in_progress=len(skipped_rows),
        skipped_in_progress_ids=[str(row[0]) for row in skipped_rows[:25]],
        source=source,
        matched=matched,
        truncated=truncated,
        bulk_job_id=bulk_job_id if tracking else None,
        tracking=tracking,
    )


async def _bulk_replayed_answer(bulk_job_id: str, workflow_type: str) -> JSONResponse:
    """200 with the earlier request's facts (from its record); nothing is queued again."""
    job = await bulk_pipeline_job_service.read_job(bulk_job_id)
    body = BulkTriggerResponse(
        queued=(job or {}).get("queued") or 0,
        not_found=(job or {}).get("not_found") or 0,
        workflow_type=(job or {}).get("workflow_type") or workflow_type,
        requested=(job or {}).get("requested") or 0,
        skipped_in_progress=(job or {}).get("skipped_in_progress") or 0,
        source=(job or {}).get("source") or "selection",
        matched=(job or {}).get("matched"),
        truncated=bool((job or {}).get("truncated")),
        bulk_job_id=bulk_job_id,
        tracking=job is not None,
        replayed=True,
    )
    return JSONResponse(status_code=200, content=body.model_dump(mode="json"))


# ── Bulk jobs (W14 AGT-12): progress and cancel ────────────────────────────
# Declared right after the bulk trigger; neither path is matched by a
# ``/pipelines/{pipeline_id}/...`` route (a literal third segment). A job is
# readable / cancellable by its requester or an admin only; anyone else gets
# the same 404 as an unknown job (no oracle).


def _job_owner(job: dict) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(job.get("requested_by") or ""))
    except ValueError:
        return None


@router.get("/pipelines/bulk-jobs/{bulk_job_id}", response_model=BulkJobRead)
async def get_bulk_job(
    bulk_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """A bulk job's request facts and its progress, COUNTED from the pipeline
    rows on this read (restricted to the caller's current projects). Without
    the request record (expired / flushed) only the caller's own rows are
    counted and ``counters_source`` says ``postgres``. Writes nothing."""
    job_id = str(bulk_job_id)
    accessible = await get_accessible_project_ids(db, current_user)
    is_admin = accessible is None
    now = datetime.now(timezone.utc)
    job = await bulk_pipeline_job_service.read_job(job_id)
    if job is not None:
        if _job_owner(job) != current_user.id and not is_admin:
            raise HTTPException(404, detail=BULK_JOB_NOT_FOUND)
        progress = await bulk_pipeline_job_service.job_progress(
            db, job_id, run_ids=job["run_ids"], requested_by=_job_owner(job), accessible=accessible,
        )
        return bulk_pipeline_job_service.job_read(job_id, job, progress, now=now)
    progress = await bulk_pipeline_job_service.job_progress(
        db, job_id, run_ids=None, requested_by=current_user.id, accessible=accessible,
    )
    if progress["started"] == 0:
        raise HTTPException(404, detail=BULK_JOB_NOT_FOUND)
    return bulk_pipeline_job_service.job_read(job_id, None, progress, now=now)


@router.post("/pipelines/bulk-jobs/{bulk_job_id}/cancel", response_model=BulkJobRead, status_code=200)
async def cancel_bulk_job(
    bulk_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: Any = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Cancel a bulk job: its tasks that have NOT started return at their first
    line without creating a pipeline; running pipelines keep their own Stop.
    Idempotent (an already-cancelled job answers 200 and records nothing new);
    a finished job is a 409. One ``analysis.bulk_cancelled`` per project."""
    job_id = str(bulk_job_id)
    accessible = await get_accessible_project_ids(db, current_user)
    is_admin = accessible is None
    now = datetime.now(timezone.utc)
    job = await bulk_pipeline_job_service.read_job(job_id)
    if job is None or (_job_owner(job) != current_user.id and not is_admin):
        raise HTTPException(404, detail=BULK_JOB_NOT_FOUND)
    progress = await bulk_pipeline_job_service.job_progress(
        db, job_id, run_ids=job["run_ids"], requested_by=_job_owner(job), accessible=accessible,
    )
    current = bulk_pipeline_job_service.job_read(job_id, job, progress, now=now)
    if current["state"] == "cancelled":
        return current
    if current["state"] == "finished":
        raise HTTPException(409, detail=BULK_JOB_ALREADY_FINISHED)
    cancelled = await bulk_pipeline_job_service.mark_cancelled(job_id, by=current_user.id, now=now)
    if cancelled is None:
        raise HTTPException(404, detail=BULK_JOB_NOT_FOUND)
    # Attempt mode (this handler never commits): the ask is the fact recorded.
    actor = ActorRef.from_user(current_user)
    for project_id, queued in sorted(cancelled["queued_per_project"].items()):
        await record_activity(
            db,
            project_id=project_id,
            event_type="analysis.bulk_cancelled",
            actor=actor,
            entity_id=project_id,
            context={"bulk_job_id": job_id, "workflow_type": cancelled["workflow_type"], "queued": queued},
        )
    return bulk_pipeline_job_service.job_read(job_id, cancelled, progress, now=now)


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
    # W14 AGT-05 / AGT-08: which workflow ran and why, from the row already
    # loaded (no statement). None on a legacy row that never recorded them.
    run_metadata = pipeline.execution_metadata if isinstance(pipeline.execution_metadata, dict) else {}

    return {
        "schema_version": 2,
        "pipeline_run_id": str(pipeline_id),
        "workflow_type": pipeline.workflow_type,
        "workflow_ref": run_metadata.get("workflow_ref"),
        "workflow_source": run_metadata.get("workflow_default_source"),
        "status": pipeline.status,
        # E7.5: the four-value projection clients should branch on.
        "public_status": public_status(pipeline.status),
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
            # W23c I-8: an inline-input invocation is not the run's analysis status.
            inline_invocation.not_inline_pipeline(),
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
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Retrieve the AI-generated markdown summary for a test run (all 4 layers if available).

    Carries the human-review envelope (E8.3): ``requires_human_review``,
    ``review`` and the ``X-TestLookup-AI-Generated`` / ``X-TestLookup-Review-State``
    headers. A deterministic fallback is ``not_applicable``: no AI wrote it.
    """
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
        return await _with_review_envelope(db, response, run_id, fallback, ai_generated=False)

    doc.pop("_id", None)
    summary = normalize_summary_doc(run_id, doc)
    if summary.executive_summary or summary.markdown_report:
        return await _with_review_envelope(db, response, run_id, summary, ai_generated=True)

    fallback = await build_fallback_summary(db, run_id)
    if not fallback:
        raise HTTPException(404, detail="No summary found for this run")
    return await _with_review_envelope(db, response, run_id, fallback, ai_generated=False)


async def _with_review_envelope(
    db: AsyncSession,
    response: Response,
    run_id: str,
    summary: AgentRunSummaryResponse,
    *,
    ai_generated: bool,
) -> AgentRunSummaryResponse:
    """Attach the E8.3 review envelope and headers to a run summary."""
    from app.services.review_envelope import review_envelope_for_run

    envelope = await review_envelope_for_run(db, run_id, ai_generated=ai_generated)
    envelope.apply_headers(response)
    fields = envelope.fields()
    return summary.model_copy(update={
        "requires_human_review": fields["requires_human_review"],
        "review": ReviewBlock(**fields["review"]),
        "ai_disclaimer": fields["ai_disclaimer"],
        "ai_disclaimer_version": fields["ai_disclaimer_version"],
    })


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


async def _run_label(db: AsyncSession, run_id: uuid.UUID) -> str:
    """``Build <n>``, the label every other run event in the feed uses."""
    result = await db.execute(select(TestRun.build_number).where(TestRun.id == run_id))
    build_number = result.scalar_one_or_none()
    return f"Build {build_number}" if build_number is not None else str(run_id)


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
    await record_activity(
        db,
        project_id=scoped_project_id,
        event_type="defect.commander_run",
        actor=ActorRef.from_user(current_user),
        entity_id=run_id,
        entity_label=await _run_label(db, run_id),
        context={
            "cluster_id": cluster_id,
            "defect_id": result.get("defect_id") if isinstance(result, dict) else None,
        },
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
    await record_activity(
        db,
        project_id=scoped_project_id,
        event_type="analysis.regression_watch_run",
        actor=ActorRef.from_user(current_user),
        entity_id=run_id,
        entity_label=await _run_label(db, run_id),
    )
    return {"run_id": str(run_id), "classifications": result}
