"""Invoke one agent through the public API (architecture E1.2).

* ``POST /api/v1/agents/{agent_id}/invoke`` -- run one agent from the catalog
  (E1.1) on a stored test run, asynchronously (202) or, for a sync-eligible
  agent, waiting a bounded time for the result.
* ``GET  /api/v1/agents/invocations/{invocation_id}`` -- poll it, including the
  agent's stored output once its stage has completed.
* ``POST /api/v1/agents/invocations/{invocation_id}/retry`` and ``.../cancel`` --
  the pipeline retry and cancel rules, applied to the invocation's run.
* ``POST /api/v1/agents/invocations/{invocation_id}/events/ticket`` then
  ``GET .../events?ticket=`` -- progress as server-sent events.

An invocation is not a second execution engine. It is an ordinary pipeline run
whose frozen plan selects only the agent and the stages it declares as
dependencies (``agent_planner.build_workflow_plan(invocation_stage=...)``), so
leases, fencing, row-owned retries, cancellation and Finalize -- including the
human review request for a report-producing agent -- apply exactly as they do
to a pipeline. The status is read from that run and never stored twice. That
holds for ``mode=sync`` too: the worker still runs it, and the request only
waits for it.

Not yet: payloads other than a stored subject.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.services import agent_catalog, agent_planner, invocation_idempotency, invocation_stream
from app.services.activity.service import ActorRef, record as record_activity
from app.services.agent_capability_registry import is_report_producing, is_sync_eligible
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
    mode: Literal["async", "sync"] = Field(
        default="async",
        description=(
            "sync waits for the result, and only for sync-eligible agents (see the catalog); "
            "any other agent runs async and answers 202."
        ),
    )
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


class StreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int
    links: dict[str, str]


# -- sync slots -----------------------------------------------------------------


class _SyncSlots:
    """Process-wide bound on requests waiting for a sync invocation (section 3.1).

    A counter, not an ``asyncio.Semaphore``: acquiring never waits (a full pool
    is a 503, not a queue), and the check and increment have no await between
    them, so they are atomic on the event loop.
    """

    def __init__(self) -> None:
        self.in_flight = 0

    def try_acquire(self) -> bool:
        if self.in_flight >= max(1, int(settings.AGENT_INVOKE_SYNC_CONCURRENCY)):
            return False
        self.in_flight += 1
        return True

    def release(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)


SYNC_SLOTS = _SyncSlots()


# -- access guards ----------------------------------------------------------------


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


def require_invocation_stream_ticket():
    """Authorise an invocation event stream by its single-use ticket.

    ``EventSource`` cannot send ``Authorization``, so the stream's credential is
    a ticket that ``require_invocation_access`` already gated at issue time. It
    is bound to the invocation in the path and consumed on use. Anything else is
    401.
    """

    async def _check(
        request: Request,
        ticket: str = Query(..., min_length=16, max_length=128),
    ) -> uuid.UUID:
        try:
            invocation_uuid = uuid.UUID(str(request.path_params.get("invocation_id")))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired stream ticket")
        if not await invocation_stream.redeem_stream_ticket(ticket, invocation_uuid):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired stream ticket")
        return invocation_uuid

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
    # populate_existing: a sync wait re-reads the same row in one session, and
    # the identity map would otherwise keep answering with the first read.
    return (
        await db.execute(
            select(AgentPipelineRun)
            .where(AgentPipelineRun.id == invocation.pipeline_run_id)
            .execution_options(populate_existing=True)
        )
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
                .execution_options(populate_existing=True)
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
                .execution_options(populate_existing=True)
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


async def _wait_for_terminal(
    db: AsyncSession,
    invocation: Any,
    *,
    wait_seconds: float,
    poll_interval: float = 0.5,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Re-read the invocation until its run leaves ``in_progress`` or the wait ends."""
    deadline = clock() + max(0.0, float(wait_seconds))
    while True:
        await sleep(poll_interval)
        view = await _invocation_view(db, invocation)
        if view["status"] != "in_progress" or clock() >= deadline:
            return view


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


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def invocation_event_stream(
    invocation_id: uuid.UUID,
    request: Any,
    *,
    poll_interval: float = 1.0,
    heartbeat_every: float = 15.0,
    max_seconds: float = 1800.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Server-sent events for one invocation: one ``invocation`` event per change.

    Each read uses its own session: a request-scoped session would be closed
    before a streamed body is sent. The stream ends when the run leaves
    ``in_progress``, when the client disconnects, or after ``max_seconds``.
    """
    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415

    started = last_sent = clock()
    last_key: Optional[tuple] = None
    while True:
        if await request.is_disconnected():
            return
        async with AsyncSessionLocal() as db:
            invocation = (
                await db.execute(select(AgentInvocation).where(AgentInvocation.id == invocation_id))
            ).scalar_one_or_none()
            if invocation is None:
                yield _sse("gone", {"invocation_id": str(invocation_id)})
                return
            view = await _invocation_view(db, invocation)
        key = (
            view["status"], view["attempt"], view["review"]["state"],
            str(view["next_retry_at"]), view["output"] is not None,
        )
        if key != last_key:
            last_key = key
            last_sent = clock()
            yield _sse("invocation", AgentInvocationResponse(**view).model_dump(mode="json"))
        if view["status"] != "in_progress":
            return
        if clock() - started >= max_seconds:
            yield _sse("timeout", {"invocation_id": str(invocation_id), "poll": view["links"]["self"]})
            return
        if clock() - last_sent >= heartbeat_every:
            last_sent = clock()
            yield ": heartbeat\n\n"
        await sleep(poll_interval)


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


# activity: none -- issues a 60-second read credential for the caller's own event stream; nothing in the project changes.
@router.post("/invocations/{invocation_id}/events/ticket", response_model=StreamTicketResponse)
async def issue_invocation_stream_ticket(
    invocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_invocation_access()),
):
    """A single-use ticket for ``GET .../events`` (EventSource cannot send Authorization)."""
    await _load_invocation_or_404(db, invocation_id)
    try:
        ticket, ttl = await invocation_stream.issue_stream_ticket(invocation_id, getattr(current_user, "id", None))
    except Exception:  # noqa: BLE001 -- the ticket store is down: say so, do not 500
        raise HTTPException(status_code=503, detail="Stream tickets are unavailable; poll links.self instead") from None
    return {
        "ticket": ticket,
        "expires_in": ttl,
        "links": {"events": f"/api/v1/agents/invocations/{invocation_id}/events?ticket={ticket}"},
    }


@router.get("/invocations/{invocation_id}/events")
async def stream_invocation_events(
    invocation_id: uuid.UUID,
    request: Request,
    _: uuid.UUID = Depends(require_invocation_stream_ticket()),
):
    """Progress of one invocation as server-sent events, until its run finishes."""
    return StreamingResponse(
        invocation_event_stream(invocation_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


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


async def _idempotent_replay(
    db: AsyncSession, user_id: Any, idempotency_key: str, fingerprint: str,
) -> Optional[dict[str, Any]]:
    """The invocation this user's key already created, or None. 422 when the request differs."""
    if user_id is None:
        return None
    existing = (
        await db.execute(
            select(AgentInvocation).where(
                AgentInvocation.requested_by == user_id,
                AgentInvocation.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_sha256 != fingerprint:
        raise HTTPException(status_code=422, detail=_KEY_REUSED)
    return await _invocation_view(db, existing)


_KEY_REUSED = "Idempotency-Key was already used for a different request; send a new key"


@router.post("/{agent_id}/invoke", response_model=AgentInvocationResponse, status_code=202)
async def invoke_agent(
    agent_id: str,
    body: AgentInvokeRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    idempotency_key: Annotated[
        Optional[str],
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
            description=(
                "Client-generated key (UUID or ULID). The same key with the same request returns "
                "the invocation it created (200); with a different request, 422; while the first "
                "request is still being handled, 409. Scoped to your user and project."
            ),
        ),
    ] = None,
):
    """Invoke one agent on a stored test run.

    ``mode=async`` (the default) answers 202 with a poll link. ``mode=sync`` on a
    sync-eligible agent waits up to ``AGENT_INVOKE_SYNC_WAIT_SECONDS`` for the run
    to finish: 200 with the result if it did, 202 if it is still going. A full
    pool of sync slots is 503 with ``Retry-After``. ``mode=sync`` on any other
    agent runs async.

    The project is derived from the test run; ``project_id`` in the body is an
    assertion that must match it. An invocation of the same agent on the same
    run that is still in progress is returned as-is with 200. With an
    ``Idempotency-Key``, a repeated request returns the invocation it created
    (E1.3).
    """
    stage_name, workflow_type, test_run_id = _validate_invocation(agent_id, body)
    sync = body.mode == "sync" and is_sync_eligible(stage_name)
    run = (await db.execute(select(TestRun).where(TestRun.id == test_run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="TestRun not found")
    await resolve_project_scope(db, current_user, str(run.project_id))
    if body.project_id != run.project_id:
        raise HTTPException(status_code=400, detail="project_id does not match the test run's project")

    user_id = getattr(current_user, "id", None)
    fingerprint: Optional[str] = None
    claimed = False
    if idempotency_key is not None:
        fingerprint = invocation_idempotency.request_fingerprint(agent_id, body.model_dump(mode="json"))
        replay = await _idempotent_replay(db, user_id, idempotency_key, fingerprint)
        if replay is not None:
            response.status_code = 200
            return replay
        claim = await invocation_idempotency.claim(user_id, run.project_id, agent_id, idempotency_key, fingerprint)
        if claim == "conflict":
            raise HTTPException(status_code=422, detail=_KEY_REUSED)
        if claim in ("in_flight", "done"):
            # ``done``: the first request committed after the lookup above; read it back.
            replay = await _idempotent_replay(db, user_id, idempotency_key, fingerprint) if claim == "done" else None
            if replay is not None:
                response.status_code = 200
                return replay
            raise HTTPException(
                status_code=409,
                detail="A request with this Idempotency-Key is still being handled; retry shortly",
                headers={"Retry-After": "1"},
            )
        claimed = claim == "claimed"

    holds_slot = False
    try:
        existing = await _in_progress_invocation(db, run.id, agent_id)
        if existing is not None:
            response.status_code = 200
            return existing

        if sync:
            if not SYNC_SLOTS.try_acquire():
                raise HTTPException(
                    status_code=503,
                    detail="All synchronous invocation slots are busy; retry shortly or invoke with mode=async",
                    headers={"Retry-After": "2"},
                )
            holds_slot = True

        now = datetime.now(timezone.utc)
        invocation = AgentInvocation(
            id=uuid.uuid4(),
            project_id=run.project_id,
            agent_id=agent_id,
            stage_name=stage_name,
            test_run_id=run.id,
            pipeline_run_id=uuid.uuid4(),
            workflow_type=workflow_type,
            mode="sync" if sync else "async",
            requested_by=user_id,
            correlation_id=body.correlation_id,
            created_at=now,
            dispatched_at=now,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
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
        try:
            await db.commit()
        except IntegrityError:
            # The unique (requested_by, idempotency_key) index: a concurrent request
            # with the same key committed first (the Redis lock was unavailable).
            await db.rollback()
            replay = (
                await _idempotent_replay(db, user_id, idempotency_key, fingerprint)
                if idempotency_key is not None and fingerprint is not None
                else None
            )
            if replay is None:
                raise
            response.status_code = 200
            return replay
        if claimed and idempotency_key is not None and fingerprint is not None:
            await invocation_idempotency.complete(
                user_id, run.project_id, agent_id, idempotency_key, fingerprint, invocation.id,
            )
            claimed = False

        from app.worker.tasks import run_agent_invocation

        run_agent_invocation.apply_async(kwargs={"invocation_id": str(invocation.id)}, queue="ai_analysis")
        if not sync:
            return project_invocation(invocation, None, None)
        view = await _wait_for_terminal(
            db, invocation, wait_seconds=float(settings.AGENT_INVOKE_SYNC_WAIT_SECONDS),
        )
        if view["status"] != "in_progress":
            response.status_code = 200
        return view
    finally:
        if holds_slot:
            SYNC_SLOTS.release()
        if claimed and idempotency_key is not None and fingerprint is not None:
            # Nothing was committed under this key: free it so the client can retry.
            await invocation_idempotency.release(user_id, run.project_id, agent_id, idempotency_key, fingerprint)
