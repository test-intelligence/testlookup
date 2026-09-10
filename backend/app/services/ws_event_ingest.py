"""``POST /ws/events/{run_id}``, persisted the way the SDK's live stream is (re-audit N14).

That endpoint takes one event per call, and it used to do exactly one thing
with it: put it on the shared live-events stream for the dashboard. No
LiveSession, no TestRun, no evidence, no persistence -- a run streamed through
it showed live and then vanished. It never reached /runs, was never analysed,
and was never finalised. The SDK's own endpoint, ``POST /api/v1/stream/ingest``,
does all of that. So an event sent with a project-scoped key is now translated
into that endpoint's event shape and admitted through the same path:

* the first event of a run creates the LiveSession, keyed by a fresh UUID with
  the caller's run_id kept as its display id, as for any SDK run;
* every result is admitted to the run's evidence stream and counted by the
  admission script itself, so nothing is counted twice;
* ``run_complete`` closes the session, which creates the TestRun and stages the
  persistence and finalisation every SDK run gets.

Two things differ from the SDK, because this endpoint is one event per call:

* Each POST gets its own ``batch_id``. The admission de-duplicates by batch and,
  without one, hashes the content -- so two genuinely distinct results that
  happen to be byte-identical would have collapsed into one.
* The session id is cached in Redis, per project and run. The full path costs
  two SELECTs; paying that for every single event would bring back the
  per-event database load the credential cache exists to avoid (see
  ``live_event_authz``). Only a run's first event, a cache miss and
  ``run_complete`` touch the database.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import LiveEvent, LiveStreamIngestRequest, LiveStreamMeta

logger = structlog.get_logger(__name__)

# As long as the run's project binding (live_event_authz.RUN_PROJECT_TTL_SECONDS).
SESSION_CACHE_TTL_SECONDS = 25 * 60 * 60
_SESSION_KEY = "testlookup:ws-events:session:{project_id}:{run_id}"


def session_cache_key(project_id: uuid.UUID, run_id: str) -> str:
    return _SESSION_KEY.format(project_id=project_id, run_id=run_id)


def _count(value: Any) -> Optional[int]:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _text(value: Any, limit: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text[:limit] if limit is not None else text


def to_live_event(event: dict) -> LiveEvent:
    """This endpoint's ``{type, ...}`` payload as the SDK stream's ``LiveEvent``.

    Lenient on purpose. The endpoint accepted any of these values before, so a
    client sending a float duration or a non-list tag must not start getting a
    422 now: a value the SDK model cannot hold is dropped, not rejected.
    """
    tags = event.get("tags")
    metadata = event.get("metadata")
    return LiveEvent(
        event_type=str(event.get("type") or "test_result"),
        test_name=_text(event.get("test_name"), 1000),
        status=_text(event.get("status")),
        duration_ms=_count(event.get("duration_ms")),
        error_message=_text(event.get("error_message")),
        stack_trace=_text(event.get("stack_trace")),
        suite_name=_text(event.get("suite_name"), 500),
        class_name=_text(event.get("class_name"), 500),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else None,
        timestamp_ms=_count(event.get("timestamp_ms")),
        metadata=metadata if isinstance(metadata, dict) else None,
    )


def meta_for(event: dict) -> Optional[LiveStreamMeta]:
    """The session metadata a ``run_start`` carries; used only if it creates the session."""
    if event.get("type") != "run_start":
        return None
    return LiveStreamMeta(
        build_number=_text(event.get("build_number"), 100),
        total_tests=_count(event.get("total_tests")),
    )


async def ingest_one(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    api_key_name: str,
    run_id: str,
    event: dict,
) -> str:
    """Admit one event through the SDK stream's path. Returns the session id."""
    from app.services import stream_service

    try:
        live_event = to_live_event(event)
        request = LiveStreamIngestRequest(
            run_id=run_id,
            batch_id=f"ws-{uuid.uuid4().hex}",
            events=[live_event],
            meta=meta_for(event),
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This event cannot be recorded as part of a run: " + "; ".join(
                ".".join(str(part) for part in err.get("loc", ())) + ": " + str(err.get("msg", "invalid"))
                for err in exc.errors()
            ),
        ) from exc

    cache_key = session_cache_key(project_id, run_id)
    completes = live_event.event_type == "run_complete"

    session_id = None if completes else await _cached_session(cache_key)
    if session_id:
        # The session exists and is this project's: the key includes the
        # project, and the route has already checked the run's binding.
        await stream_service._persist_event_batch(
            session_id=session_id,
            run_id=session_id,
            events=request.events,
            batch_id=request.batch_id,
            project_id=str(project_id),
        )
        return session_id

    response = await stream_service.ingest_via_api_key(
        db=db, project_id=project_id, api_key_name=api_key_name, request=request,
    )
    # The stream router's order, for the same reason: the Redis side of a
    # close may only be finalised once the close itself is durable.
    await db.commit()
    if completes:
        await stream_service.finalize_closed_session_redis(response.session_id)
        await _forget_session(cache_key)
    else:
        await _remember_session(cache_key, response.session_id)
    return response.session_id


async def _cached_session(cache_key: str) -> Optional[str]:
    try:
        from app.db.redis_client import get_redis

        value = await get_redis().get(cache_key)
    except Exception as exc:  # noqa: BLE001 -- degrades to the full path
        # Slower, never wrong: a miss takes the database path.
        logger.warning("ws_events_session_cache_unavailable", error=str(exc))
        return None
    if isinstance(value, bytes):
        value = value.decode()
    return value or None


async def _remember_session(cache_key: str, session_id: str) -> None:
    try:
        from app.db.redis_client import get_redis

        await get_redis().set(cache_key, session_id, ex=SESSION_CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- the next event just misses
        logger.warning("ws_events_session_cache_write_failed", error=str(exc))


async def _forget_session(cache_key: str) -> None:
    try:
        from app.db.redis_client import get_redis

        await get_redis().delete(cache_key)
    except Exception as exc:  # noqa: BLE001 -- a stale entry hits the closed fence
        logger.warning("ws_events_session_cache_delete_failed", error=str(exc))
