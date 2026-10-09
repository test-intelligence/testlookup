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
    elif allowed_project_ids is not None:
        # Match the Mongo half above. An empty membership set compiles to a
        # false predicate; it must never fall through to the fleet query.
        stmt = stmt.where(TestRun.project_id.in_(list(allowed_project_ids)))

    db_runs = (await db.execute(stmt)).scalars().all()
    stubs = []
    for run in db_runs:
        if str(run.id) in ai_run_ids:
            continue
        total = run.total_tests or 0
        failed = run.failed_tests or 0
        # Read the stored count; never derive it. `total - failed` counted
        # broken and skipped tests as passing — for a run of 4 passed / 4
        # failed / 1 broken / 1 skipped it produced "6", which contradicted
        # both the real passed count and the pass rate printed beside it.
        passed = run.passed_tests or 0
        skipped = run.skipped_tests or 0
        broken = run.broken_tests or 0
        # A broken test is a failure for this purpose; "no failures" while a
        # test errored out is not something to tell a user.
        failures = failed + broken
        # The rate's denominator is executed tests, so show the fraction over
        # the same denominator rather than over the total.
        executed = max(total - skipped, 0)
        tally = f"{passed}/{executed} executed" + (f", {skipped} skipped" if skipped else "")
        # A run still in progress has no pass rate yet, and one that measured
        # no tests has none at all. ``pass_rate or 0.0`` read both as 0%: "Build
        # viz-3044 completed — 1 test failed. Pass rate: 0.0% (10/11
        # executed)" for a run still running at 10 passed, 1 failed (the UX
        # redesign's browser E2E pass, 2026-10-08).
        status = getattr(run, "status", None)
        if str(getattr(status, "value", status) or "").upper() == "IN_PROGRESS":
            outcome = (
                f"is still running: {passed} passed and {failures} failed so far. "
                "No pass rate until it finishes"
            )
        else:
            status_word = (
                "completed with no failures" if failures == 0
                else f"completed — {failures} test{'s' if failures != 1 else ''} failed"
            )
            rate = (
                f"Pass rate: {run.pass_rate:.1f}% ({tally})" if run.pass_rate is not None
                else f"No pass rate: no test passed or failed ({tally})"
            )
            outcome = f"{status_word}. {rate}"
        stubs.append(
            {
                "test_run_id": str(run.id),
                "project_id": str(run.project_id),
                "build_number": run.build_number or "",
                "executive_summary": (
                    f"Build **{run.build_number or str(run.id)[:8]}** {outcome}. "
                    # Nothing records whether analysis was ever requested for
                    # a run: ingestion takes a ``run_ai`` flag and, when it is
                    # false, only logs ``agent_pipeline_skipped``. This used to
                    # read "AI analysis is being generated and will appear
                    # shortly" for every run without a summary -- a promise that
                    # never came true for any run ingested with run_ai=false, or
                    # any whose analysis had failed. State what is known.
                    + "No AI analysis has been generated for this run."
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


async def list_sessions(
    db: AsyncSession, current_user, project_id: Optional[uuid.UUID] = None,
) -> list[ChatSession]:
    from app.core.deps import get_accessible_project_ids

    accessible = await get_accessible_project_ids(db, current_user)
    statement = select(ChatSession).where(ChatSession.user_id == current_user.id)
    if accessible is not None:
        # Session ownership is insufficient after membership is revoked. A
        # non-admin may list only sessions in projects they can access now.
        # Projectless sessions are deliberately excluded by this predicate.
        statement = statement.where(ChatSession.project_id.in_(list(accessible)))
    if project_id is not None:
        # The chat page shows the active project's conversations. Without this
        # an admin's list mixed every project's sessions -- including ones
        # bound to purged projects -- under one project's name.
        statement = statement.where(ChatSession.project_id == project_id)
    result = await db.execute(
        statement.order_by(ChatSession.updated_at.desc()).limit(50)
    )
    return list(result.scalars().all())


async def validate_report_binding(db: AsyncSession, payload, current_user) -> None:
    """Validate a session's immutable DecisionReport anchor before insert."""
    fields = (payload.active_test_run_id, payload.active_report_id, payload.active_report_version)
    if not any(isinstance(value, (str, uuid.UUID, int)) for value in fields):
        return
    if not payload.project_id or not payload.active_test_run_id or not payload.active_report_id:
        raise ValueError("project_id, active_test_run_id, and active_report_id are required for report-grounded chat")

    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
    run = (await db.execute(
        select(TestRun).where(
            TestRun.id == payload.active_test_run_id,
            TestRun.project_id == payload.project_id,
        )
    )).scalar_one_or_none()
    if run is None:
        raise ValueError("Test run not found in the selected project")

    from app.db.mongo import Collections, get_mongo_db
    query = {
        "report_id": payload.active_report_id,
        "project_id": str(payload.project_id),
        "test_run_id": str(payload.active_test_run_id),
        "status": "published",
    }
    if payload.active_report_version is not None:
        query["report_version"] = payload.active_report_version
    report = await get_mongo_db()[Collections.DECISION_REPORTS].find_one(query, {"_id": 0, "report_version": 1})
    if report is None:
        raise ValueError("Published DecisionReport version not found")
    if payload.active_report_version is None:
        payload.active_report_version = int(report["report_version"])
async def create_session(db: AsyncSession, payload, current_user) -> ChatSession:
    """Stage a new session. Caller owns the transaction — this only flushes
    so the generated primary key is available to the handler.
    """
    session = ChatSession(
        user_id=current_user.id,
        project_id=payload.project_id,
        active_test_run_id=payload.active_test_run_id,
        active_report_id=payload.active_report_id,
        active_report_version=payload.active_report_version,
        title=payload.title or "New conversation",
    )
    db.add(session)
    await db.flush()
    return session


async def delete_session(db: AsyncSession, session: ChatSession) -> None:
    """Stage deletion of an already-authorized session. The handler commits."""
    await db.delete(session)


async def get_messages(db: AsyncSession, session_id: uuid.UUID, limit: int) -> list[ChatMessage]:
    """The LATEST ``limit`` questions and answers of a session the caller has
    already been authorized for, oldest first.

    This used to be ``order_by(created_at asc).limit(limit)`` -- the OLDEST
    fifty -- so a long conversation stopped showing its new turns. It also
    returned the history-compression ``summary`` row, which the page rendered
    as if the assistant had said it.
    """
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


def stage_title(session: ChatSession, message: Optional[str]) -> None:
    """Name a new conversation after its first question (the handler commits)."""
    if message and (not session.title or session.title == "New conversation"):
        session.title = message.strip()[:80]


# ── Streaming ────────────────────────────────────────────────────────────────

# Server-sent events: a comment line keeps idle proxies from closing the
# connection while a tool runs; the disconnect check stops a turn whose reader
# left (the Stop button, a closed tab) within half a second.
_SSE_HEARTBEAT_SECONDS = 10.0
_SSE_DISCONNECT_POLL_SECONDS = 0.5


def start_turn(session: ChatSession, payload, current_user):
    """Start a streamed turn on an already-authorized session."""
    from app.agents.conversation import ConversationAgent

    return ConversationAgent().start_turn(
        session_id=str(session.id),
        user_id=str(current_user.id),
        project_id=str(session.project_id) if session.project_id else None,
        user_message=payload.message,
        retry=bool(payload.retry),
        test_run_id=str(session.active_test_run_id) if session.active_test_run_id else None,
        report_id=session.active_report_id,
        report_version=session.active_report_version,
    )


def _sse(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def sse_events(turn, request):
    """Forward a turn's events as server-sent events until it ends or the
    reader goes away; leaving cancels the turn (it keeps any text shown)."""
    import time

    last_sent = last_check = time.monotonic()
    try:
        while True:
            try:
                item = await turn.next_event(timeout=_SSE_DISCONNECT_POLL_SECONDS)
            except StopAsyncIteration:
                return
            now = time.monotonic()
            if item is None or now - last_check >= _SSE_DISCONNECT_POLL_SECONDS:
                last_check = now
                if await request.is_disconnected():
                    return
            if item is None:
                if now - last_sent >= _SSE_HEARTBEAT_SECONDS:
                    last_sent = now
                    yield ": ping\n\n"
                continue
            event, data = item
            last_sent = now
            yield _sse(event, data)
    finally:
        turn.cancel_nowait()


async def send_message(
    db: AsyncSession,
    session: ChatSession,
    payload,
    current_user,
) -> dict:
    """Handle a user turn against an already-authorized session.

    Title updates are staged (no commit). The caller (handler) commits once.
    Raises ``ChatTurnError`` when no answer could be produced.
    """
    stage_title(session, payload.message)

    from app.agents.conversation import ConversationAgent

    agent = ConversationAgent()
    result = await agent.chat(
        session_id=str(session.id),
        user_message=payload.message,
        user_id=str(current_user.id),
        project_id=str(session.project_id) if session.project_id else payload.project_id,
        test_run_id=str(session.active_test_run_id) if session.active_test_run_id else None,
        report_id=session.active_report_id,
        report_version=session.active_report_version,
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
