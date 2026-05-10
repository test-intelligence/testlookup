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
from app.models.schemas import (
    ActiveSessionsResponse,
    LiveEventBatchResponse,
    LiveSessionResponse,
    LiveSessionState,
    LiveStreamIngestRequest,
    LiveStreamIngestResponse,
)
from app.services.async_utils import await_if_needed

logger = logging.getLogger(__name__)

SESSION_TTL = 86_400
SESSION_TOKEN_KEY = "live:session:{token}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def canonical_test_run_uuid(run_id: str) -> uuid.UUID:
    """Map a live-session ``run_id`` (which may be a UUID *or* an arbitrary
    user-supplied slug like ``local-abc12345``) to the canonical ``TestRun.id``
    UUID we persist under.

    SDKs frequently default to slug-style ids (the Python SDK's
    ``f"local-{uuid.uuid4().hex[:8]}"`` is the canonical example). Without
    this helper, callers had three choices — and the three call sites that
    needed the mapping (``upsert_test_run``, ``persist_live_session``, and
    the LiveSessionState builders) drifted: the first two derived a UUID5
    via ``uuid.uuid5(NAMESPACE_DNS, run_id)`` while the live state response
    returned the raw slug, so the frontend's ``/runs/<run_id>`` link 422'd
    for any non-UUID slug. Centralising here keeps them in lockstep.
    """
    try:
        return uuid.UUID(run_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, run_id)


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
        launch_name=getattr(payload, "launch_name", None) or None,
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
        launch_name=getattr(payload, "launch_name", None) or None,
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


async def _persist_event_batch(session_id: str, run_id: str, events) -> int:
    """Publish events to Redis Streams and update the live-state hash.

    Shared by the session-token path (``ingest_event_batch``) and the
    API-key path (``ingest_via_api_key``). Buffering test_result events into
    the Redis list and HINCRBY-ing the counter hash must happen here —
    synchronously in the HTTP handler — not in the async stream consumer,
    because close_session() / persist_live_session can be dispatched before
    the consumer processes the stream (race condition).
    """
    import json as _json
    from app.streams import LIVE_TESTCASES_KEY, LIVE_STATE_KEY

    accepted = await publish_event_batch(session_id=session_id, run_id=run_id, events=events)

    redis = get_redis()
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
    state_key = LIVE_STATE_KEY.format(run_id=run_id)
    counter_map = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "BROKEN": "broken"}
    now = datetime.now(timezone.utc).isoformat()
    last_test_name = ""

    pipe = redis.pipeline()
    for event in events:
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
            await await_if_needed(pipe.rpush(list_key, entry))
            status_upper = (event_dict.get("status") or "UNKNOWN").upper()
            counter_field = counter_map.get(status_upper)
            if counter_field:
                await await_if_needed(pipe.hincrby(state_key, counter_field, 1))
            last_test_name = event_dict.get("test_name") or last_test_name

    await await_if_needed(pipe.expire(list_key, 90_000))  # 25 h TTL — same as consumer's buffer
    if last_test_name:
        await await_if_needed(pipe.hset(state_key, mapping={"last_event_at": now, "current_test": last_test_name}))
    else:
        await await_if_needed(pipe.hset(state_key, "last_event_at", now))
    await await_if_needed(pipe.expire(state_key, 86_400))
    await pipe.execute()

    return accepted


async def ingest_event_batch(batch, x_session_token: str) -> LiveEventBatchResponse:
    redis = get_redis()
    stored_session_id = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    if not stored_session_id or stored_session_id != batch.session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        )

    accepted = await _persist_event_batch(
        session_id=batch.session_id, run_id=batch.run_id, events=batch.events
    )

    await redis.expire(SESSION_TOKEN_KEY.format(token=x_session_token), SESSION_TTL)
    return LiveEventBatchResponse(accepted=accepted, run_id=batch.run_id, session_id=batch.session_id)


async def ingest_via_api_key(
    db: AsyncSession,
    project_id: uuid.UUID,
    api_key_name: str,
    request: LiveStreamIngestRequest,
) -> LiveStreamIngestResponse:
    """Ingest a batch of live events authenticated by an API key.

    On the first call for a given (project_id, run_id) pair, auto-creates a
    LiveSession populated from ``request.meta`` (with sensible fallbacks).
    Subsequent calls reuse the existing session. The release record is
    auto-created when ``meta.release_name`` is set so live runs participate
    in release tracking the same way the legacy /sessions flow does.

    The handler is responsible for committing the DB transaction after this
    call so the session row, Redis token, and run-state hash all come into
    being atomically.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Look up the most recent session for (project_id, run_id), regardless of
    # status. We need to distinguish three cases:
    #   1. No prior session       → create one (the happy path)
    #   2. Active session exists  → reuse it (subsequent batches in a run)
    #   3. Completed session      → reject 409 (the run already finalised; the
    #      client must pick a new run_id, otherwise we'd silently start a new
    #      run under the same display id and the UI would conflate the two).
    existing = (
        await db.execute(
            select(LiveSession)
            .where(
                LiveSession.project_id == project_id,
                LiveSession.run_id == request.run_id,
            )
            .order_by(LiveSession.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None and existing.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"run_id {request.run_id!r} has already finalised "
                f"(status={existing.status!r}). Pick a new run_id — including "
                "the CI build number or commit SHA in the run_id keeps it "
                "unique per run."
            ),
        )

    meta = request.meta
    created_session = False
    if existing is None:
        session_uuid = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)
        session = LiveSession(
            id=session_uuid,
            project_id=project_id,
            run_id=request.run_id,
            client_name=api_key_name,
            machine_id=(meta.machine_id if meta else None),
            build_number=(meta.build_number if meta else None) or request.run_id,
            framework=(meta.framework if meta else None),
            branch=(meta.branch if meta else None),
            commit_hash=(meta.commit_hash if meta else None),
            session_token_hash=hash_token(session_token),
            total_tests=(meta.total_tests if meta else None) or 0,
            status="active",
            release_name=(meta.release_name.strip() if meta and meta.release_name else None),
            launch_name=(meta.launch_name.strip() if meta and meta.launch_name else None),
            started_at=datetime.now(timezone.utc),
            extra_metadata=(meta.metadata if meta else None) or {},
        )
        db.add(session)
        await db.flush()

        redis = get_redis()
        await redis.setex(SESSION_TOKEN_KEY.format(token=session_token), SESSION_TTL, str(session_uuid))

        from app.streams.live_run_state import RedisLiveRunState

        await RedisLiveRunState.start(
            run_id=request.run_id,
            project_id=str(project_id),
            build_number=(meta.build_number if meta else None) or request.run_id,
            total_tests=(meta.total_tests if meta else None) or 0,
            launch_name=(meta.launch_name.strip() if meta and meta.launch_name else None),
        )

        # Auto-create the release record so it shows up in release tracking
        # immediately. The actual run→release link is wired up at session close.
        if session.release_name:
            try:
                from app.services.release_linker import resolve_or_create_release

                await resolve_or_create_release(db, project_id, session.release_name)
            except Exception as exc:  # pragma: no cover - non-fatal, log only
                logger.warning(
                    "live_session_release_autocreate_failed run_id=%s release=%s: %s",
                    request.run_id, session.release_name, exc,
                )

        created_session = True
        logger.info(
            "Live session auto-created via API key: session=%s run=%s project=%s",
            session_uuid, request.run_id, project_id,
        )
    else:
        session = existing

    accepted = await _persist_event_batch(
        session_id=str(session.id), run_id=request.run_id, events=request.events
    )

    # Detect a run_complete event and finalize the session in the same handler.
    # This is what causes the TestRun row to be created (via upsert_test_run
    # inside close_session). Without it the session stays "active" forever and
    # nothing shows up in the Runs / Overview / Coverage / Failures / Trends
    # pages — those all read from TestRun, not the live Redis state.
    has_run_complete = any(
        (
            event.model_dump() if hasattr(event, "model_dump") else dict(event)
        ).get("event_type") == "run_complete"
        for event in request.events
    )
    if has_run_complete and session.status == "active":
        await close_session(db, str(session.id))

    return LiveStreamIngestResponse(
        accepted=accepted,
        run_id=request.run_id,
        session_id=str(session.id),
        created_session=created_session,
    )


def build_live_session_state(payload: dict) -> LiveSessionState:
    raw_run_id = payload.get("run_id", "")
    return LiveSessionState(
        run_id=raw_run_id,
        test_run_id=str(canonical_test_run_uuid(raw_run_id)) if raw_run_id else None,
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
        launch_name=payload.get("launch_name"),
    )


def build_completed_session_state(session) -> LiveSessionState:
    final_state = (session.extra_metadata or {}).get("final_state", {})
    return LiveSessionState(
        run_id=session.run_id,
        test_run_id=str(canonical_test_run_uuid(session.run_id)) if session.run_id else None,
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
        launch_name=session.launch_name or None,
    )


def build_test_run_fallback_state(run) -> LiveSessionState:
    return LiveSessionState(
        run_id=str(run.id),
        test_run_id=str(run.id),
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
    run_uuid = canonical_test_run_uuid(session.run_id)

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
