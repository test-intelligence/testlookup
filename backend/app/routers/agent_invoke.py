"""Invoke one agent through the public API (architecture E1.2).

* ``POST /api/v1/agents/{agent_id}/invoke`` -- run one agent from the catalog
  (E1.1) on a stored test run.
* ``GET  /api/v1/agents/invocations/{invocation_id}`` -- poll it, including the
  agent's stored output once its stage has completed.
* ``POST /api/v1/agents/invocations/{invocation_id}/retry`` and ``.../cancel`` --
  the pipeline retry and cancel rules, applied to the invocation's run.

An invocation is not a second execution engine. It is an ordinary pipeline run
whose frozen plan selects only the agent and the stages it declares as
dependencies (``agent_planner.build_workflow_plan(invocation_stage=...)``), so
leases, fencing, row-owned retries, cancellation and Finalize -- including the
human review request for a report-producing agent -- apply exactly as they do
to a pipeline. The status is read from that run and never stored twice.

Not yet: ``mode=sync`` (refused with 422 for now), payloads other than a stored
subject, and SSE progress.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _enforce_api_key_project_binding,
    get_current_active_user,
    require_role,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import (
    AgentInvocation,
    AgentPipelineRun,
    AgentStageResult,
    ProjectMember,
    ReviewRequest,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import ReviewBlock
from app.services import agent_catalog, agent_planner
from app.services.activity.service import ActorRef, record as record_activity
from app.services.agent_capability_registry import is_report_producing
from app.services.pipeline_cancellation import request_cancel
from app.services.pipeline_retry_config import decide_retry_mode
from app.services.review_envelope import ReviewEnvelope, envelope_from_review
from app.services.workflow_run_state import (
    REVIEW_NOT_APPLICABLE,
    is_resumable,
    is_terminal,
    normalize_status,
    public_status,
)

router = APIRouter(prefix="/api/v1/agents", tags=["Agent Invocations"])

#: An invocation whose pipeline run has not appeared this long after it was
#: dispatched was lost (broker down, worker gone). It reads ``failed`` so a
#: caller stops polling, and a retry dispatches it again.
DISPATCH_GRACE = timedelta(minutes=10)
#: ``agent_pipeline_runs.max_attempts`` default, reported until the run exists.
_DEFAULT_MAX_ATTEMPTS = 5


class AgentInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID = Field(
        description="Assertion: must equal the project of the test run named in input, else 400.",
    )
    input: dict[str, Any] = Field(
        description="The agent's <StageName>InvokeInput, as published by GET /api/v1/agents/catalog/{agent_id}.",
    )
    mode: Literal["async", "sync"] = "async"
    correlation_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class AgentInvocationResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    test_run_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    mode: Literal["sync", "async"]
    status: Literal["in_progress", "completed", "failed", "passed"]
    attempt: int
    max_attempts: int
    next_retry_at: Optional[datetime] = None
    error: Optional[str] = None
    output: Optional[dict[str, Any]] = Field(
        default=None,
        description="The invoked agent's stored stage output, once that stage has completed.",
    )
    requires_human_review: bool
    review: ReviewBlock
    created_at: Optional[datetime] = None
    links: dict[str, str]


# -- access guard ---------------------------------------------------------------


def require_invocation_access():
    """Resolve ``{invocation_id}`` to its project and check the caller may see it.

    Modelled on ``require_investigation_access``. A caller outside the project
    gets 404, not 403: a UUID-only route must not confirm that another tenant's
    invocation exists (architecture section 3.1). A project-bound API key reaches
    only its own project; ADMIN bypasses membership once the row exists.
    """

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        raw = request.path_params.get("invocation_id")
        if not raw:
            return current_user
        try:
            invocation_uuid = uuid.UUID(str(raw))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invocation not found")
        project_id = (
            await db.execute(select(AgentInvocation.project_id).where(AgentInvocation.id == invocation_uuid))
        ).scalar_one_or_none()
        if project_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invocation not found")
        _enforce_api_key_project_binding(current_user, project_id)
        role_value = getattr(current_user.role, "value", current_user.role)
        if str(role_value) == UserRole.ADMIN.value:
            return current_user
        member = (
            await db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.user_id == current_user.id,
                    ProjectMember.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invocation not found")
        return current_user

    return _check


# -- projection -----------------------------------------------------------------


def _stage_output(stage: Any) -> Optional[dict[str, Any]]:
    """The invoked stage's stored output, only once that stage has completed."""
    if stage is None or getattr(stage, "status", None) != "completed":
        return None
    data = getattr(stage, "checkpoint_data", None) or getattr(stage, "result_data", None)
    return data if isinstance(data, dict) else None


def project_invocation(
    invocation: Any,
    pipeline: Any,
    review: Any,
    *,
    stage: Any = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The public view of an invocation, read from its pipeline run, review and stage."""
    now = now or datetime.now(timezone.utc)
    links = {"self": f"/api/v1/agents/invocations/{invocation.id}"}
    if pipeline is None:
        dispatched = getattr(invocation, "dispatched_at", None) or invocation.created_at or now
        if dispatched.tzinfo is None:
            dispatched = dispatched.replace(tzinfo=timezone.utc)
        lost = now - dispatched > DISPATCH_GRACE
        run_state: dict[str, Any] = {
            "status": "failed" if lost else "in_progress",
            "attempt": 1,
            "max_attempts": _DEFAULT_MAX_ATTEMPTS,
            "next_retry_at": None,
            "error": "invocation_not_started: no worker picked it up" if lost else None,
        }
    else:
        run_state = {
            "status": public_status(pipeline.status),
            "attempt": int(pipeline.attempt or 1),
            "max_attempts": int(pipeline.max_attempts or _DEFAULT_MAX_ATTEMPTS),
            "next_retry_at": pipeline.next_retry_at,
            "error": pipeline.error,
        }
        links["pipeline"] = f"/api/v1/agents/pipelines/{pipeline.id}"

    if review is not None:
        envelope = envelope_from_review(review)
        links["review"] = f"/api/v1/reviews/{review.id}"
    elif not is_report_producing(invocation.stage_name) or (
        pipeline is not None and pipeline.review_policy == REVIEW_NOT_APPLICABLE
    ):
        envelope = ReviewEnvelope(
            ai_generated=False,
            state="not_applicable",
            message="No report to review: this agent's output is not a report contract.",
        )
    else:
        envelope = ReviewEnvelope(
            ai_generated=True,
            state="pending_review",
            message=(
                "AI-generated report. It is a draft until a person accepts its review, "
                "which is created when the run finishes."
            ),
        )
    return {
        "id": invocation.id,
        "project_id": invocation.project_id,
        "agent_id": invocation.agent_id,
        "test_run_id": invocation.test_run_id,
        "pipeline_run_id": invocation.pipeline_run_id,
        "mode": invocation.mode,
        **run_state,
        "output": _stage_output(stage),
        "requires_human_review": envelope.ai_generated,
        "review": envelope.block(),
        "created_at": invocation.created_at,
        "links": links,
    }


async def _load_invocation_or_404(db: AsyncSession, invocation_id: uuid.UUID) -> Any:
    invocation = (
        await db.execute(select(AgentInvocation).where(AgentInvocation.id == invocation_id))
    ).scalar_one_or_none()
    if invocation is None:
        raise HTTPException(status_code=404, detail="Invocation not found")
    return invocation


async def _load_pipeline(db: AsyncSession, invocation: Any) -> Any:
    return (
        await db.execute(select(AgentPipelineRun).where(AgentPipelineRun.id == invocation.pipeline_run_id))
    ).scalar_one_or_none()


async def _invocation_view(db: AsyncSession, invocation: Any) -> dict[str, Any]:
    pipeline = await _load_pipeline(db, invocation)
    review = stage = None
    if pipeline is not None:
        review = (
            await db.execute(
                select(ReviewRequest)
                .where(ReviewRequest.pipeline_run_id == invocation.pipeline_run_id)
                .order_by(ReviewRequest.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        stage = (
            await db.execute(
                select(AgentStageResult)
                .where(
                    AgentStageResult.pipeline_run_id == invocation.pipeline_run_id,
                    AgentStageResult.stage_name == invocation.stage_name,
                )
                .order_by(AgentStageResult.attempt.desc())
                .limit(1)
            )
        ).scalars().first()
    return project_invocation(invocation, pipeline, review, stage=stage)


async def _run_label(db: AsyncSession, test_run_id: Any) -> str:
    build = (
        await db.execute(select(TestRun.build_number).where(TestRun.id == test_run_id))
    ).scalar_one_or_none()
    return f"Build {build}" if build else str(test_run_id)


async def _current_mode_snapshot() -> dict[str, Any]:
    from app.agents.workflow import _resolve_analysis_mode_snapshot  # noqa: PLC0415

    return await _resolve_analysis_mode_snapshot()


def _validate_invocation(agent_id: str, body: AgentInvokeRequest) -> tuple[str, str, uuid.UUID]:
    """(stage_name, workflow_type, test_run_id), or the 4xx that refuses the call."""
    if not agent_catalog.AGENT_ID_PATTERN.match(agent_id):
        raise HTTPException(
            status_code=422,
            detail="agent_id must look like agent.<name>.v<version>, for example agent.summary.v1",
        )
    spec = agent_catalog.capability_for(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    workflow_type = agent_planner.invocation_workflow_type(spec.stage_name)
    if workflow_type is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{agent_id} cannot be invoked on its own yet: it runs only inside a workflow "
                "(a cluster child, runtime or investigation stage)"
            ),
        )
    if body.mode == "sync":
        raise HTTPException(
            status_code=422,
            detail="mode=sync is not available yet; invoke with mode=async and poll links.self",
        )
    try:
        parsed = agent_catalog.input_wrapper(agent_id).model_validate(body.input)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        ) from None
    payload = getattr(parsed, "payload", None)
    if not isinstance(payload, agent_catalog.SubjectRef):
        raise HTTPException(
            status_code=422,
            detail='Only a stored subject can be invoked in this release: send input.payload = {"test_run_id": "<uuid>"}',
        )
    return spec.stage_name, workflow_type, payload.test_run_id


async def _in_progress_invocation(
    db: AsyncSession, test_run_id: uuid.UUID, agent_id: str
) -> Optional[dict[str, Any]]:
    """The newest invocation of this agent on this run, if it is still in progress."""
    latest = (
        await db.execute(
            select(AgentInvocation)
            .where(AgentInvocation.test_run_id == test_run_id, AgentInvocation.agent_id == agent_id)
            .order_by(AgentInvocation.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest is None:
        return None
    view = await _invocation_view(db, latest)
    return view if view["status"] == "in_progress" else None


# -- routes (literal paths first: /invocations/... before /{agent_id}/invoke) -----


@router.get("/invocations/{invocation_id}", response_model=AgentInvocationResponse)
async def get_invocation(
    invocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_invocation_access()),
):
    """Poll one invocation: status, attempts, output and review state, read from its run."""
    invocation = await _load_invocation_or_404(db, invocation_id)
    return await _invocation_view(db, invocation)


@router.post("/invocations/{invocation_id}/retry", response_model=AgentInvocationResponse, status_code=202)
async def retry_invocation(
    invocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_invocation_access()),
):
    """Retry a failed invocation: a new attempt of the same invocation (E1.2).

    The pipeline retry rules apply to the invocation's run. It is refused with
    409 while the run is in progress, after a clean finish, or at the attempt
    ceiling; the last two carry ``links.rerun`` to invoke the agent again.

    A retry resumes the SAME run, so the frozen plan (this agent and its
    dependencies only) is kept. When the agent configuration changed since the
    run started, resuming would replay work authorised under the old
    configuration, and a rerun needs a new run -- which is a new invocation --
    so that is 409 with ``links.rerun`` too. An invocation whose run never
    started is dispatched again.
    """
    invocation = await _load_invocation_or_404(db, invocation_id)
    pipeline = await _load_pipeline(db, invocation)
    base = {"invocation_id": str(invocation.id)}
    rerun = {"rerun": f"/api/v1/agents/{invocation.agent_id}/invoke"}

    if pipeline is None:
        if project_invocation(invocation, None, None)["status"] != "failed":
            raise HTTPException(status_code=409, detail={**base, "message": "Invocation has not started yet"})
        mode = "redispatch"
        # Restart the dispatch clock; created_at keeps the original request time.
        invocation.dispatched_at = datetime.now(timezone.utc)
    else:
        current = normalize_status(pipeline.status)
        if not is_terminal(current):
            raise HTTPException(
                status_code=409,
                detail={**base, "message": "Invocation is still in progress", "status": public_status(current)},
            )
        if not is_resumable(pipeline.status, pipeline.execution_metadata):
            raise HTTPException(
                status_code=409,
                detail={
                    **base,
                    "message": "Invocation finished successfully; nothing to retry",
                    "status": public_status(current),
                    "links": rerun,
                },
            )
        attempt = int(pipeline.attempt or 1)
        max_attempts = int(pipeline.max_attempts or _DEFAULT_MAX_ATTEMPTS)
        if attempt >= max_attempts:
            raise HTTPException(
                status_code=409,
                detail={
                    **base,
                    "message": (
                        f"Invocation reached its attempt ceiling ({attempt}/{max_attempts}); "
                        "invoke the agent again instead"
                    ),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "links": rerun,
                },
            )
        plan = decide_retry_mode(pipeline.execution_metadata, await _current_mode_snapshot())
        if plan.is_rerun:
            raise HTTPException(
                status_code=409,
                detail={
                    **base,
                    "message": (
                        "The agent configuration changed since this invocation ran; invoke the "
                        "agent again to run under the current configuration"
                    ),
                    "reason": plan.reason,
                    "links": rerun,
                },
            )
        mode = "resume"

    await record_activity(
        db,
        project_id=invocation.project_id,
        event_type="analysis.retried",
        actor=ActorRef.from_user(current_user),
        entity_id=invocation.test_run_id,
        entity_label=await _run_label(db, invocation.test_run_id),
        context={
            "mode": mode,
            "invocation_id": str(invocation.id),
            "agent_id": invocation.agent_id,
            "pipeline_run_id": str(invocation.pipeline_run_id),
        },
    )
    # Commit BEFORE dispatch: the worker reads what this request wrote.
    await db.commit()
    if mode == "resume":
        from app.worker.tasks import resume_agent_pipeline

        resume_agent_pipeline.apply_async(
            kwargs={"pipeline_run_id": str(invocation.pipeline_run_id), "build_number": "manual-retry"},
            queue="ai_analysis",
        )
    else:
        from app.worker.tasks import run_agent_invocation

        run_agent_invocation.apply_async(kwargs={"invocation_id": str(invocation.id)}, queue="ai_analysis")
    return await _invocation_view(db, invocation)


@router.post("/invocations/{invocation_id}/cancel", response_model=AgentInvocationResponse, status_code=202)
async def cancel_invocation(
    invocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_invocation_access()),
):
    """Cancel an invocation cooperatively, exactly as its pipeline run is cancelled.

    A run with no live worker stops now; a running one is asked to stop and
    terminalises at its next stage boundary. Cancellation is sticky: an
    automatic retry never brings it back. An invocation whose run has not been
    created yet has nothing to cancel (409).
    """
    invocation = await _load_invocation_or_404(db, invocation_id)
    pipeline = await _load_pipeline(db, invocation)
    base = {"invocation_id": str(invocation.id)}
    if pipeline is None:
        raise HTTPException(
            status_code=409,
            detail={**base, "message": "Invocation has not started yet; there is no run to cancel"},
        )
    outcome = await request_cancel(db, pipeline.id, requested_by=getattr(current_user, "email", None))
    if not outcome.accepted:
        raise HTTPException(
            status_code=409,
            detail={**base, "message": "Invocation has already finished", **outcome.as_dict()},
        )
    await record_activity(
        db,
        project_id=invocation.project_id,
        event_type="analysis.cancelled",
        actor=ActorRef.from_user(current_user),
        entity_id=invocation.test_run_id,
        entity_label=await _run_label(db, invocation.test_run_id),
        context={
            "invocation_id": str(invocation.id),
            "agent_id": invocation.agent_id,
            "pipeline_run_id": str(pipeline.id),
            "from_status": outcome.status,
            "terminal": outcome.terminal,
            "reason": outcome.reason,
        },
    )
    await db.commit()
    return await _invocation_view(db, invocation)


@router.post("/{agent_id}/invoke", response_model=AgentInvocationResponse, status_code=202)
async def invoke_agent(
    agent_id: str,
    body: AgentInvokeRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Invoke one agent on a stored test run. Returns 202 and a poll link.

    The project is derived from the test run; ``project_id`` in the body is an
    assertion that must match it. An invocation of the same agent on the same
    run that is still in progress is returned as-is with 200.
    """
    stage_name, workflow_type, test_run_id = _validate_invocation(agent_id, body)
    run = (await db.execute(select(TestRun).where(TestRun.id == test_run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="TestRun not found")
    await resolve_project_scope(db, current_user, str(run.project_id))
    if body.project_id != run.project_id:
        raise HTTPException(status_code=400, detail="project_id does not match the test run's project")

    existing = await _in_progress_invocation(db, run.id, agent_id)
    if existing is not None:
        response.status_code = 200
        return existing

    now = datetime.now(timezone.utc)
    invocation = AgentInvocation(
        id=uuid.uuid4(),
        project_id=run.project_id,
        agent_id=agent_id,
        stage_name=stage_name,
        test_run_id=run.id,
        pipeline_run_id=uuid.uuid4(),
        workflow_type=workflow_type,
        mode=body.mode,
        requested_by=getattr(current_user, "id", None),
        correlation_id=body.correlation_id,
        created_at=now,
        dispatched_at=now,
    )
    db.add(invocation)
    await record_activity(
        db,
        project_id=run.project_id,
        event_type="agent.invoked",
        actor=ActorRef.from_user(current_user),
        entity_id=run.id,
        entity_label=f"Build {run.build_number}",
        context={"agent_id": agent_id, "invocation_id": str(invocation.id)},
    )
    # Commit BEFORE dispatch: the worker loads the row by id.
    await db.commit()

    from app.worker.tasks import run_agent_invocation

    run_agent_invocation.apply_async(kwargs={"invocation_id": str(invocation.id)}, queue="ai_analysis")
    return project_invocation(invocation, None, None)
