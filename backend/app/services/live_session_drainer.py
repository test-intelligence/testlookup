"""Phase 4.5 — incremental drain of live-stream event buffers.

The live-stream ingestion path RPUSHes per-test events to a Redis LIST at
``LIVE_TESTCASES_KEY`` and ``HINCRBY``-bumps run aggregates on a Hash at
``LIVE_STATE_KEY``. Historically the LIST was only drained to Postgres
at session close, which left three failure modes:

1. **Cap overflow** — ``LTRIM list -50000 -1`` runs on every batch
   (`stream_service._persist_event_batch`). Runs > 50K events lose
   their oldest per-test rows; HINCRBY aggregates stay accurate so
   ``TestRun.total_tests`` reports a count the ``test_cases`` table
   can no longer back up.
2. **Memory-pressure eviction** — under ``maxmemory`` + ``allkeys-lru``,
   the LIST key can vanish entirely between the SDK's last event and
   ``close_session``. Per-test rows are lost; aggregates survive
   because the Hash is hotter.
3. **TTL expiry** — 25-hour TTL on the LIST. Sessions that never call
   ``close_session`` (SDK crash, network partition) lose everything
   once the reaper sweeps after TTL.

Phase 4.5 drains the buffer on a 30-second cadence DURING the session
so the buffer cap only ever evicts events already durable in
``test_cases``. Aggregates remain authoritative on the Hash;
``upsert_test_run`` at close time still stamps the final values.

Idempotency:
  * ``LRANGE 0 N-1`` + ``LTRIM N -1`` is safe under "writers only
    append" semantics — writers RPUSH, never read. Between the LRANGE
    and the LTRIM a writer can append; LTRIM ``N -1`` then keeps the
    newly-pushed entry intact (it's at index ``N`` of the post-trim
    list = head).
  * A per-run Redis SET-NX lock with short TTL prevents two beat ticks
    racing on the same buffer. A stale lock from a crashed worker
    expires naturally; the worst case is a 60-second drain delay for
    that run.

Caller contract (per the single-owner rule in ``backend/CLAUDE.md``):
each drain opens its own ``AsyncSessionLocal`` so the Celery task that
schedules it doesn't need to thread a session through.
"""
from __future__ import annotations

import hashlib
import json
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import insert as _sa_insert
from sqlalchemy import select as _sel

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.db.redis_client import get_redis
from app.models.postgres import LaunchStatus, TestCase, TestRun, TestStatus
from app.streams import LIVE_TESTCASES_KEY
from app.services.run_tombstone_service import run_is_tombstoned
from app.services.ingestion_sanitization import sanitize_test_result_payload
from app.streams.live_run_state import RedisLiveRunState

logger = structlog.get_logger(__name__)

# Per-run lock key. TTL slightly longer than the beat interval so a
# worker hang doesn't permanently strand the run; short enough that
# a real crash unblocks the next tick.
_DRAIN_LOCK_KEY = "testlookup:live:drain_lock:{run_id}"
_DRAIN_LOCK_TTL_SECONDS = 90


def _resolved_started_at(state: dict, now: datetime) -> datetime:
    """Real session start from the Redis live-state hash (stamped at
    session-create as an ISO string), not the first-drain tick.

    Close-time ``upsert_test_run`` only sets ``start_time`` when it CREATES the
    TestRun row; on an already-existing row (one the drainer materialised) it
    updates totals/end_time but leaves ``start_time`` untouched. So if the
    drainer creates the row with ``now`` (the first 30s tick), that skewed-late
    start persists through close — for exactly the long-running runs Phase 4.5
    targets. Resolve the authoritative start here instead.
    """
    raw = state.get("started_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            pass
    return now


def _resolved_suite(event: dict, default_suite: Optional[str]) -> Optional[str]:
    """Mirror ``persist_live_session``'s suite_name resolution. SDKs send
    ``testlookup.suite`` once at session-create; per-event fields are
    usually empty. Fall back to the session-level default so per-suite
    grouping queries don't see NULL.
    """
    event_suite = (event.get("suite_name") or "").strip()
    resolved = (event_suite or default_suite or "")[:500]
    return resolved or None


def _event_to_row(
    event: dict,
    *,
    run_uuid: _uuid_mod.UUID,
    default_suite: Optional[str],
) -> dict:
    """Build a single TestCase row dict for ``insert(TestCase).execute(rows)``.

    Mirrors the construction in ``persist_live_session`` exactly — both
    paths must produce identical row shapes so a buffer drained mid-run
    is indistinguishable from one drained at close.
    """
    safe_event = sanitize_test_result_payload(event)
    test_name = safe_event.get("test_name") or ""
    class_name = safe_event.get("class_name") or ""
    raw_status = (safe_event.get("status") or "UNKNOWN").upper()
    try:
        tc_status = TestStatus(raw_status)
    except ValueError:
        tc_status = TestStatus.UNKNOWN
    fingerprint = hashlib.md5(
        f"{test_name}:{class_name}".encode()
    ).hexdigest()
    return {
        "id": _uuid_mod.uuid4(),
        "test_run_id": run_uuid,
        "test_fingerprint": fingerprint,
        "test_name": test_name[:1000],
        "suite_name": _resolved_suite(safe_event, default_suite),
        "class_name": class_name[:500] or None,
        "status": tc_status.value if hasattr(tc_status, "value") else tc_status,
        "duration_ms": safe_event.get("duration_ms"),
        "error_message": safe_event.get("error_message"),
        "stack_trace": safe_event.get("stack_trace"),
        "tags": safe_event.get("tags"),
    }


async def _acquire_lock(redis, run_id: str) -> bool:
    """Best-effort SET NX EX lock. Returns True iff this caller owns it."""
    key = _DRAIN_LOCK_KEY.format(run_id=run_id)
    # redis.set with nx + ex returns True/None depending on the client;
    # both indicate "got it" vs "didn't". Treat truthy as success.
    return bool(await redis.set(key, "1", nx=True, ex=_DRAIN_LOCK_TTL_SECONDS))


async def _release_lock(redis, run_id: str) -> None:
    key = _DRAIN_LOCK_KEY.format(run_id=run_id)
    try:
        await redis.delete(key)
    except Exception:  # pragma: no cover - lock will TTL out
        pass


async def drain_run_buffer(
    run_id: str,
    project_id: str,
    *,
    build_number: str = "",
    suite_name: Optional[str] = None,
    max_events: Optional[int] = None,
) -> dict:
    """Drain up to ``max_events`` events from one live run's Redis buffer
    into Postgres ``test_cases`` rows.

    Materialises ``TestRun`` row (status=IN_PROGRESS) on first drain.
    Refreshes aggregate counters from the authoritative HINCRBY hash so
    cap-overflowed runs report accurate totals.

    Does NOT call ``finalize_run`` — that stays terminal at close-time.

    Returns ``{drained: int, lock_held: bool, run_uuid: str|None}`` for
    observability.
    """
    # Local import so unit tests can monkeypatch the symbol on the
    # services module without dragging in stream_service's heavy deps.
    from app.services.stream_service import canonical_test_run_uuid

    result = {"drained": 0, "lock_held": False, "run_uuid": None}

    chunk = max_events or settings.LIVE_SESSION_DRAIN_BATCH_SIZE
    if chunk <= 0:
        return result

    redis = get_redis()
    if not await _acquire_lock(redis, run_id):
        # Another drain tick is mid-flight for this run. Skip — the
        # next beat tick will pick up whatever this one leaves behind.
        return result
    result["lock_held"] = True

    try:
        list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
        raw_entries = await redis.lrange(list_key, 0, chunk - 1)
        if not raw_entries:
            return result

        try:
            proj_uuid = _uuid_mod.UUID(project_id)
        except ValueError:
            logger.error(
                "drain_invalid_project_id",
                run_id=run_id, project_id=project_id,
            )
            return result

        events: list[dict] = []
        for raw in raw_entries:
            try:
                events.append(json.loads(raw))
            except Exception:
                continue
        if not events:
            # Garbled buffer entries — still LTRIM them so we don't
            # re-read the same junk on the next tick.
            await redis.ltrim(list_key, len(raw_entries), -1)
            return result

        run_uuid = canonical_test_run_uuid(run_id)
        result["run_uuid"] = str(run_uuid)
        now = datetime.now(timezone.utc)

        # Authoritative aggregates come from the HINCRBY hash; the
        # event slice we just popped is only a window of the run.
        state = await RedisLiveRunState.get(run_id) or {}
        agg_passed = int(state.get("passed", 0) or 0)
        agg_failed = int(state.get("failed", 0) or 0)
        agg_skipped = int(state.get("skipped", 0) or 0)
        agg_broken = int(state.get("broken", 0) or 0)
        # Results the server could not interpret. Counted here so they cannot
        # vanish from ``total`` — an unrecognised status used to increment no
        # counter at all while its TestCase row was still written as UNKNOWN.
        agg_unknown = int(state.get("unknown", 0) or 0)
        agg_total = int(state.get("total", 0) or 0)
        agg_total = agg_total or (
            agg_passed + agg_failed + agg_skipped + agg_broken + agg_unknown
        )

        session_suite = (suite_name or state.get("suite_name") or "").strip() or None
        started_at = _resolved_started_at(state, now)

        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(_sel(TestRun).where(TestRun.id == run_uuid))
            ).scalar_one_or_none()
            if run is None and await run_is_tombstoned(db, run_uuid):
                # Deliberately deleted: draining would recreate the row with
                # its events and objects already gone.
                logger.info(
                    "drain_skipped_tombstoned_run", run_id=str(run_uuid)
                )
                return
            if run is None:
                # First drain for this run — create with status=IN_PROGRESS.
                run = TestRun(
                    id=run_uuid,
                    project_id=proj_uuid,
                    build_number=build_number or state.get("build_number") or run_id[:8],
                    trigger_source="live_stream",
                    ingestion_source="live",
                    status=LaunchStatus.IN_PROGRESS,
                    total_tests=agg_total,
                    passed_tests=agg_passed,
                    failed_tests=agg_failed,
                    skipped_tests=agg_skipped,
                    broken_tests=agg_broken,
                    unknown_tests=agg_unknown,
                    primary_suite_name=session_suite,
                    suite_names=[session_suite] if session_suite else None,
                    start_time=started_at,
                    end_time=now,
                )
                db.add(run)
                await db.flush()
            else:
                # Refresh aggregates from HINCRBY counters. Keep the
                # stored ``status`` — close_session flips it to its
                # terminal value via upsert_test_run.
                run.total_tests = agg_total
                run.passed_tests = agg_passed
                run.failed_tests = agg_failed
                run.skipped_tests = agg_skipped
                run.broken_tests = agg_broken
                run.unknown_tests = agg_unknown
                run.end_time = now
                if session_suite and not run.primary_suite_name:
                    run.primary_suite_name = session_suite
                    run.suite_names = [session_suite]

            rows = [
                _event_to_row(
                    e, run_uuid=run_uuid, default_suite=session_suite,
                )
                for e in events
            ]
            insert_chunk = max(1, settings.PERSIST_LIVE_BULK_INSERT_CHUNK)
            for offset in range(0, len(rows), insert_chunk):
                await db.execute(
                    _sa_insert(TestCase), rows[offset:offset + insert_chunk],
                )

            await db.commit()

        # Drained-and-committed — now LTRIM the head off the buffer.
        # Done AFTER commit so a DB failure mid-write leaves the buffer
        # intact for the next tick to retry. Worst case: a tick succeeds
        # at commit but crashes before LTRIM → next tick re-inserts the
        # same rows. Acceptable: a re-run produces duplicates only on
        # this narrow window, and the existing "buffer empty at close"
        # warning still surfaces if it ever matters.
        drained = len(raw_entries)
        await redis.ltrim(list_key, drained, -1)
        result["drained"] = drained
        logger.info(
            "live_session_drained",
            run_id=run_id, project_id=project_id,
            drained=drained, agg_total=agg_total,
        )
    finally:
        await _release_lock(redis, run_id)

    return result


async def drain_all_active_runs(
    *,
    max_events_per_run: Optional[int] = None,
) -> dict:
    """Walk every active live run (per ``RedisLiveRunState.get_all_active``)
    and drain its buffer. One run per loop iteration so a slow run can't
    starve the others.

    Used by the ``drain-active-live-sessions`` Celery beat task.
    """
    if not settings.LIVE_SESSION_DRAIN_ENABLED:
        return {"runs_scanned": 0, "drained": 0, "errors": 0, "disabled": True}

    totals = {"runs_scanned": 0, "drained": 0, "errors": 0}
    states = await RedisLiveRunState.get_all_active()
    for state in states:
        run_id = state.get("run_id")
        project_id = state.get("project_id")
        if not run_id or not project_id:
            continue
        totals["runs_scanned"] += 1
        try:
            outcome = await drain_run_buffer(
                run_id=run_id,
                project_id=project_id,
                build_number=state.get("build_number") or "",
                suite_name=state.get("suite_name"),
                max_events=max_events_per_run,
            )
            totals["drained"] += int(outcome.get("drained", 0))
        except Exception as exc:
            totals["errors"] += 1
            logger.warning(
                "drain_run_failed",
                run_id=run_id, project_id=project_id, error=str(exc),
            )
    return totals
