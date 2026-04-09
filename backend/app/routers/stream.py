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

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.schemas import ActiveSessionsResponse, LiveEventBatch, LiveSessionCreate
from app.services import stream_service

router = APIRouter(prefix="/api/v1/stream", tags=["Live Stream"])

_sse_subscribers: dict[str, set[asyncio.Queue]] = {}


@router.post("/sessions", response_model=stream_service.LiveSessionResponse, status_code=201)
async def create_session(
    payload: LiveSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return await stream_service.create_session(db, payload)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    return await stream_service.get_session(db, session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    await stream_service.close_session(db, session_id)


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
    from app.core.security import decode_token
    from jose import JWTError

    try:
        decode_token(token)
    except (JWTError, Exception):
        raise HTTPException(status_code=401, detail="Invalid token")

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
