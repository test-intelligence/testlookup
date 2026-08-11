from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.services.run_status import terminal_run_status

logger = logging.getLogger(__name__)

# Throttle: warn at most once per process when the live buffer cap is disabled.
_buffer_cap_disabled_warned = False


def _warn_buffer_cap_disabled() -> None:
    global _buffer_cap_disabled_warned
    _buffer_cap_disabled_warned = True
    logger.warning(
        "LIVE_BUFFER_MAX_EVENTS_PER_RUN <= 0 — per-run event buffer is "
        "UNBOUNDED; Redis backpressure for live runs is disabled."
    )


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


async def resolve_project(db: AsyncSession, identifier: str) -> Project:
    """Resolve a project identifier (UUID *or* name) to a Project row.

    Used by the streaming-session endpoint so SDK users can configure either
    ``testlookup.project=<uuid>`` or ``testlookup.project=<name>`` without
    knowing which one the server expects. UUID is tried first because it's
    the unambiguous case; the name fallback is a case-insensitive exact
    match (no fuzzy search — surprising matches would be worse than 404).
    """
    try:
        as_uuid = uuid.UUID(str(identifier))
    except (ValueError, AttributeError, TypeError):
        as_uuid = None

    if as_uuid is not None:
        project = await db.get(Project, as_uuid)
        if project:
            return project

    # Fall through to case-insensitive name lookup.
    result = await db.execute(
        select(Project).where(func.lower(Project.name) == str(identifier).lower())
    )
    project = result.scalar_one_or_none()
    if project:
        return project

    raise HTTPException(status_code=404, detail="Project not found")


_CI_CONTEXT_FIELDS = ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url")


def _with_ci_context(metadata: dict, payload) -> dict:
    """Fold the payload's CI-context fields (US-4.3) into the session's
    extra_metadata under ``ci_context`` — LiveSession has no dedicated
    columns; ``upsert_test_run`` reads this back at persist time and stamps
    the TestRun. Explicit ``metadata['ci_context']`` keys from the caller
    win over the typed fields."""
    ci = {
        f: getattr(payload, f, None)
        for f in _CI_CONTEXT_FIELDS
        if getattr(payload, f, None) is not None
    }
    # US-8.1 — stash a caller-supplied commit range (air-gapped attribution)
    # alongside the CI context. LiveSession has no column for it; upsert_test_run
    # reads it back at persist time and persists the range for the run.
    supplied_range = getattr(payload, "commit_range", None)
    if not ci and not supplied_range:
        return metadata
    merged = dict(metadata)
    if ci:
        merged["ci_context"] = {**ci, **(merged.get("ci_context") or {})}
    if supplied_range and not merged.get("commit_range"):
        # Two accepted wire shapes: a bare commit list, or the
        # boundary-carrying ``{base, head, commits}`` object. Stash whichever
        # arrived as plain JSON — the object form is what lets ``base_commit``
        # survive to the persisted row.
        if isinstance(supplied_range, dict):
            merged["commit_range"] = supplied_range
        elif hasattr(supplied_range, "model_dump"):
            merged["commit_range"] = supplied_range.model_dump()
        else:
            merged["commit_range"] = [
                c.model_dump() if hasattr(c, "model_dump") else c for c in supplied_range
            ]
    return merged


async def create_session(
    db: AsyncSession,
    payload,
    bound_project_id: Optional[uuid.UUID] = None,
) -> LiveSessionResponse:
    """Stage a new LiveSession and return the response shape. Handler commits.

    Redis session-token registration and in-memory run state happen *after*
    the handler's commit so an aborted transaction never leaves a dangling
    session token that authenticates a run which doesn't exist in Postgres.

    ``bound_project_id`` is the UUID a project-scoped API key restricts this
    call to (or ``None`` for JWT / user-scoped keys). It's checked against the
    *resolved* project, not the raw identifier, so passing a project name with
    a UUID-bound key still validates correctly.
    """
    project = await resolve_project(db, payload.project_id)
    if bound_project_id is not None and project.id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

    session_id = str(uuid.uuid4())
    run_id = session_id
    session_token = secrets.token_urlsafe(32)

    # Use the *resolved* project's real UUID for every downstream write. The
    # original payload.project_id may have been a name; project.id is always a
    # UUID so the LiveSession FK and the Redis state stay consistent.
    project_uuid = project.id

    session = LiveSession(
        id=uuid.UUID(session_id),
        project_id=project_uuid,
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
        suite_name=getattr(payload, "suite_name", None) or None,
        started_at=datetime.now(timezone.utc),
        extra_metadata=_with_ci_context(payload.metadata or {}, payload),
    )
    db.add(session)
    await db.flush()

    # Companion TestRun stub so /runs (and the Pipeline-signal KPI on
    # it) include this run from the moment it starts — not 30s later
    # when the Phase 4.5 drainer fires its first non-empty drain, and
    # not at session-complete when persist_live_session runs. Without
    # this, a short live session that finishes inside the drainer
    # window never lands in test_runs at all, and the Pipeline-signal
    # count stays frozen on historical builds. (Bug 2026-05-19.)
    #
    # ``upsert_test_run`` and the drainer both use ``id``-keyed
    # upsert semantics, so this stub is a forward-compatible write —
    # the persist path on session-complete updates these aggregates
    # to the final values and flips ``status`` to its terminal value.
    started_at = session.started_at
    # SAVEPOINT-wrap the stub write so a duplicate-key race with a
    # concurrent ingest/replay can't poison the outer transaction —
    # ``db.begin_nested()`` issues SAVEPOINT and SQLAlchemy auto-rolls
    # back to it on exception. The outer LiveSession write stays
    # committed regardless. Services don't own ``db.rollback()`` on
    # injected sessions; the SAVEPOINT is the right primitive here.
    try:
        async with db.begin_nested():
            stub = TestRun(
                id=uuid.UUID(session_id),
                project_id=project_uuid,
                build_number=payload.build_number or run_id[:8],
                trigger_source="live_stream",
                ingestion_source="live",
                status=LaunchStatus.IN_PROGRESS,
                total_tests=payload.total_tests or 0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                broken_tests=0,
                primary_suite_name=getattr(payload, "suite_name", None) or None,
                suite_names=[getattr(payload, "suite_name", None)] if getattr(payload, "suite_name", None) else None,
                branch=payload.branch,
                commit_hash=payload.commit_hash,
                ci_provider=getattr(payload, "ci_provider", None) or None,
                ci_repo=getattr(payload, "ci_repo", None) or None,
                pr_number=getattr(payload, "pr_number", None),
                ci_actor=getattr(payload, "ci_actor", None) or None,
                ci_run_url=getattr(payload, "ci_run_url", None) or None,
                start_time=started_at,
                end_time=started_at,
            )
            db.add(stub)
            await db.flush()
    except Exception as exc:
        # stdlib ``logging`` doesn't take arbitrary kwargs the way
        # structlog does (the rest of this module is on stdlib logging
        # via ``logger = logging.getLogger(__name__)``). Format the
        # fields into the message instead.
        logger.warning(
            "live_session_test_run_stub_skipped run_id=%s error=%s",
            run_id, exc,
        )

    redis = get_redis()
    await redis.setex(SESSION_TOKEN_KEY.format(token=session_token), SESSION_TTL, session_id)

    from app.streams.live_run_state import RedisLiveRunState

    await RedisLiveRunState.start(
        run_id=run_id,
        project_id=str(project_uuid),
        build_number=payload.build_number or session_id,
        total_tests=payload.total_tests or 0,
        launch_name=getattr(payload, "launch_name", None) or None,
        suite_name=getattr(payload, "suite_name", None) or None,
    )

    logger.info(
        "Live session created: session=%s run=%s project=%s framework=%s",
        session_id,
        run_id,
        project_uuid,
        payload.framework,
    )
    return LiveSessionResponse(
        session_id=session_id,
        session_token=session_token,
        run_id=run_id,
        project_id=str(project_uuid),
        expires_in=SESSION_TTL,
        created_at=session.started_at,
    )


async def get_session(
    db: AsyncSession,
    session_id: str,
    bound_project_id: Optional[uuid.UUID] = None,
) -> dict:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if bound_project_id is not None and session.project_id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

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


async def close_session(
    db: AsyncSession,
    session_id: str,
    bound_project_id: Optional[uuid.UUID] = None,
) -> None:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Project-scoped API key: the session must belong to the bound project.
    # JWT and user-scoped API keys pass ``bound_project_id=None`` and skip
    # this check (membership at the route level was previously enforced by
    # ``require_live_session_access``; the route now relies on this service
    # check so X-API-Key callers don't get a spurious 401).
    if bound_project_id is not None and session.project_id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

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

    # ── 15-day durable event archive ───────────────────────────────────────
    # The Redis buffer that holds raw SDK events for a live run has a
    # 25-hour TTL. ``persist_live_session`` normally drains it minutes after
    # close_session fires, but a worker crash / transient error can leave
    # ``test_cases`` rows missing once the TTL lapses — and the user reports
    # "Recover from buffer" failing on day 2+ of a run they want to revisit.
    # Copy the events to ``TestRun.event_archive`` *before* queuing persist
    # so the Run Intelligence page stays recoverable for 15 days from this
    # moment regardless of the Redis TTL. Best-effort: a failure here must
    # not block session close.
    try:
        from app.streams import LIVE_TESTCASES_KEY

        redis = get_redis()
        list_key = LIVE_TESTCASES_KEY.format(run_id=session.run_id)
        raw_events = await redis.lrange(list_key, 0, -1)
        decoded: list = []
        for raw in raw_events:
            try:
                decoded.append(json.loads(raw))
            except Exception:
                continue
        if decoded:
            run_uuid = canonical_test_run_uuid(session.run_id)
            tr = (
                await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            ).scalar_one_or_none()
            if tr is not None:
                tr.event_archive = decoded
                tr.event_archive_at = now
                # NOTE: this module uses stdlib ``logging`` (see line 26).
                # Stdlib's Logger doesn't accept structlog-style ``key=value``
                # kwargs — passing them raises ``TypeError: Logger._log()
                # got an unexpected keyword argument 'session_id'`` which
                # then propagates as a 500 from the wrapping handler.
                # Use stdlib-format f-strings (the prevailing style in this
                # module) so the call works regardless of whether the
                # exception path or the success path fires.
                logger.info(
                    f"live_event_archive_written run_id={session.run_id} "
                    f"event_count={len(decoded)}"
                )
    except Exception as arc_err:
        # Archive failures are non-fatal — persist_live_session reads from
        # Redis first, so as long as the buffer is fresh recovery still
        # works. The user only loses the long-tail (>25h) recovery path.
        logger.warning(
            f"live_event_archive_failed session_id={session_id}: {arc_err}"
        )

    # Release linking — explicit session.release_name wins; otherwise fall
    # back to the project's default release (migration 0077). Wrapped in a
    # broad try/except so a release-linking error never blocks session close.
    try:
        from app.services.release_linker import link_run_or_default

        await link_run_or_default(
            db=db,
            project_id=session.project_id,
            release_name=session.release_name,
            # run_id is a slug for live sessions (e.g. "local-abc123"); the
            # TestRun row is persisted under canonical_test_run_uuid(run_id),
            # so the release link must target the SAME uuid. Using
            # uuid.UUID(session.run_id) raised ValueError on every slug run,
            # silently skipping release linking via the broad except below.
            test_run_id=canonical_test_run_uuid(session.run_id),
        )
    except Exception as rel_err:
        logger.warning(
            f"live_session_release_link_failed session_id={session_id}: {rel_err}"
        )

    # stage-only: handler commits the LiveSession close, the upserted
    # TestRun, and any release link together.

    try:
        from app.worker.tasks import persist_live_session
        from app.worker.ingestion_routing import queue_for_project

        # Phase 2.4 — route per-project to a shard queue so one noisy
        # project can't starve another's persist throughput. Falls back
        # to the legacy ``ingestion`` queue when sharding is disabled
        # (``LIVE_INGEST_SHARD_COUNT=0`` — used in tests).
        target_queue = queue_for_project(str(session.project_id))

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
                "suite_name": session.suite_name or None,
            },
            queue=target_queue,
            priority=7,
        )
        logger.info(
            f"queued_persist_live_session session_id={session_id} queue={target_queue}"
        )
    except Exception as exc:
        logger.warning(
            f"persist_live_session_queue_failed session_id={session_id}: {exc}"
        )

    try:
        # ``session.run_id`` is the SDK-supplied slug (e.g. ``local-abc12345``),
        # not necessarily a UUID. The pipeline task writes to
        # ``agent_pipeline_runs.test_run_id`` which is ``UUID(as_uuid=True)``
        # with an FK to ``test_runs.id``. Passing the raw slug here caused
        # the task to silently fail on insert and no AgentPipelineRun row
        # was ever created — so the /agents page showed "No agent pipelines
        # yet" for every live_stream run whose SDK didn't use UUID slugs.
        # Convert to the canonical UUID the same way upsert_test_run and
        # persist_live_session do.
        canonical_run_uuid = canonical_test_run_uuid(session.run_id)

        # Phase 3 — route through the debouncer so per-project bursts
        # don't fire 500 LLM pipelines at once. The debouncer also
        # applies the daily $10 LLM budget cap (services/llm_cost_budget)
        # before fanning out, so projects over budget transparently
        # degrade to rules/ml instead of falling off the cliff.
        # ``AI_PIPELINE_DEBOUNCE_ENABLED=False`` reverts to immediate
        # dispatch for tests + any deploy that hasn't enabled the beat
        # schedule yet.
        from app.services.ai_pipeline_debouncer import enqueue_pipeline_for_run

        await enqueue_pipeline_for_run(
            project_id=str(session.project_id),
            test_run_id=str(canonical_run_uuid),
            build_number=session.build_number or session_id,
            workflow_type="offline",
        )
        logger.info(
            f"ai_pipeline_queued_from_live session_id={session_id} "
            f"session_run_id={session.run_id} canonical_run_id={canonical_run_uuid}"
        )
    except Exception as exc:
        logger.warning(
            f"ai_pipeline_queue_failed session_id={session_id}: {exc}"
        )


async def _resolve_project_id_for_run(run_id: str) -> Optional[str]:
    """Helper used by ``_persist_event_batch`` to attribute the batch's
    test-event count to the right project for the high-volume detector.
    Best-effort: returns None on lookup failure and the detector simply
    skips this batch (the rolling counter is observability, not auth)."""
    try:
        from app.db.postgres import AsyncSessionLocal
        run_uuid = canonical_test_run_uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(TestRun, run_uuid)
            return str(run.project_id) if run else None
    except Exception:
        return None


async def _persist_event_batch(session_id: str, run_id: str, events) -> int:
    """Publish events to Redis Streams and update the live-state hash.

    Shared by the session-token path (``ingest_event_batch``) and the
    API-key path (``ingest_via_api_key``). Buffering test_result events into
    the Redis list and HINCRBY-ing the counter hash must happen here —
    synchronously in the HTTP handler — not in the async stream consumer,
    because close_session() / persist_live_session can be dispatched before
    the consumer processes the stream (race condition).
    """
    from app.streams import LIVE_TESTCASES_KEY, LIVE_STATE_KEY

    accepted = await publish_event_batch(session_id=session_id, run_id=run_id, events=events)

    redis = get_redis()
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
    state_key = LIVE_STATE_KEY.format(run_id=run_id)
    counter_map = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "BROKEN": "broken"}
    now = datetime.now(timezone.utc).isoformat()
    last_test_name = ""

    pipe = redis.pipeline()
    test_event_count = 0
    for event in events:
        event_dict: dict = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        if event_dict.get("event_type") == "test_result":
            test_event_count += 1
            entry = json.dumps({
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

    # Phase 2.1 — bound the per-run list to LIVE_BUFFER_MAX_EVENTS_PER_RUN
    # so a single long-lived run can't grow the Redis footprint unbounded
    # even while it's within rate-limit budget. ``LTRIM`` keeps the
    # NEWEST events (oldest get evicted) so the dashboard always shows
    # the most recent activity. The trade-off: if persist_live_session
    # is delayed past the buffer cap, the dropped events are lost for
    # the per-test rollup. Aggregates remain accurate because they
    # come from HINCRBY counters that we DON'T trim. See
    # docs/SCALABLE_INGESTION_DESIGN.md § Phase 2.
    buffer_cap = settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN
    if buffer_cap > 0:
        # LTRIM start=-N keeps the last N entries. Cheap O(1) operation
        # on Redis Lists; we run it on every batch so the cap is enforced
        # as soon as it's exceeded rather than only at TTL refresh.
        await await_if_needed(pipe.ltrim(list_key, -buffer_cap, -1))
    elif not _buffer_cap_disabled_warned:
        # cap <= 0 disables backpressure entirely (unbounded per-run list).
        # Surface it once so a misconfigured deploy is observable rather than
        # silently dropping the safeguard.
        _warn_buffer_cap_disabled()
    await await_if_needed(pipe.expire(list_key, 90_000))  # 25 h TTL — same as consumer's buffer
    if last_test_name:
        await await_if_needed(pipe.hset(state_key, mapping={"last_event_at": now, "current_test": last_test_name}))
    else:
        await await_if_needed(pipe.hset(state_key, "last_event_at", now))
    await await_if_needed(pipe.expire(state_key, 86_400))
    await pipe.execute()

    # Phase 4.1 — feed the high-volume detector with the test_result event
    # count from this batch (counted in the loop above — no second model_dump
    # pass). Project_id is looked up lazily (one cached DB read per run) and
    # never blocks the ingest path — ``record_test_events`` swallows its errors.
    if test_event_count > 0:
        try:
            from app.services.high_volume_detector import record_test_events
            project_id = await _resolve_project_id_for_run(run_id)
            if project_id:
                await record_test_events(project_id, test_event_count)
        except Exception as exc:
            logger.warning(
                "high_volume_record_failed run_id=%s error=%s", run_id, exc,
            )

    return accepted


async def resolve_project_id_for_session(
    session_id: str, x_session_token: str,
) -> Optional[uuid.UUID]:
    """Look up the project_id for a live session, given its session-token.

    Used by the ``/stream/events/batch`` admission gate so the per-project
    rate-limit charge lands on the right bucket. Returns ``None`` when
    the session token isn't recognised — the caller treats that as
    "skip the rate-limit charge", and ``ingest_event_batch`` will then
    raise its own 401 a few lines later. We don't want to surface the
    token failure twice or burn a rate-limit token on an invalid auth.
    """
    redis = get_redis()
    try:
        stored = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    except Exception:
        return None
    if not stored or not secrets.compare_digest(stored, session_id):
        return None
    # Look up the project_id on the LiveSession row. The cheaper path
    # would be to cache (token → project_id) in Redis at session-create
    # time, but that's a tier-2 optimisation; the per-request DB cost
    # here is one indexed PK lookup.
    from app.db.postgres import AsyncSessionLocal

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    async with AsyncSessionLocal() as db:
        session = await db.get(LiveSession, sid)
        return session.project_id if session else None


async def ingest_event_batch(batch, x_session_token: str) -> LiveEventBatchResponse:
    redis = get_redis()
    stored_session_id = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    if not stored_session_id or not secrets.compare_digest(stored_session_id, batch.session_id):
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
        # SAVEPOINT-wrap the insert: a concurrent first-batch for the same
        # (project_id, run_id) hits the partial unique index
        # ``ix_live_sessions_active_run`` (active sessions only). Without this
        # guard the loser's flush raises IntegrityError, which 500s the SDK's
        # first batch instead of degrading to "reuse the winning session".
        # ``begin_nested`` unwinds only the failed insert; the outer
        # transaction stays usable for the re-select + event persist below.
        raced = False
        try:
            async with db.begin_nested():
                db.add(session)
                await db.flush()
        except IntegrityError:
            raced = True
            session = (
                await db.execute(
                    select(LiveSession)
                    .where(
                        LiveSession.project_id == project_id,
                        LiveSession.run_id == request.run_id,
                    )
                    .order_by(LiveSession.started_at.desc())
                    .limit(1)
                )
            ).scalar_one()
            logger.info(
                "live_session_create_race_reusing_winner run_id=%s project=%s",
                request.run_id, project_id,
            )

        if not raced:
            # Only the runner that actually created the row registers the
            # Redis token / run-state and auto-creates the release — the
            # race winner already did all of this for its own create.
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
        # Pass the API key's bound project so close_session re-asserts scope
        # (defense-in-depth parity with the JWT path), not just the row lookup.
        await close_session(db, str(session.id), bound_project_id=project_id)

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
        suite_name=payload.get("suite_name"),
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
        suite_name=getattr(session, "suite_name", None) or None,
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
        suite_name=getattr(run, "primary_suite_name", None) or None,
    )


async def list_active_sessions(
    db: AsyncSession,
    project_id: Optional[str] = None,
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
    suite_name: Optional[str] = None,
    days: int = 7,
) -> ActiveSessionsResponse:
    """
    List active + recent live sessions, enforcing tenant isolation.

    - ``project_id`` (when set) narrows the result to that single project.
    - ``allowed_project_ids`` (when set) constrains the result to the caller's
      accessible project set — used for non-admin callers without a pinned
      project. A value of ``None`` means no constraint (ADMIN or a project_id
      that has already been verified).
    - ``days`` controls the cutoff for *completed* sessions/runs joined onto
      the always-current active set. 1 = last 24 hours; 0 = no cutoff.
    """
    from app.streams.live_run_state import RedisLiveRunState

    suite_key = (suite_name or "").strip().lower()

    # Soft-deleted projects are excluded for every caller. The two filters
    # below are conditional by design (a pinned project, or a non-admin's
    # membership set), so an ADMIN with no project pinned matched neither and
    # saw sessions from projects that no longer exist. Measured live: of 9
    # entries, 4 belonged to two deleted probe projects.
    #
    # Eighth surface in this family (#535 runs, #538 dashboard, #539 analytics,
    # #541 ROI, #547 trends, #549 defect KPI, #550 releases). Applied to the
    # Redis set and both DB queries below, because this endpoint unions three
    # sources and filtering only one would leave the other two leaking.
    live_project_ids = {
        str(pid)
        for pid in (
            await db.execute(select(Project.id).where(Project.is_active.is_(True)))
        ).scalars().all()
    }

    all_active = await RedisLiveRunState.get_all_active()
    all_active = [s for s in all_active if s.get("project_id") in live_project_ids]
    if project_id:
        all_active = [session for session in all_active if session.get("project_id") == project_id]
    elif allowed_project_ids is not None:
        allowed_str = {str(pid) for pid in allowed_project_ids}
        all_active = [s for s in all_active if s.get("project_id") in allowed_str]
    if suite_key:
        all_active = [
            session
            for session in all_active
            if (session.get("suite_name") or "").strip().lower() == suite_key
        ]

    # Dedup on the CANONICAL run UUID, not the raw id.
    #
    # Redis keys a live run by whatever the SDK supplied — frequently a slug
    # like ``local-abc12345`` — while the DB rows carry the UUID persisted for
    # it. Comparing the two forms directly never matches, so one logical run
    # was listed twice. Measured live after the scoping fix, the Live page
    # showed exactly two rows and both were the same run:
    #
    #     sdk-probe-1 -> sdk-probe-1                            (Redis, slug)
    #     sdk-probe-1 -> bd337e00-38ae-50ee-b2e5-0f21077a711b   (DB, UUID)
    #
    # ``canonical_test_run_uuid`` is the module's existing mapping for exactly
    # this; its docstring already records three earlier call sites that drifted
    # apart the same way. This is the *opposite* failure to the older
    # build_number dedup bug: two runs may legitimately share a build number,
    # but one run must never carry two identities.
    active_run_ids = {
        canonical_test_run_uuid(str(session.get("run_id")))
        for session in all_active
        if session.get("run_id")
    }
    active_sessions = [build_live_session_state(session) for session in all_active]

    # ``days=0`` disables the cutoff so the caller sees every completed session
    # the limit allows (still capped to 50 below).
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    )
    stmt = (
        select(LiveSession)
        .where(
            LiveSession.status == "completed",
            # Same life-cycle exclusion as the Redis set above.
            LiveSession.project_id.in_(
                select(Project.id).where(Project.is_active.is_(True))
            ),
        )
        .order_by(LiveSession.completed_at.desc())
        .limit(50)
    )
    if cutoff is not None:
        stmt = stmt.where(LiveSession.completed_at >= cutoff)
    if project_id:
        try:
            stmt = stmt.where(LiveSession.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        if not allowed_project_ids:
            return ActiveSessionsResponse(sessions=[], count=0)
        stmt = stmt.where(LiveSession.project_id.in_(allowed_project_ids))
    if suite_key:
        stmt = stmt.where(func.lower(func.trim(LiveSession.suite_name)) == suite_key)

    db_sessions = (await db.execute(stmt)).scalars().all()
    seen_run_ids = set(active_run_ids)
    completed_sessions = []
    for session in db_sessions:
        canonical = canonical_test_run_uuid(str(session.run_id))
        if canonical in seen_run_ids:
            continue
        seen_run_ids.add(canonical)
        completed_sessions.append(build_completed_session_state(session))

    tr_stmt = (
        select(TestRun)
        .where(
            TestRun.trigger_source == "live_stream",
            # Third source of this union — the same exclusion applies.
            TestRun.project_id.in_(
                select(Project.id).where(Project.is_active.is_(True))
            ),
        )
        .order_by(TestRun.start_time.desc())
        .limit(50)
    )
    if cutoff is not None:
        tr_stmt = tr_stmt.where(TestRun.start_time >= cutoff)
    if project_id:
        try:
            tr_stmt = tr_stmt.where(TestRun.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        tr_stmt = tr_stmt.where(TestRun.project_id.in_(allowed_project_ids))
    if suite_key:
        tr_stmt = tr_stmt.where(func.lower(func.trim(TestRun.primary_suite_name)) == suite_key)

    tr_runs = (await db.execute(tr_stmt)).scalars().all()
    for run in tr_runs:
        if run.id in seen_run_ids:
            continue
        seen_run_ids.add(run.id)
        completed_sessions.append(build_test_run_fallback_state(run))

    sessions = active_sessions + completed_sessions

    # Stamp the per-(project, suite) run sequence on every session so the
    # /live UI can render "Run #N" instead of the opaque SDK-supplied
    # build_number. Resolves via the canonical ``test_run_id`` — both
    # active sessions (Phase 4.5 drain pre-creates the TestRun row in
    # IN_PROGRESS state) and completed ones (TestRun already exists)
    # are covered. Bulk-fetched once for the full page payload.
    from app.services.runs_service import fetch_run_seq_map
    seq_ids: list[uuid.UUID] = []
    for s in sessions:
        if s.test_run_id:
            try:
                seq_ids.append(uuid.UUID(s.test_run_id))
            except ValueError:
                # ``run_id`` slugs that don't round-trip through UUID
                # are pre-Phase-4.5 legacy rows; their run_seq stays None.
                continue
    if seq_ids:
        seq_map = await fetch_run_seq_map(db, seq_ids)
        for s in sessions:
            if s.test_run_id and s.test_run_id in seq_map:
                s.run_seq = seq_map[s.test_run_id]

    return ActiveSessionsResponse(sessions=sessions, count=len(sessions))


async def upsert_test_run(db: AsyncSession, session: LiveSession, state: dict) -> None:
    run_uuid = canonical_test_run_uuid(session.run_id)

    passed = int(state.get("passed", 0))
    failed = int(state.get("failed", 0))
    skipped = int(state.get("skipped", 0))
    broken = int(state.get("broken", 0))
    # Results whose status the server could not interpret. Included in the
    # total fallback so they cannot be erased from the run's own arithmetic.
    unknown = int(state.get("unknown", 0))
    total = int(state.get("total", session.total_tests or 0))
    # Fallback: if total wasn't tracked, derive it from component counts
    total = total or (passed + failed + skipped + broken + unknown)
    # Pass rate EXCLUDES skipped from the denominator (passed / passed+failed+broken),
    # consistent with live_consumer and ingestion. Skips are neither pass nor fail.
    executed = passed + failed + broken
    pass_rate = round(passed / executed * 100, 2) if executed > 0 else None
    # A closed run that executed nothing (empty session / all-skipped) grades as
    # STOPPED, not PASSED — mirrors ingestion._update_run_aggregates via the
    # shared helper. Runs at close_session only; the drainer creates the
    # in-progress row as IN_PROGRESS, so this never mislabels a live run.
    run_status = terminal_run_status(executed, failed, broken, unknown)
    now = datetime.now(timezone.utc)

    # Run-level suite identifier supplied by the SDK on session create. We
    # stamp it on the TestRun immediately so every UI page that joins the run
    # shows a single suite label without waiting for per-event aggregation
    # in _update_run_aggregates.
    suite_label = getattr(session, "suite_name", None) or None

    # CI context (US-4.3): stashed in extra_metadata["ci_context"] at session
    # create — stamp onto the TestRun (fill-if-null; never overwrite).
    ci_context = (getattr(session, "extra_metadata", None) or {}).get("ci_context") or {}

    run = (await db.execute(select(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if run is None:
        run = TestRun(
            id=run_uuid,
            project_id=session.project_id,
            build_number=session.build_number or str(session.id)[:8],
            trigger_source="live_stream",
            ingestion_source="live",
            branch=session.branch or None,
            commit_hash=session.commit_hash or None,
            ci_provider=ci_context.get("ci_provider"),
            ci_repo=ci_context.get("ci_repo"),
            pr_number=ci_context.get("pr_number"),
            ci_actor=ci_context.get("ci_actor"),
            ci_run_url=ci_context.get("ci_run_url"),
            status=run_status,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            broken_tests=broken,
            unknown_tests=unknown,
            pass_rate=pass_rate,
            primary_suite_name=suite_label,
            suite_names=[suite_label] if suite_label else None,
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
        run.unknown_tests = unknown
        run.pass_rate = pass_rate
        run.end_time = now
        # Only overwrite suite when SDK provided one — preserve any value
        # already populated by per-event aggregation.
        if suite_label and not run.primary_suite_name:
            run.primary_suite_name = suite_label
            run.suite_names = [suite_label]
        # CI context: fill-if-null only (the create_session stub usually
        # already carries it; this covers drainer-created rows).
        for field in ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url"):
            value = ci_context.get(field)
            if value is not None and getattr(run, field) is None:
                setattr(run, field, value)
        logger.info(
            f"test_run_updated_for_live_session run_id={run_uuid} session_id={session.id}"
        )

    # US-8.1 — persist a caller-supplied commit range stashed at session
    # create. Staged under this session (handler commits); best-effort so a
    # malformed list never fails live-run persistence.
    supplied_range = (getattr(session, "extra_metadata", None) or {}).get("commit_range")
    if supplied_range:
        try:
            await db.flush()
            from app.services.commit_attribution_service import store_supplied_range
            await store_supplied_range(db, run, supplied_range)
        except Exception as exc:
            logger.warning(
                f"live_supplied_commit_range_store_failed run_id={run_uuid} error={exc}"
            )
