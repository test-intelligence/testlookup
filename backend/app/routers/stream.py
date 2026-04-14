"""
Live Test Execution Streaming API.

The router keeps only transport concerns such as SSE subscription state.
Session lifecycle, event ingestion, and dashboard aggregation live in
`app.services.stream_service`.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_api_key_context,
    get_current_active_user,
    require_live_session_access,
)
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import ActiveSessionsResponse, LiveEventBatch, LiveSessionCreate
from app.services import stream_service

router = APIRouter(prefix="/api/v1/stream", tags=["Live Stream"])

_sse_subscribers: dict[str, set[asyncio.Queue]] = {}


@router.post("/sessions", response_model=stream_service.LiveSessionResponse, status_code=201)
async def create_session(
    payload: LiveSessionCreate,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
):
    current_user, bound_project_id = auth

    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user account")

    # Project-scoped API key: enforce that the session targets the bound project
    if bound_project_id is not None and payload.project_id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

    response = await stream_service.create_session(db, payload)
    await db.commit()
    return response


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
    _: User = Depends(require_live_session_access()),
):
    return await stream_service.get_session(db, session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, None] = Depends(get_api_key_context),
    _: User = Depends(require_live_session_access()),
):
    await stream_service.close_session(db, session_id)
    await db.commit()


@router.post("/events/batch", response_model=stream_service.LiveEventBatchResponse, status_code=202)
async def ingest_event_batch(
    batch: LiveEventBatch,
    x_session_token: str = Header(..., alias="X-Session-Token"),
):
    return await stream_service.ingest_event_batch(batch, x_session_token)


@router.get("/active", response_model=ActiveSessionsResponse)
async def list_active_sessions(
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return ActiveSessionsResponse(sessions=[], total=0)
    return await stream_service.list_active_sessions(db, project_id)


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
    from jose import JWTError

    from app.core.security import decode_token
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

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _sse_subscribers.setdefault(project_id, set()).add(queue)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            from app.streams.live_run_state import RedisLiveRunState

            active = await RedisLiveRunState.get_all_active()
            project_sessions = [session for session in active if session.get("project_id") == project_id]
            yield "data: " + json.dumps({"type": "initial_state", "sessions": project_sessions}) + "\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield "data: " + json.dumps(msg) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _sse_subscribers.get(project_id, set()).discard(queue)
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
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead.add(queue)
    for queue in dead:
        subscribers.discard(queue)
