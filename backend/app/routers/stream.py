"""
Live Test Execution Streaming API.

The router keeps only transport concerns such as SSE subscription state.
Session lifecycle, event ingestion, and dashboard aggregation live in
`app.services.stream_service`.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Annotated, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    StreamingApiKeyContext,
    ensure_ingest_role,
    get_accessible_project_ids,
    get_api_key_context,
    get_current_active_user,
    get_streaming_api_key_context,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import LiveSession, TestRun, User
from app.models.schemas import (
    ActiveSessionsResponse,
    LiveEventBatch,
    LiveHttpExchangesIn,
    LiveHttpExchangesResponse,
    LiveSessionCreate,
    LiveStreamIngestRequest,
)
from app.models.viz_contracts import MAX_SUITE_NAME_LENGTH, MAX_SUITES
from app.services import stream_service
from app.services.activity.service import ActorRef, record as record_activity
from app.services.analytics_scope import parse_suite_filter
from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
from app.services.ingestion_rate_limit import enforce_ingest_rate_limit

router = APIRouter(prefix="/api/v1/stream", tags=["Live Stream"])

_sse_subscribers: dict[str, set[asyncio.Queue]] = {}
_sse_bootstrap_floors: dict[asyncio.Queue, str] = {}


@router.post("/sessions", response_model=stream_service.LiveSessionResponse, status_code=201)
async def create_session(
    payload: LiveSessionCreate,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    current_user, bound_project_id = auth

    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user account")
    ensure_ingest_role(current_user)

    # Project-scoped API key enforcement moved into stream_service.create_session
    # so it can compare against the *resolved* project — payload.project_id may
    # be a name or a UUID.
    response = await stream_service.create_session(
        db, payload, bound_project_id=bound_project_id, user=current_user,
    )
    # W21 ACT-01. Outcome mode: staged on this session, so it commits with the
    # session and its run stub or not at all.
    await record_activity(
        db,
        project_id=response.project_id,
        event_type="run.live_started",
        actor=ActorRef.from_user(current_user),
        entity_id=response.run_id,
        entity_label=f"Build {payload.build_number or str(response.run_id)[:8]}",
        context={"framework": getattr(payload, "framework", None)},
    )
    await db.commit()
    # VIZ-212: the session start commits an IN_PROGRESS TestRun stub, which
    # the dashboard's run counts already include.
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(response.project_id)
    return response


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, uuid.UUID | None] = Depends(get_api_key_context),
):
    # ``require_live_session_access`` was removed from this route because it
    # depends on get_current_active_user (JWT-only) and breaks SDK callers
    # using X-API-Key. The service checks the key's binding or, for a JWT or
    # user-scoped key, the caller's membership.
    current_user, bound_project_id = auth
    return await stream_service.get_session(
        db, session_id, bound_project_id=bound_project_id, user=current_user,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, uuid.UUID | None] = Depends(get_api_key_context),
):
    current_user, bound_project_id = auth
    # Closing finalizes the run (counts, AI pipeline): a write, like opening.
    ensure_ingest_role(current_user)
    closed_project_id = await stream_service.close_session(
        db, session_id, bound_project_id=bound_project_id, user=current_user,
    )
    if closed_project_id is not None:
        # W21 ACT-01. The run's id IS the session id (the stub is written with
        # it). Outcome mode, committed with the close.
        build_number = await db.scalar(
            select(TestRun.build_number).where(TestRun.id == uuid.UUID(session_id))
        )
        await record_activity(
            db,
            project_id=closed_project_id,
            event_type="run.live_finalized",
            actor=ActorRef.from_user(current_user),
            entity_id=session_id,
            entity_label=f"Build {build_number or session_id[:8]}",
        )
    await db.commit()
    if closed_project_id is not None:
        # VIZ-212: the close created/updated the TestRun analytics reads.
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(closed_project_id)
    await stream_service.finalize_closed_session_redis(session_id)
    # W25e E-1: publish the staged persist_live_session now instead of at the
    # next relay tick (0-60 s). Whether or not THIS call closed the session: an
    # earlier close whose publish was deferred is picked up too. Never raises;
    # a deferred publish stays relay-recoverable.
    from app.worker.downstream_dispatch import publish_staged_now

    await publish_staged_now(db, run_id=session_id)


@router.post("/events/batch", response_model=stream_service.LiveEventBatchResponse, status_code=202)
async def ingest_event_batch(
    batch: LiveEventBatch,
    x_session_token: str = Header(..., alias="X-Session-Token"),
):
    # Two-layer admission gate. Order matters: backpressure first so a
    # red-line Redis short-circuits the rate-limit Redis op too. The
    # rate-limit Redis op itself is bounded but stacking it after the
    # backpressure check makes the failure path strictly cheaper.
    await enforce_redis_memory_backpressure()
    # ``batch`` doesn't carry project_id (the SDK only sends session_id +
    # run_id; the project is resolved server-side from the session token).
    # We resolve it once here so the rate-limit charge lands on the right
    # bucket — otherwise a noisy session's project escapes the per-project
    # gate.
    project_id = await stream_service.resolve_project_id_for_session(
        batch.session_id, x_session_token,
    )
    if project_id is not None:
        await enforce_ingest_rate_limit(str(project_id))
    return await stream_service.ingest_event_batch(batch, x_session_token)


@router.post("/ingest", response_model=stream_service.LiveStreamIngestResponse, status_code=202)
async def ingest_via_api_key(
    payload: LiveStreamIngestRequest,
    db: AsyncSession = Depends(get_db),
    auth: StreamingApiKeyContext = Depends(get_streaming_api_key_context),
):
    """Stream test results using only an API key — no /sessions ceremony.

    Auth: ``X-API-Key`` only. The key must be project-scoped (so the server
    can derive ``project_id`` itself) and carry the ``stream:write`` scope.
    The first call for a given ``run_id`` auto-creates a live session;
    subsequent calls reuse it.
    """
    # See the ``/events/batch`` handler — same two-layer gate, but the
    # API-key path already has the project_id from auth so the rate-limit
    # call doesn't need a separate resolution.
    await enforce_redis_memory_backpressure()
    await enforce_ingest_rate_limit(str(auth.project_id))
    response = await stream_service.ingest_via_api_key(
        db=db,
        project_id=auth.project_id,
        api_key_name=auth.api_key_name,
        request=payload,
    )
    await db.commit()
    has_run_complete = any(
        (
            event.model_dump() if hasattr(event, "model_dump") else dict(event)
        ).get("event_type") == "run_complete"
        for event in payload.events
    )
    if has_run_complete:
        # VIZ-212: a run_complete closed the session inside the service call,
        # so the commit above wrote the run analytics reads.
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(auth.project_id)
        await stream_service.finalize_closed_session_redis(response.session_id)
        # W25e E-1: the close staged persist_live_session; publish it now.
        from app.worker.downstream_dispatch import publish_staged_now

        await publish_staged_now(db, run_id=response.session_id)
    return response


@router.post("/http-exchanges", response_model=LiveHttpExchangesResponse, status_code=202)
async def ingest_http_exchanges(
    payload: LiveHttpExchangesIn,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, uuid.UUID | None] = Depends(get_api_key_context),
):
    """W23d AGT-14: the captured HTTP exchanges of ONE live test (opt-in capture).

    Authenticated like ``POST /ingest`` (JWT or API key, the ingest role). The
    BODY ``session_id`` names the live session; the project, the run and the
    test case id are DERIVED from that session, never read from the body. A key
    bound to another project is refused, the caller must be a member of the
    session's project, and the body ``run_id`` must be the session's own. The
    exchanges were already cut and redacted by the model; the store redacts
    them again (idempotent) and never fails the request on a Mongo error.
    """
    from app.services import http_exchange_store
    from app.services.live_session_drainer import live_test_case_id

    current_user, bound_project_id = auth
    ensure_ingest_role(current_user)
    try:
        session_uuid = uuid.UUID(payload.session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    session = await db.get(LiveSession, session_uuid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if bound_project_id is not None and bound_project_id != session.project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is restricted to a different project",
        )
    await resolve_project_scope(db, current_user, str(session.project_id))
    if session.run_id != payload.run_id:
        raise stream_service.LiveBatchConflictError("run_id does not belong to this live session")
    # Admission gates after authorisation (the /ingest order): backpressure first.
    await enforce_redis_memory_backpressure()
    await enforce_ingest_rate_limit(str(session.project_id))

    # A live run's TestRun.id IS its session id (the stub row is written with it).
    run_uuid = stream_service.canonical_test_run_uuid(str(session.id))
    test_case_id = live_test_case_id(run_uuid, payload.test_name, payload.class_name)
    counts = await http_exchange_store.write_exchanges(
        project_id=str(session.project_id),
        test_run_id=str(run_uuid),
        build_number=session.build_number or str(run_uuid)[:8],
        run_created_at=session.started_at,
        items=[(str(test_case_id), payload.exchanges, payload.exchanges_dropped)],
        source="live",
    )
    return LiveHttpExchangesResponse(
        stored=counts.stored, truncated=counts.truncated, dropped=counts.dropped,
        test_case_id=str(test_case_id),
    )


@router.get("/active", response_model=ActiveSessionsResponse)
async def list_active_sessions(
    project_id: Optional[str] = None,
    # E3 (m3): repeatable, OR within, filtered server-side before each DB
    # source's page cap. ``Annotated`` so a direct call that omits it gets
    # ``None``, not FastAPI's ``Query`` object.
    suite_name: Annotated[
        Optional[list[str]],
        Query(description=(
            f"Repeatable (OR, at most {MAX_SUITES}), 1-{MAX_SUITE_NAME_LENGTH} characters: "
            "sessions in any of these suites; trimmed, case-insensitive."
        )),
    ] = None,
    days: int = Query(
        7,
        ge=0,
        le=365,
        description=(
            "Cutoff for *completed* sessions/runs to include alongside the always-"
            "current active set. 1 = last 24 hours; 0 = no cutoff. Default 7 "
            "preserves the prior hardcoded window."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    # C1 before any database access: an empty, over-long or 51st suite is a
    # 422 with the analytics error body (``suite_name_length`` / ``suite_cap``).
    # One name reaches the service as the same string it always did.
    suites = parse_suite_filter(suite_name)
    # Tenant isolation:
    # - ADMIN (accessible=None): sees everything; no filter.
    # - Non-admin with specific project_id: verify they are a member of that project.
    # - Non-admin without project_id: scope the listing to every project they belong
    #   to (previously this branch returned an empty list, hiding the user's own
    #   sessions from the live dashboard).
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id:
        # Shape check FIRST, for every role. This used to live inside the
        # ``accessible is not None`` branch below, so it ran for non-admins only:
        # ``get_accessible_project_ids`` returns None for an ADMIN, which skipped
        # the check entirely and let a raw string (a stale link, or the
        # frontend-only ALL_PROJECTS_ID sentinel "all") through to the query. The
        # admin then got a silently EMPTY live dashboard with no error, while a
        # non-admin sending the identical value got a clean 400.
        #
        # Same role-dependent shape as the /metrics 500 fixed earlier; only the
        # symptom differs, because this query degrades to "no match" instead of
        # blowing up. An empty page with no explanation is the harder one to
        # diagnose, not the easier one.
        try:
            parsed_project_id = uuid.UUID(project_id)
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project_id — expected a UUID",
            )
        if accessible is not None and parsed_project_id not in accessible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        return await stream_service.list_active_sessions(
            db, project_id, suite_name=suites, days=days,
        )

    return await stream_service.list_active_sessions(
        db,
        project_id=None,
        allowed_project_ids=accessible,
        suite_name=suites,
        days=days,
    )


@router.get("/sse/{project_id}")
async def sse_stream(
    project_id: str,
    request: Request,
    token: str,
):
    # SSE routes can't go through the usual require_project_access dependency
    # because the token arrives as a query param (browsers don't send auth
    # headers on EventSource requests) and the handler streams forever. We
    # inline the equivalent membership check against a short-lived DB session
    # so the streaming generator doesn't hold a pool slot.
    import uuid as _uuid

    from sqlalchemy import select as _select

    from app.core.security import JWTError, decode_token
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import ProjectMember, User, UserRole

    try:
        claims = decode_token(token)
        user_id = claims.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except (JWTError, Exception):
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        project_uuid = _uuid.UUID(project_id)
        user_uuid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project or user ID")

    async with AsyncSessionLocal() as _db:
        user = (await _db.execute(_select(User).where(User.id == user_uuid))).scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid token")
        if user.role != UserRole.ADMIN and str(user.role) != UserRole.ADMIN.value:
            member = (await _db.execute(
                _select(ProjectMember.id).where(
                    ProjectMember.user_id == user_uuid,
                    ProjectMember.project_id == project_uuid,
                )
            )).scalar_one_or_none()
            if member is None:
                raise HTTPException(status_code=403, detail="You do not have access to this project")

    last_event_id = request.headers.get("last-event-id")
    if last_event_id and re.fullmatch(r"\d+-\d+", last_event_id) is None:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    from app.streams.live_fanout import (
        latest_live_sequence,
        project_delivery_lock,
        replay_live_notifications,
    )
    from app.streams.live_run_state import RedisLiveRunState

    bootstrap: list[dict] = []
    async with project_delivery_lock(project_id):
        high_water = await latest_live_sequence(project_id)
        active = await RedisLiveRunState.get_all_active()
        project_sessions = [session for session in active if session.get("project_id") == project_id]
        if last_event_id:
            replay, reconcile = await replay_live_notifications(
                project_id, last_event_id, through_id=high_water,
            )
            if reconcile:
                bootstrap.append({
                    "type": "reconcile_required",
                    "sessions": project_sessions,
                    "sequence_id": high_water,
                })
            else:
                bootstrap.extend(replay)
        else:
            bootstrap.append({
                "type": "initial_state",
                "sessions": project_sessions,
                "sequence_id": high_water,
            })
        _sse_subscribers.setdefault(project_id, set()).add(queue)
        _sse_bootstrap_floors[queue] = high_water

    async def generate() -> AsyncGenerator[str, None]:
        try:
            for event in bootstrap:
                sequence_id = event.get("sequence_id")
                yield f"id: {sequence_id}\ndata: {json.dumps(event)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    sequence_id = msg.get("sequence_id")
                    id_line = f"id: {sequence_id}\n" if sequence_id else ""
                    yield id_line + "data: " + json.dumps(msg) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _sse_subscribers.get(project_id, set()).discard(queue)
            _sse_bootstrap_floors.pop(queue, None)
            if project_id in _sse_subscribers and not _sse_subscribers[project_id]:
                del _sse_subscribers[project_id]

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def push_to_sse(project_id: str, message: dict) -> None:
    subscribers = _sse_subscribers.get(project_id, set())
    if not subscribers:
        return
    dead: set[asyncio.Queue] = set()
    for queue in list(subscribers):
        sequence_id = message.get("sequence_id")
        floor = _sse_bootstrap_floors.get(queue)
        if sequence_id and floor:
            from app.streams.live_fanout import sequence_at_or_before
            if sequence_at_or_before(str(sequence_id), floor):
                continue
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Keep the stream alive and make the gap explicit. Silently
            # unregistering a full queue left its client connected forever but
            # permanently starved.
            try:
                queue.get_nowait()
                queue.put_nowait({
                    "type": "reconcile_required",
                })
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                dead.add(queue)
    for queue in dead:
        subscribers.discard(queue)
