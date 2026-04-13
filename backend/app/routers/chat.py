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
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_session_access,
)
from app.db.postgres import get_db
from app.models.postgres import ChatSession, User
from app.models.schemas import (
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services import chat_service

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


# ── Pre-computed run summaries ─────────────────────────────────────────────

@router.get("/run-summaries")
async def get_run_summaries(
    project_id: Optional[str] = None,
    days: int = Query(5, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return []
    return await chat_service.get_run_summaries(db, project_id, days)


# ── Sessions ──────────────────────────────────────────────────────

@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await chat_service.list_sessions(db, current_user)


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
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
    result = await chat_service.send_message(db, session, payload, current_user)
    # Persist the title mutation staged by send_message. The ConversationAgent
    # manages its own writes internally; we only commit our unit of work.
    await db.commit()
    return SendMessageResponse(**result)
