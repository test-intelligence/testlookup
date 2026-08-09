from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ChatMessage, ChatSession, TestRun


async def get_run_summaries(
    db: AsyncSession,
    project_id: Optional[str],
    days: int,
    *,
    allowed_project_ids: Optional[set] = None,
) -> list[dict]:
    """Recent AI run summaries, scoped to what the caller may see.

    ``project_id`` and ``allowed_project_ids`` are the two mutually exclusive
    halves of ``resolve_project_scope``'s answer: a pinned project the caller
    has been verified against, or the set of projects they belong to. Both
    ``None`` means ADMIN with no project named — unrestricted.

    An **empty** ``allowed_project_ids`` is meaningful, not missing: it is a
    user who belongs to nothing, and must match no summaries rather than all
    of them. Hence the explicit ``is not None`` test.
    """
    from app.db.mongo import Collections, get_mongo_db

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    mongo_db = get_mongo_db()
    mongo_query: dict = {"generated_at": {"$gte": cutoff}}
    if project_id:
        mongo_query["project_id"] = project_id
    elif allowed_project_ids is not None:
        mongo_query["project_id"] = {"$in": [str(p) for p in allowed_project_ids]}

    cursor = mongo_db[Collections.RUN_SUMMARIES].find(
        mongo_query,
        {
            "_id": 0,
            "test_run_id": 1,
            "project_id": 1,
            "build_number": 1,
            "executive_summary": 1,
            "markdown_report": 1,
            "anomaly_count": 1,
            "is_regression": 1,
            "analysis_count": 1,
            "generated_at": 1,
        },
    ).sort("generated_at", -1).limit(20)

    ai_summaries = await cursor.to_list(length=20)
    ai_run_ids = {summary["test_run_id"] for summary in ai_summaries}

    for summary in ai_summaries:
        if isinstance(summary.get("generated_at"), datetime):
            summary["generated_at"] = summary["generated_at"].isoformat()
        summary["is_stub"] = False

    stmt = select(TestRun).where(TestRun.start_time >= cutoff).order_by(TestRun.start_time.desc()).limit(20)
    if project_id:
        try:
            stmt = stmt.where(TestRun.project_id == uuid.UUID(project_id))
        except ValueError:
            pass

    db_runs = (await db.execute(stmt)).scalars().all()
    stubs = []
    for run in db_runs:
        if str(run.id) in ai_run_ids:
            continue
        pass_rate = run.pass_rate or 0.0
        total = run.total_tests or 0
        failed = run.failed_tests or 0
        passed = total - failed
        status_word = "completed with no failures" if failed == 0 else f"completed — {failed} test{'s' if failed != 1 else ''} failed"
        stubs.append(
            {
                "test_run_id": str(run.id),
                "project_id": str(run.project_id),
                "build_number": run.build_number or "",
                "executive_summary": (
                    f"Build **{run.build_number or str(run.id)[:8]}** {status_word}. "
                    f"Pass rate: {pass_rate:.1f}% ({passed}/{total} tests). "
                    f"AI analysis is being generated and will appear shortly."
                ),
                "markdown_report": None,
                "anomaly_count": 0,
                "is_regression": False,
                "analysis_count": 0,
                "generated_at": run.start_time.isoformat() if run.start_time else datetime.now(timezone.utc).isoformat(),
                "is_stub": True,
            }
        )

    combined = ai_summaries + stubs
    combined.sort(key=lambda summary: summary.get("generated_at", ""), reverse=True)
    return combined[:20]


async def list_sessions(db: AsyncSession, current_user) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession).where(ChatSession.user_id == current_user.id).order_by(ChatSession.updated_at.desc()).limit(50)
    )
    return list(result.scalars().all())


async def create_session(db: AsyncSession, payload, current_user) -> ChatSession:
    """Stage a new session. Caller owns the transaction — this only flushes
    so the generated primary key is available to the handler.
    """
    session = ChatSession(
        user_id=current_user.id,
        project_id=payload.project_id,
        title=payload.title or "New conversation",
    )
    db.add(session)
    await db.flush()
    return session


async def delete_session(db: AsyncSession, session: ChatSession) -> None:
    """Stage deletion of an already-authorized session. The handler commits."""
    await db.delete(session)


async def get_messages(db: AsyncSession, session_id: uuid.UUID, limit: int) -> list[ChatMessage]:
    """List messages for a session the caller has already been authorized for
    (the ``require_session_access`` guard runs before this service is called)."""
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc()).limit(limit)
    )
    return list(result.scalars().all())


async def send_message(
    db: AsyncSession,
    session: ChatSession,
    payload,
    current_user,
) -> dict:
    """Handle a user turn against an already-authorized session.

    Title updates are staged (no commit). The caller (handler) commits once.
    """
    if not session.title or session.title == "New conversation":
        session.title = payload.message[:80]

    from app.agents.conversation import ConversationAgent

    agent = ConversationAgent()
    result = await agent.chat(
        session_id=str(session.id),
        user_message=payload.message,
        user_id=str(current_user.id),
        project_id=str(session.project_id) if session.project_id else payload.project_id,
    )
    return {
        "session_id": session.id,
        "reply": result["reply"],
        "sources": result["sources"],
        # AI-6: tool-use transparency + human action handoffs (empty on the
        # single-shot path — older agents may not return the keys at all).
        "tool_trace": result.get("tool_trace", []),
        "suggested_actions": result.get("suggested_actions", []),
    }
