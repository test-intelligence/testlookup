"""Auto-recovery for completed live-stream runs missing per-test rows.

Belt-and-braces for the close_session → persist_live_session handoff.
When that handoff fails — silently swallowed apply_async, queue
backpressure, worker restart during dispatch — the TestRun row carries
final aggregates (``total_tests``, ``passed_tests``, ``failed_tests``)
but ``test_cases`` is empty. The hourly ``backfill_placeholder_test_cases``
task eventually inserts marker rows (``[ingestion gap …] #1``) but the
*real* per-test detail is still recoverable from ``TestRun.event_archive``
(written by ``close_session`` before the worker handoff) for 15 days.

This service finds those runs and re-queues ``persist_live_session``
with the archived events staged back into Redis — the same code path
the ``POST /api/v1/runs/{id}/recover-live`` endpoint uses. Runs hourly
out of band of the placeholder backfill so users see real names within
a minute of close_session firing, not "[ingestion gap …]" rows.

Idempotent: candidates are filtered by ``COUNT(test_cases) = 0``, and
a second run after a successful first one finds zero rows to recover.
"""
from __future__ import annotations

import hashlib
import json as _json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis_client import get_redis
from app.models.postgres import LiveSession, TestCase, TestRun
from app.streams import (
    LIVE_BATCH_DEDUP_KEY,
    LIVE_EVIDENCE_STREAM_KEY,
    LIVE_TESTCASES_KEY,
)
from app.services.ingestion_sanitization import sanitize_test_result_payload

logger = structlog.get_logger(__name__)

_RECOVERY_DEDUPE_TTL_SECONDS = 15 * 24 * 60 * 60

_RECOVERY_BATCH_LUA = r"""
local expected_types = {
  'stream', 'hash', 'set', 'set', 'set', 'set', 'set', 'set', 'set'
}
for i = 1, 9 do
  local actual = redis.call('TYPE', KEYS[i])['ok']
  if actual ~= 'none' and actual ~= expected_types[i] then
    return {'wrongtype', KEYS[i]}
  end
end

local event_count = tonumber(ARGV[3])
local capacity = tonumber(ARGV[10])
local ok_events, events = pcall(cjson.decode, ARGV[7])
local ok_ids, event_ids = pcall(cjson.decode, ARGV[8])
if not event_count or event_count < 1 or not capacity
   or not ok_events or not ok_ids
   or type(events) ~= 'table' or type(event_ids) ~= 'table'
   or #events ~= event_count or #event_ids ~= event_count then
  return {'invalid', '0'}
end

local dedupe_field = ARGV[1]
local existing = redis.call('HGET', KEYS[2], dedupe_field)
local stream_id = nil
local recovering = false
if existing then
  local ok, receipt = pcall(cjson.decode, existing)
  if not ok then return {'corrupt', '0'} end
  if receipt['digest'] ~= ARGV[2]
     or tonumber(receipt['count']) ~= event_count then
    return {'collision', tostring(receipt['count'] or '0')}
  end
  if receipt['state'] == 'accepted' or receipt['state'] == 'projected' then
    redis.call('HSET', KEYS[2], '__gate__', 'closed')
    if receipt['state'] == 'projected' and redis.call('SCARD', KEYS[3]) == 0 then
      for i = 2, 9 do redis.call('EXPIRE', KEYS[i], ARGV[9]) end
    end
    return {'duplicate', tostring(receipt['count']), tostring(receipt['stream_id'])}
  end
  if receipt['state'] ~= 'staged'
     or redis.call('HGET', KEYS[2], '__admitting__') ~= dedupe_field then
    return {'corrupt', '0'}
  end
  stream_id = tostring(receipt['stream_id'])
  recovering = true
end

if not recovering then
  if redis.call('HEXISTS', KEYS[2], '__admitting__') == 1 then
    return {'busy', '0'}
  end
  local pending_total = redis.call('SCARD', KEYS[3])
  if capacity > 0 and pending_total + event_count > capacity then
    return {'capacity', tostring(pending_total)}
  end

  local now = redis.call('TIME')
  local milliseconds = tonumber(now[1]) * 1000
    + math.floor(tonumber(now[2]) / 1000)
  local sequence = 0
  local tail = redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)
  if #tail == 1 then
    local last_ms, last_sequence = string.match(tail[1][1], '^(%d+)%-(%d+)$')
    last_ms = tonumber(last_ms)
    last_sequence = tonumber(last_sequence)
    if milliseconds < last_ms then milliseconds = last_ms end
    if milliseconds == last_ms then sequence = last_sequence + 1 end
  end
  stream_id = tostring(milliseconds) .. '-' .. tostring(sequence)
  local staged = cjson.encode({
    digest = ARGV[2], count = event_count,
    state = 'staged', stream_id = stream_id
  })
  redis.call(
    'HSET', KEYS[2], dedupe_field, staged,
    '__admitting__', dedupe_field, '__gate__', 'closed'
  )
end

local exact = redis.call('XRANGE', KEYS[1], stream_id, stream_id, 'COUNT', 1)
if #exact == 0 then
  redis.call(
    'XADD', KEYS[1], stream_id,
    'batch_id', ARGV[4],
    'batch_digest', ARGV[2],
    'event_count', ARGV[3],
    'session_id', ARGV[5],
    'run_id', ARGV[6],
    'events_json', ARGV[7],
    'event_ids_json', ARGV[8],
    'trim_legacy', '0'
  )
end

for i = 1, #event_ids do
  local event_id = event_ids[i]
  local event = events[i]
  local event_type = tostring(event['event_type'] or 'test_result')
  redis.call('SADD', KEYS[3], event_id)
  if event_type ~= 'live_heartbeat' then
    redis.call('SADD', KEYS[9], event_id)
  end
  if event_type == 'test_result' then
    local outcome = string.upper(tostring(event['status'] or 'UNKNOWN'))
    if outcome == 'PASSED' then redis.call('SADD', KEYS[4], event_id)
    elseif outcome == 'FAILED' then redis.call('SADD', KEYS[5], event_id)
    elseif outcome == 'SKIPPED' then redis.call('SADD', KEYS[6], event_id)
    elseif outcome == 'BROKEN' then redis.call('SADD', KEYS[7], event_id)
    else redis.call('SADD', KEYS[8], event_id) end
  end
end

redis.call('HSET', KEYS[2], dedupe_field, cjson.encode({
  digest = ARGV[2], count = event_count,
  state = 'accepted', stream_id = stream_id
}), '__gate__', 'closed')
redis.call('HDEL', KEYS[2], '__admitting__')
return {'accepted', ARGV[3], stream_id}
"""


def _redis_field(fields: dict, name: str, default=None):
    return fields.get(name, fields.get(name.encode(), default))


async def buffered_live_evidence_counts(
    redis,
    run_id_str: str,
) -> tuple[int, int, str | None]:
    """Return ``(events, batches, source)`` for recoverable Redis evidence."""
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id_str)
    stream_batches = int(await redis.xlen(stream_key) or 0)
    if stream_batches:
        from app.core.config import settings

        record_cap = max(1, int(settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN or 50_000))
        if stream_batches > record_cap:
            raise RuntimeError("live evidence stream exceeds its configured capacity")
        rows = await redis.xrange(
            stream_key,
            min="-",
            max="+",
            count=stream_batches,
        )
        event_count = 0
        for _stream_id, fields in rows:
            raw_count = _redis_field(fields, "event_count")
            if raw_count is None:
                # Rolling-upgrade one-entry-per-event stream shape.
                event_count += 1
                continue
            try:
                event_count += max(0, int(raw_count))
            except (TypeError, ValueError):
                raise RuntimeError("live evidence manifest has invalid event_count") from None
        return event_count, len(rows), "redis_stream"

    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id_str)
    legacy_events = int(await redis.llen(list_key) or 0)
    if legacy_events:
        return legacy_events, 0, "redis_legacy"
    return 0, 0, None


def _dedupe_field(session_id: str, batch_id: str) -> str:
    return "batch:" + hashlib.sha256(
        f"{session_id}\0{batch_id}".encode("utf-8")
    ).hexdigest()


async def _stage_archive_to_redis(run_id_str: str, events: list[dict]) -> int:
    """Append a historical archive to the durable per-run evidence Stream.

    An empty Stream created by the persistence consumer group must not hide
    recovery data in the legacy LIST.  Stable batch/event identities also let
    PostgreSQL's receipt and test-case uniqueness constraints converge safely
    if a recovery dispatch is retried.
    """
    from app.core.config import settings

    redis = get_redis()
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id_str)
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id_str)
    safe_events = [sanitize_test_result_payload(event) for event in events]
    canonical = _json.dumps(
        safe_events,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    session_id = "archive-recovery-" + hashlib.sha256(
        run_id_str.encode("utf-8")
    ).hexdigest()
    batch_id = "archive-" + hashlib.sha256(
        f"{session_id}\0{run_id_str}\0{canonical}".encode("utf-8")
    ).hexdigest()
    event_ids = []
    for index in range(len(safe_events)):
        event_ids.append(hashlib.sha256(
            f"{session_id}\0{batch_id}\0{index}".encode("utf-8")
        ).hexdigest())
    events_json = _json.dumps(
        [{**event, "run_id": run_id_str} for event in safe_events],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    batch_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    dedupe_field = _dedupe_field(session_id, batch_id)
    response = await redis.eval(
        _RECOVERY_BATCH_LUA,
        9,
        stream_key,
        dedupe_key,
        f"{dedupe_key}:pending",
        f"{dedupe_key}:passed",
        f"{dedupe_key}:failed",
        f"{dedupe_key}:skipped",
        f"{dedupe_key}:broken",
        f"{dedupe_key}:unknown",
        f"{dedupe_key}:received",
        dedupe_field,
        batch_digest,
        str(len(safe_events)),
        batch_id,
        session_id,
        run_id_str,
        events_json,
        _json.dumps(event_ids, separators=(",", ":"), ensure_ascii=False),
        str(_RECOVERY_DEDUPE_TTL_SECONDS),
        str(max(0, int(settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN or 0))),
    )
    outcome = response[0].decode() if isinstance(response[0], bytes) else response[0]
    if outcome not in {"accepted", "duplicate"}:
        raise RuntimeError(f"archive recovery batch admission failed: {outcome}")
    return len(safe_events)


async def auto_recover_completed_runs(
    db: AsyncSession,
    *,
    lookback_hours: int = 24,
    max_runs: int = 100,
) -> dict:
    """Re-enqueue persistence for completed live_stream runs whose
    ``test_cases`` table is empty but ``event_archive`` is still
    populated and within the 15-day archive window.

    Caller owns the transaction. The function does not commit; it
    only stages Redis state and dispatches Celery tasks. The candidate
    SELECT is read-only.

    Returns ``{candidates: int, recovered: int, errors: int}`` for
    observability.
    """
    # Local imports keep the service free of the Celery import cycle and
    # let tests stub them cleanly.
    from app.worker.ingestion_routing import queue_for_project
    from app.worker.tasks import persist_live_session

    counts = {"candidates": 0, "recovered": 0, "errors": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    archive_cutoff = datetime.now(timezone.utc) - timedelta(days=15)

    candidates_stmt = (
        select(TestRun)
        .where(
            TestRun.trigger_source == "live_stream",
            TestRun.event_archive_at.is_not(None),
            TestRun.event_archive_at >= archive_cutoff,
            TestRun.updated_at >= cutoff,
        )
        .where(
            ~select(TestCase.id)
            .where(TestCase.test_run_id == TestRun.id)
            .exists()
        )
        .order_by(TestRun.updated_at.desc())
        .limit(max_runs)
    )

    candidates = list((await db.execute(candidates_stmt)).scalars().all())
    counts["candidates"] = len(candidates)

    redis = get_redis()
    for run in candidates:
        archive = list(run.event_archive or [])
        if not archive:
            # Archive column present but empty — no real names to recover.
            # Leave the run to the placeholder backfill.
            continue

        try:
            run_id_str = str(run.id)
            # Prefer the durable Stream, then the legacy rolling-upgrade LIST.
            # An existing empty Stream/group is deliberately treated as empty
            # so archive staging still reaches the consumer.
            event_count, batch_count, source = await buffered_live_evidence_counts(
                redis,
                run_id_str,
            )
            if event_count > 0:
                staged = event_count
            else:
                staged = await _stage_archive_to_redis(run_id_str, archive)
                batch_count = 1 if staged else 0
                source = "archive"

            persist_live_session.apply_async(
                kwargs={
                    "run_id": run_id_str,
                    "project_id": str(run.project_id),
                    "build_number": run.build_number or run_id_str,
                    "branch": run.branch or "",
                    "commit_hash": run.commit_hash or "",
                    "final_state": {
                        "passed": run.passed_tests or 0,
                        "failed": run.failed_tests or 0,
                        "skipped": run.skipped_tests or 0,
                        "broken": run.broken_tests or 0,
                        "unknown": getattr(run, "unknown_tests", 0) or 0,
                        "total": run.total_tests or 0,
                    },
                    "suite_name": run.primary_suite_name or None,
                },
                queue=queue_for_project(str(run.project_id)),
                priority=7,
            )
            counts["recovered"] += 1
            logger.info(
                "auto_recover_queued",
                run_id=run_id_str,
                project_id=str(run.project_id),
                events=staged,
                batches=batch_count,
                source=source,
            )
        except Exception as exc:
            counts["errors"] += 1
            logger.warning(
                "auto_recover_failed",
                run_id=str(run.id),
                error=str(exc),
            )

    if counts["candidates"] or counts["recovered"]:
        logger.info("auto_recover_sweep_done", **counts)
    return counts


async def repair_clobbered_primary_suite_names(
    db: AsyncSession,
    *,
    lookback_hours: int = 24,
    max_runs: int = 200,
) -> dict:
    """Reset ``TestRun.primary_suite_name`` to the SDK-supplied
    ``LiveSession.suite_name`` for any live_stream run where the two
    disagree.

    Before ingestion.py's ``_update_run_aggregates`` learned to respect
    a session-supplied ``primary_suite_name``, finalize_run recomputed
    it from the dominant per-event ``suite_name`` — which, combined
    with the TestNG-listener bug that stamped per-event suite_name to
    the test class name, made the run show up under
    ``com.example.OrderApiRegressionTests`` instead of the user's
    chosen "API Regression Multi-Class" label. This sweep heals
    affected rows by re-stamping ``primary_suite_name`` from the
    authoritative ``LiveSession.suite_name``. Idempotent: when the
    two already match, the row is left alone.

    Caller owns the transaction.

    Returns ``{candidates: int, repaired: int}`` for observability.
    """
    counts = {"candidates": 0, "repaired": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # LiveSession.id is the internal run identity and equals TestRun.id.
    # LiveSession.run_id is only the client-supplied display slug and can be
    # non-UUID or reused by another project, so it must never drive this join.
    candidates_stmt = (
        select(
            TestRun.id,
            TestRun.primary_suite_name,
            LiveSession.suite_name,
        )
        .join(LiveSession, LiveSession.id == TestRun.id)
        .where(
            TestRun.trigger_source == "live_stream",
            TestRun.updated_at >= cutoff,
            LiveSession.suite_name.is_not(None),
            LiveSession.suite_name != "",
        )
        .limit(max_runs)
    )

    try:
        rows = list((await db.execute(candidates_stmt)).all())
    except Exception as exc:
        # Cast may differ across dialects (sqlite vs postgres); never
        # fail the beat task here — log and bail.
        logger.warning("repair_primary_suite_join_failed", error=str(exc))
        return counts

    counts["candidates"] = len(rows)

    for run_id, current_psn, session_suite in rows:
        current = (current_psn or "").strip() or None
        target = (session_suite or "").strip() or None
        if not target or current == target:
            continue
        try:
            await db.execute(
                update(TestRun)
                .where(TestRun.id == run_id)
                .values(primary_suite_name=target)
            )
            counts["repaired"] += 1
            logger.info(
                "primary_suite_repaired",
                run_id=str(run_id), from_=current, to=target,
            )
        except Exception as exc:
            logger.warning(
                "primary_suite_repair_failed",
                run_id=str(run_id), error=str(exc),
            )

    if counts["repaired"]:
        logger.info("primary_suite_repair_sweep_done", **counts)
    return counts
