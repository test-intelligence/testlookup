"""
Chat / Conversation Agent endpoints.

Provides CRUD for chat sessions and a message endpoint that invokes
the ConversationAgent to answer questions about test results.

Transaction model (pilot of the target "one commit per request" pattern):
  * Services stage changes with ``db.add`` / ``db.delete`` / mutation and
    at most call ``db.flush()`` when they need a generated ID.
  * The router handler owns ``db.commit()`` — one commit per request path.
  * Ownership checks live in the ``require_session_access`` dependency so
    the handler receives an already-authorized :class:`ChatSession`.
"""
import uuid
from typing import Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _enforce_api_key_project_binding,
    get_current_active_user,
    require_session_access,
)
from app.db.postgres import get_db
from app.models.postgres import ChatSession, User, UserRole
from app.models.schemas import (
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamMessageRequest,
)
from app.services import chat_service

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


def _require_chat_project(user: User, project_id: object | None) -> None:
    """Keep non-admin chat inside one budgeted, tenant-scoped project."""
    _enforce_api_key_project_binding(user, cast(uuid.UUID | None, project_id))
    if project_id is None and user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=422,
            detail="Select a project before starting or continuing a chat",
        )


# ── Pre-computed run summaries ─────────────────────────────────────────────

@router.get("/run-summaries")
async def get_run_summaries(
    project_id: Optional[str] = None,
    days: int = Query(5, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # F-040: the old guard ran only in the ``if not project_id`` branch, so
    # naming a project skipped it entirely and the service applied no scoping
    # of its own. Same shape as the digests leak (F-033) — the guard fired
    # only where there was nothing to guard.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(db, current_user, project_id)
    return await chat_service.get_run_summaries(
        db,
        str(scoped_project_id) if scoped_project_id else None,
        days,
        allowed_project_ids=allowed,
    )


# ── Sessions ──────────────────────────────────────────────────────

@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    project_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The caller's own conversations, newest first; ``project_id`` narrows
    them to one project (the chat page passes the active project)."""
    return await chat_service.list_sessions(db, current_user, project_id=project_id)


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # F-041: payload.project_id went straight onto the row unchecked. The row
    # is not inert — send_message hands its project to the ConversationAgent,
    # which is what fetches the data to answer with, so an unverified binding
    # is a standing handle on another tenant's project.
    _require_chat_project(current_user, payload.project_id)
    if payload.project_id:
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(payload.project_id))
    try:
        await chat_service.validate_report_binding(db, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = await chat_service.create_session(db, payload, current_user)
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session: ChatSession = Depends(require_session_access()),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.delete_session(db, session)
    await db.commit()


# ── Messages ──────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
async def get_messages(
    limit: int = Query(50, ge=1, le=200),
    session: ChatSession = Depends(require_session_access()),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.get_messages(db, session.id, limit)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(
    payload: SendMessageRequest,
    session: ChatSession = Depends(require_session_access()),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # F-041: send_message resolves ``session.project_id or payload.project_id``,
    # so a per-message project re-points an otherwise legitimate session at
    # another tenant. Session *ownership* is guarded by require_session_access
    # (creator-only); the project named inside it was not.
    if payload.project_id and session.project_id and str(session.project_id) != str(payload.project_id):
        raise HTTPException(status_code=422, detail="message project_id must match the session project")
    _require_chat_project(current_user, session.project_id or payload.project_id)
    if payload.project_id and not session.project_id:
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(payload.project_id))

    from app.agents.conversation import ChatTurnError  # noqa: PLC0415

    try:
        result = await chat_service.send_message(db, session, payload, current_user)
    except ChatTurnError as exc:
        # The question was saved; no answer was. Say why, in the same
        # vocabulary the streaming endpoint's ``error`` event uses.
        await db.commit()
        raise HTTPException(status_code=_turn_error_status(exc.code), detail=exc.as_event()) from exc
    # Persist the title mutation staged by send_message. The ConversationAgent
    # manages its own writes internally; we only commit our unit of work.
    await db.commit()
    return SendMessageResponse(**result)


def _turn_error_status(code: str) -> int:
    return {
        "project_required": 422,
        "empty_question": 422,
        "nothing_to_retry": 409,
        "llm_disabled": 409,
        "budget_exceeded": 429,
        "rate_limited": 429,
        "busy": 429,
        "timeout": 504,
    }.get(code, 503)


# activity: none -- asks this user's own chat a question; the question and answer are stored in their private session and nothing in the project changes.
@router.post("/sessions/{session_id}/messages/stream")
async def stream_message(
    payload: StreamMessageRequest,
    request: Request,
    session: ChatSession = Depends(require_session_access()),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Ask a question and receive the answer as server-sent events.

    Events, in order: ``start`` (the question was saved), ``status`` (what the
    assistant is looking up), ``delta`` (answer text as it is written), then
    ``done`` (the saved answer, its sources, tool trace and timings) or
    ``error`` (``code``, ``message``, ``retryable``; no answer was saved).
    ``retry: true`` answers the last unanswered or stopped question again.
    Closing the connection stops the answer; text already sent is kept.
    """
    # The session's project is the only scope: there is no per-message
    # project to re-point it with (F-041).
    _require_chat_project(current_user, session.project_id)
    chat_service.stage_title(session, payload.message)
    await db.commit()
    turn = chat_service.start_turn(session, payload, current_user)
    return StreamingResponse(
        chat_service.sse_events(turn, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
