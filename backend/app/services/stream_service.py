from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import LaunchStatus, LiveSession, Project, TestRun
from app.models.schemas import ActiveSessionsResponse, LiveEventBatchResponse, LiveSessionResponse, LiveSessionState

logger = logging.getLogger(__name__)

SESSION_TTL = 86_400
SESSION_TOKEN_KEY = "live:session:{token}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_redis():
    from app.db.redis_client import get_redis as _get_redis

    return _get_redis()


async def publish_event_batch(session_id: str, run_id: str, events):
    from app.streams.producer import publish_event_batch as _publish_event_batch

    return await _publish_event_batch(session_id=session_id, run_id=run_id, events=events)


async def create_session(db: AsyncSession, payload) -> LiveSessionResponse:
    """Stage a new LiveSession and return the response shape. Handler commits.

    Redis session-token registration and in-memory run state happen *after*
    the handler's commit so an aborted transaction never leaves a dangling
    session token that authenticates a run which doesn't exist in Postgres.
    """
    project = await db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    session_id = str(uuid.uuid4())
    run_id = session_id
    session_token = secrets.token_urlsafe(32)

    session = LiveSession(
        id=uuid.UUID(session_id),
        project_id=payload.project_id,
        run_id=run_id,
        client_name=payload.client_name,
        machine_id=payload.machine_id,
        build_number=payload.build_number,
        framework=payload.framework,
        branch=payload.branch,
        commit_hash=payload.commit_hash,
        session_token_hash=hash_token(session_token),
        total_tests=payload.total_tests or 0,
        status="active",
        release_name=payload.release_name or None,
        started_at=datetime.now(timezone.utc),
        extra_metadata=payload.metadata or {},
    )
    db.add(session)
    await db.flush()

    redis = get_redis()
    await redis.setex(SESSION_TOKEN_KEY.format(token=session_token), SESSION_TTL, session_id)

    from app.streams.live_run_state import RedisLiveRunState

    await RedisLiveRunState.start(
        run_id=run_id,
        project_id=str(payload.project_id),
        build_number=payload.build_number or session_id,
        total_tests=payload.total_tests or 0,
    )

    logger.info(
        "Live session created: session=%s run=%s project=%s framework=%s",
        session_id,
        run_id,
        payload.project_id,
        payload.framework,
    )
    return LiveSessionResponse(
        session_id=session_id,
        session_token=session_token,
        run_id=run_id,
        project_id=str(payload.project_id),
        expires_in=SESSION_TTL,
        created_at=session.started_at,
    )


async def get_session(db: AsyncSession, session_id: str) -> dict:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.streams.live_run_state import RedisLiveRunState

    live_stats = await RedisLiveRunState.get(session.run_id) or {}
    return {
        "session_id": str(session.id),
        "run_id": session.run_id,
        "project_id": str(session.project_id),
        "client_name": session.client_name,
        "machine_id": session.machine_id,
        "build_number": session.build_number,
        "framework": session.framework,
        "branch": session.branch,
        "status": session.status,
        "total_tests": session.total_tests,
        "events_received": session.events_received,
        "started_at": session.started_at.isoformat(),
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "live_stats": live_stats,
    }


async def close_session(db: AsyncSession, session_id: str) -> None:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Idempotency guard: a previous close_session for this session has already
    # cleared Redis state and queued the persistence task. Returning early here
    # closes the race where two concurrent close requests would each clear state
    # and enqueue persist_live_session, producing duplicate log lines and —
    # depending on Celery worker timing — duplicate DB upserts.
    if session.status == "completed":
        logger.info(f"close_session: already completed, skipping session_id={session_id}")
        return

    from app.streams.live_run_state import RedisLiveRunState

    state = await RedisLiveRunState.complete(session.run_id)
    now = datetime.now(timezone.utc)
    session.status = "completed"
    session.completed_at = now
    if state:
        session.extra_metadata = {**(session.extra_metadata or {}), "final_state": state}

    await upsert_test_run(db, session, state or {})

    if session.release_name and session.release_name.strip():
        try:
            from app.services.release_linker import auto_link_release

            await auto_link_release(
                db=db,
                project_id=session.project_id,
                release_name=session.release_name.strip(),
                test_run_id=uuid.UUID(session.run_id),
            )
        except Exception as rel_err:
            logger.warning(
                f"live_session_release_link_failed session_id={session_id}: {rel_err}"
            )

    # stage-only: handler commits the LiveSession close, the upserted
    # TestRun, and any release link together.

    try:
        from app.worker.tasks import persist_live_session

        persist_live_session.apply_async(
            kwargs={
                "run_id": session.run_id,
                "project_id": str(session.project_id),
                "build_number": session.build_number or session_id,
                "client_name": session.client_name,
                "framework": session.framework or "",
                "branch": session.branch or "",
                "commit_hash": session.commit_hash or "",
                "final_state": state or {},
            },
            queue="ingestion",
            priority=7,
        )
        logger.info(f"queued_persist_live_session session_id={session_id}")
    except Exception as exc:
        logger.warning(
            f"persist_live_session_queue_failed session_id={session_id}: {exc}"
        )

    try:
        from app.worker.tasks import run_agent_pipeline

        run_agent_pipeline.apply_async(
            kwargs={
                "test_run_id": session.run_id,
                "project_id": str(session.project_id),
                "build_number": session.build_number or session_id,
                "workflow_type": "offline",
            },
            queue="ai_analysis",
            priority=6,
            countdown=45,
        )
        logger.info(
            f"ai_pipeline_queued_from_live session_id={session_id} run_id={session.run_id}"
        )
    except Exception as exc:
        logger.warning(
            f"ai_pipeline_queue_failed session_id={session_id}: {exc}"
        )


async def ingest_event_batch(batch, x_session_token: str) -> LiveEventBatchResponse:
    import json as _json
    from app.streams import LIVE_TESTCASES_KEY, LIVE_STATE_KEY

    redis = get_redis()
    stored_session_id = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    if not stored_session_id or stored_session_id != batch.session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        )

    accepted = await publish_event_batch(session_id=batch.session_id, run_id=batch.run_id, events=batch.events)

    # Buffer test_result events directly into the Redis List that persist_live_session
    # reads.  This must happen here — synchronously in the HTTP handler — not in the
    # async stream consumer, because close_session() / persist_live_session can be
    # dispatched before the consumer processes the stream (race condition).
    #
    # Counter increments (HINCRBY) also happen here so the live state is
    # immediately accurate.  The async consumer only broadcasts + queues analysis.
    list_key = LIVE_TESTCASES_KEY.format(run_id=batch.run_id)
    state_key = LIVE_STATE_KEY.format(run_id=batch.run_id)
    counter_map = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "BROKEN": "broken"}
    now = datetime.now(timezone.utc).isoformat()
    last_test_name = ""

    pipe = redis.pipeline()
    for event in batch.events:
        event_dict: dict = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        if event_dict.get("event_type") == "test_result":
            entry = _json.dumps({
                "test_name":     event_dict.get("test_name", ""),
                "status":        event_dict.get("status", "UNKNOWN"),
                "duration_ms":   event_dict.get("duration_ms", 0),
                "suite_name":    event_dict.get("suite_name"),
                "class_name":    event_dict.get("class_name"),
                "error_message": event_dict.get("error_message"),
                "stack_trace":   event_dict.get("stack_trace"),
                "tags":          event_dict.get("tags"),
                "timestamp_ms":  event_dict.get("timestamp_ms"),
            })
            pipe.rpush(list_key, entry)

            # Increment the appropriate counter in the live run state hash
            status_upper = (event_dict.get("status") or "UNKNOWN").upper()
            counter_field = counter_map.get(status_upper)
            if counter_field:
                pipe.hincrby(state_key, counter_field, 1)
            last_test_name = event_dict.get("test_name") or last_test_name

    pipe.expire(list_key, 90_000)  # 25 h TTL — same as consumer's buffer
    # Update metadata on the live state hash
    if last_test_name:
        pipe.hset(state_key, mapping={"last_event_at": now, "current_test": last_test_name})
    else:
        pipe.hset(state_key, "last_event_at", now)
    pipe.expire(state_key, 86_400)
    await pipe.execute()

    await redis.expire(SESSION_TOKEN_KEY.format(token=x_session_token), SESSION_TTL)
    return LiveEventBatchResponse(accepted=accepted, run_id=batch.run_id, session_id=batch.session_id)


def build_live_session_state(payload: dict) -> LiveSessionState:
    return LiveSessionState(
        run_id=payload.get("run_id", ""),
        project_id=payload.get("project_id", ""),
        build_number=payload.get("build_number", ""),
        status=payload.get("status", "running"),
        total=int(payload.get("total", 0)),
        passed=int(payload.get("passed", 0)),
        failed=int(payload.get("failed", 0)),
        skipped=int(payload.get("skipped", 0)),
        broken=int(payload.get("broken", 0)),
        pass_rate=float(payload.get("pass_rate", 0.0)),
        current_test=payload.get("current_test") or None,
        started_at=payload.get("started_at"),
        last_event_at=payload.get("last_event_at"),
        client_name=payload.get("client_name"),
        completed_at=payload.get("completed_at"),
        release_name=payload.get("release_name"),
    )


def build_completed_session_state(session) -> LiveSessionState:
    final_state = (session.extra_metadata or {}).get("final_state", {})
    return LiveSessionState(
        run_id=session.run_id,
        project_id=str(session.project_id),
        build_number=session.build_number or "",
        status="completed",
        total=int(final_state.get("total", session.total_tests or 0)),
        passed=int(final_state.get("passed", 0)),
        failed=int(final_state.get("failed", 0)),
        skipped=int(final_state.get("skipped", 0)),
        broken=int(final_state.get("broken", 0)),
        pass_rate=float(final_state.get("pass_rate", 0.0)),
        current_test=None,
        started_at=session.started_at.isoformat() if session.started_at else None,
        last_event_at=session.completed_at.isoformat() if session.completed_at else None,
        client_name=session.client_name,
        completed_at=session.completed_at.isoformat() if session.completed_at else None,
        release_name=session.release_name or None,
    )


def build_test_run_fallback_state(run) -> LiveSessionState:
    return LiveSessionState(
        run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number or "",
        status="completed",
        total=run.total_tests or 0,
        passed=run.passed_tests or 0,
        failed=run.failed_tests or 0,
        skipped=run.skipped_tests or 0,
        broken=run.broken_tests or 0,
        pass_rate=float(run.pass_rate or 0.0),
        current_test=None,
        started_at=run.start_time.isoformat() if run.start_time else None,
        last_event_at=run.end_time.isoformat() if run.end_time else None,
        client_name=None,
        completed_at=run.end_time.isoformat() if run.end_time else None,
    )


async def list_active_sessions(
    db: AsyncSession,
    project_id: Optional[str] = None,
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
) -> ActiveSessionsResponse:
    """
    List active + recent live sessions, enforcing tenant isolation.

    - ``project_id`` (when set) narrows the result to that single project.
    - ``allowed_project_ids`` (when set) constrains the result to the caller's
      accessible project set — used for non-admin callers without a pinned
      project. A value of ``None`` means no constraint (ADMIN or a project_id
      that has already been verified).
    """
    from app.streams.live_run_state import RedisLiveRunState

    all_active = await RedisLiveRunState.get_all_active()
    if project_id:
        all_active = [session for session in all_active if session.get("project_id") == project_id]
    elif allowed_project_ids is not None:
        allowed_str = {str(pid) for pid in allowed_project_ids}
        all_active = [s for s in all_active if s.get("project_id") in allowed_str]

    active_run_ids = {session.get("run_id") for session in all_active}
    active_sessions = [build_live_session_state(session) for session in all_active]

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stmt = (
        select(LiveSession)
        .where(LiveSession.status == "completed", LiveSession.completed_at >= cutoff)
        .order_by(LiveSession.completed_at.desc())
        .limit(50)
    )
    if project_id:
        try:
            stmt = stmt.where(LiveSession.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        if not allowed_project_ids:
            return ActiveSessionsResponse(sessions=[], count=0)
        stmt = stmt.where(LiveSession.project_id.in_(allowed_project_ids))

    db_sessions = (await db.execute(stmt)).scalars().all()
    seen_run_ids = set(active_run_ids)
    completed_sessions = []
    for session in db_sessions:
        if session.run_id in seen_run_ids:
            continue
        seen_run_ids.add(session.run_id)
        completed_sessions.append(build_completed_session_state(session))

    tr_stmt = (
        select(TestRun)
        .where(TestRun.trigger_source == "live_stream", TestRun.start_time >= cutoff)
        .order_by(TestRun.start_time.desc())
        .limit(50)
    )
    if project_id:
        try:
            tr_stmt = tr_stmt.where(TestRun.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        tr_stmt = tr_stmt.where(TestRun.project_id.in_(allowed_project_ids))

    tr_runs = (await db.execute(tr_stmt)).scalars().all()
    for run in tr_runs:
        if str(run.id) in seen_run_ids:
            continue
        seen_run_ids.add(str(run.id))
        completed_sessions.append(build_test_run_fallback_state(run))

    sessions = active_sessions + completed_sessions
    return ActiveSessionsResponse(sessions=sessions, count=len(sessions))


async def upsert_test_run(db: AsyncSession, session: LiveSession, state: dict) -> None:
    try:
        run_uuid = uuid.UUID(session.run_id)
    except ValueError:
        run_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, session.run_id)

    passed = int(state.get("passed", 0))
    failed = int(state.get("failed", 0))
    skipped = int(state.get("skipped", 0))
    broken = int(state.get("broken", 0))
    total = int(state.get("total", session.total_tests or 0))
    # Fallback: if total wasn't tracked, derive it from component counts
    total = total or (passed + failed + skipped + broken)
    pass_rate = round(passed / (passed + failed + broken) * 100, 2) if (passed + failed + broken) > 0 else None
    run_status = LaunchStatus.FAILED if (failed + broken) > 0 else LaunchStatus.PASSED
    now = datetime.now(timezone.utc)

    run = (await db.execute(select(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if run is None:
        run = TestRun(
            id=run_uuid,
            project_id=session.project_id,
            build_number=session.build_number or str(session.id)[:8],
            trigger_source="live_stream",
            branch=session.branch or None,
            commit_hash=session.commit_hash or None,
            status=run_status,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            broken_tests=broken,
            pass_rate=pass_rate,
            start_time=session.started_at or now,
            end_time=now,
        )
        db.add(run)
        logger.info(
            f"test_run_created_for_live_session run_id={run_uuid} session_id={session.id}"
        )
    else:
        run.status = run_status
        run.total_tests = total
        run.passed_tests = passed
        run.failed_tests = failed
        run.skipped_tests = skipped
        run.broken_tests = broken
        run.pass_rate = pass_rate
        run.end_time = now
        logger.info(
            f"test_run_updated_for_live_session run_id={run_uuid} session_id={session.id}"
        )
