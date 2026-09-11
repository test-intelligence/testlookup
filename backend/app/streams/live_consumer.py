"""
Live Event Stream Consumer — asyncio background task.

Reads from the `testlookup:stream:live_events` Redis Stream using a consumer group.
Runs inside the FastAPI process so it can directly access the WebSocket manager.

Fault-tolerance mechanisms:
  1. Consumer group ACK model — events only removed after explicit acknowledgement
  2. XAUTOCLAIM — reclaims events idle > STALE_IDLE_MS from any crashed consumer
  3. Retry counter per message — after MAX_DELIVERY_ATTEMPTS, moves to DLQ
  4. Graceful shutdown — drains in-flight messages on SIGTERM
  5. Redis reconnect — on connection loss, waits 5s then retries the loop

Architecture:
  POST /ws/events/{run_id}
       │  (thin producer, 202 in ~1ms)
       ▼
  Redis Stream: testlookup:stream:live_events
       │
       ▼  (this consumer, runs in FastAPI lifespan)
  LiveEventStreamConsumer.process_message()
       ├─ run_start   → RedisLiveRunState.start()  → WebSocket broadcast
       ├─ test_result → RedisLiveRunState.record_test_event()
       │                → WebSocket broadcast
       │                → If FAILED: queue immediate AI analysis via Celery
       └─ run_complete → RedisLiveRunState.complete() → WebSocket broadcast
                       → Trigger offline pipeline (Celery)
"""
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from contextvars import ContextVar
from typing import Any, cast

from app.db.redis_client import get_redis
from app.streams import (
    CONSUMER_BATCH_SIZE,
    CONSUMER_BLOCK_MS,
    DLQ_STREAM,
    LIVE_EVENTS_STREAM,
    LIVE_GROUP,
    MAX_DELIVERY_ATTEMPTS,
    LIVE_PROCESSOR_LEADER_KEY,
    STALE_CLAIM_INTERVAL_S,
    STALE_IDLE_MS,
)
from app.streams.live_run_state import RedisLiveRunState
from app.services.ingestion_sanitization import sanitize_test_result_payload

logger = logging.getLogger("streams.live_consumer")

# Unique consumer name per process (handles multiple uvicorn workers)
_CONSUMER_NAME = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
_SOURCE_EVENT: ContextVar[tuple[str, int] | None] = ContextVar("live_source_event", default=None)
_LEADER_TTL_SECONDS = 30
_RENEW_LEADER_SCRIPT = """
local owner = redis.call('GET', KEYS[1])
if owner == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
if not owner and redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
  return 2
end
return 0
"""


def _group_name(group: Any) -> str:
    """Name out of an XINFO GROUPS entry.

    The project configures ``decode_responses=True`` so this is already a str,
    but a bytes name would silently never match and put us straight back to
    creating a group that already exists.
    """
    name = group.get("name") if isinstance(group, dict) else None
    return name.decode() if isinstance(name, bytes) else str(name)


class LiveEventStreamConsumer:
    """
    Async consumer for the live events Redis Stream.
    Start with `asyncio.create_task(consumer.run())` in the FastAPI lifespan.
    """

    def __init__(self):
        self._running = False
        self._last_reclaim = 0.0
        self._just_acquired_leadership = False
        # Every process incarnation must drain the PEL on its first leadership
        # turn, even if a stale lease from a restarted host/PID appears owned.
        self._must_drain_pending = True
        self._leader_heartbeat: asyncio.Task | None = None

    async def run(self) -> None:
        """Main consumer loop. Runs until cancelled."""
        self._running = True
        logger.info("Live stream consumer starting (consumer=%s)", _CONSUMER_NAME)

        await self._ensure_group()

        while self._running:
            try:
                if not await self._hold_leadership():
                    await asyncio.sleep(1)
                    continue
                if self._just_acquired_leadership:
                    # A predecessor may have stopped with an older entry in
                    # the PEL. Recover it before accepting newer `>` entries.
                    self._must_drain_pending = True
                if self._must_drain_pending:
                    drained = await self._reclaim_stale(
                        min_idle_time=0, drain_all=True,
                    )
                    self._must_drain_pending = not drained
                    if not drained:
                        await asyncio.sleep(1)
                        continue
                # Periodically reclaim stale messages from crashed consumers
                if time.time() - self._last_reclaim > STALE_CLAIM_INTERVAL_S:
                    if not await self._reclaim_stale():
                        self._must_drain_pending = True
                        continue
                    self._last_reclaim = time.time()

                # Read a batch of new messages
                if not await self._hold_leadership():
                    self._must_drain_pending = True
                    continue
                messages = await self._read_new()
                for msg_id, data in messages:
                    if not await self._hold_leadership():
                        self._must_drain_pending = True
                        break
                    if not await self._handle_message(msg_id, data):
                        self._must_drain_pending = True
                        break

            except asyncio.CancelledError:
                logger.info("Live stream consumer shutting down")
                self._running = False
                break
            except Exception as exc:
                logger.error("Live stream consumer error: %s — retrying in 5s", exc)
                await asyncio.sleep(5)
        await self._release_leadership()

    def stop(self) -> None:
        self._running = False

    async def _hold_leadership(self) -> bool:
        """Keep one sequential source projector active across the API fleet."""
        result = int(await get_redis().eval(
            _RENEW_LEADER_SCRIPT,
            1,
            LIVE_PROCESSOR_LEADER_KEY,
            _CONSUMER_NAME,
            _LEADER_TTL_SECONDS,
        ))
        self._just_acquired_leadership = result == 2
        if result and (self._leader_heartbeat is None or self._leader_heartbeat.done()):
            self._leader_heartbeat = asyncio.create_task(
                self._renew_leadership(), name="live-processor-leader-heartbeat",
            )
        return result != 0

    async def _renew_leadership(self) -> None:
        """Renew independently so a slow batch cannot expire the owner lease."""
        while self._running:
            await asyncio.sleep(_LEADER_TTL_SECONDS / 3)
            result = await get_redis().eval(
                _RENEW_LEADER_SCRIPT,
                1,
                LIVE_PROCESSOR_LEADER_KEY,
                _CONSUMER_NAME,
                _LEADER_TTL_SECONDS,
            )
            if int(result) == 0:
                return

    async def _release_leadership(self) -> None:
        """Release only this process's lease during graceful shutdown."""
        try:
            if self._leader_heartbeat is not None:
                self._leader_heartbeat.cancel()
                await asyncio.gather(self._leader_heartbeat, return_exceptions=True)
            await get_redis().eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) else return 0 end",
                1,
                LIVE_PROCESSOR_LEADER_KEY,
                _CONSUMER_NAME,
            )
        except Exception as exc:
            logger.debug("Live processor leader release skipped: %s", exc)

    # ── Message dispatch ──────────────────────────────────────────────────────

    async def _handle_message(self, msg_id: str, raw: dict) -> bool:
        """Process one message; return whether it was ACKed or terminally DLQed."""
        try:
            payload = json.loads(raw.get("payload", "{}"))
            run_id = raw.get("run_id", payload.get("run_id", ""))
            event_type = raw.get("event_type", payload.get("type", "test_result"))

            token = _SOURCE_EVENT.set((msg_id, 0))
            try:
                await self._process(run_id, event_type, payload)
            finally:
                _SOURCE_EVENT.reset(token)
            await self._ack(msg_id)
            return True

        except Exception as exc:
            # Attempt count comes from the stream's own delivery counter
            # (XPENDING ``times_delivered``), NOT a per-process dict. The
            # dict was lost on restart and not shared across uvicorn workers,
            # so a message reclaimed by a different consumer restarted its
            # count at 0 and MAX_DELIVERY_ATTEMPTS was never enforced
            # consistently across the fleet. Redis increments times_delivered
            # on the initial XREADGROUP and on every XAUTOCLAIM reclaim.
            attempt = await self._delivery_count(msg_id)
            logger.warning(
                "Failed to process live event msg=%s attempt=%d error=%s",
                msg_id, attempt, exc,
            )
            if attempt >= MAX_DELIVERY_ATTEMPTS:
                await self._move_to_dlq(msg_id, raw, str(exc), attempt)
                await self._ack(msg_id)
                return True
            # If under max attempts: leave in pending list for XAUTOCLAIM to re-deliver
            return False

    async def _process(self, run_id: str, event_type: str, payload: dict) -> None:
        """Dispatch to the appropriate handler based on event type."""
        if event_type == "run_start":
            await self._on_run_start(run_id, payload)
        elif event_type == "test_result":
            await self._on_test_result(run_id, payload)
        elif event_type == "run_complete":
            await self._on_run_complete(run_id, payload)
        elif event_type == "live_heartbeat":
            # No-op — the synchronous ingest handler already refreshed the
            # Redis ``last_event_at`` field before the message hit this
            # stream, which is the only thing the reaper cares about. We
            # also intentionally do NOT broadcast heartbeats to the
            # WebSocket so the page's event feed stays signal-only.
            return
        else:
            logger.debug("Unknown live event type=%s run=%s — ignoring", event_type, run_id)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_run_start(self, run_id: str, payload: dict) -> None:
        project_id  = payload.get("project_id", "")
        build_number = payload.get("build_number")

        if not project_id or not build_number:
            # A run_start admitted through the SDK stream's path -- its own
            # ingest, or /ws/events since re-audit N14 -- carries neither a
            # project nor a build in its payload: the session that admitted it
            # already started the run's live state with both. Read them from
            # there, rather than fail the event three times and dead-letter it,
            # or announce the session's UUID as the build (code review of N14).
            state = await RedisLiveRunState.get(run_id) or {}
            project_id = project_id or state.get("project_id", "")
            build_number = build_number or state.get("build_number")
        build_number = build_number or run_id
        if not project_id:
            raise ValueError("run_start event missing project_id")

        await RedisLiveRunState.start(run_id, project_id, build_number)
        await _broadcast(project_id, {
            "type": "live_run_started",
            "run_id": run_id,
            "build_number": build_number,
        })
        logger.info("Live run started: %s build=%s", run_id, build_number)

    async def _on_test_result(self, run_id: str, payload: dict) -> None:
        status     = payload.get("status", "UNKNOWN").upper()
        test_name  = payload.get("test_name", "")

        # Counter increments (HINCRBY) and event buffering are done synchronously
        # in stream_service.ingest_event_batch() to keep the live state immediately
        # accurate.  Here we only read the already-updated state and broadcast it.
        state = await RedisLiveRunState.get(run_id)
        if state is None:
            return  # Unknown run — ignore

        # Broadcast incremental update
        await _broadcast(state["project_id"], {
            "type": "live_test_result",
            **state,
            "last_test": test_name,
            "last_status": status,
        })

        # Early-warning broadcast
        if RedisLiveRunState.should_warn(state):
            await _broadcast(state["project_id"], {
                "type": "live_warning",
                "run_id": run_id,
                "message": f"High failure rate: {100 - state['pass_rate']:.0f}% of tests failing",
            })

        # Immediate AI analysis for failing tests (fire-and-forget Celery task)
        if status in ("FAILED", "BROKEN"):
            test_case_id = payload.get("test_case_id")
            if test_case_id:
                await _queue_live_analysis(
                    test_case_id=test_case_id,
                    test_name=test_name,
                    run_id=run_id,
                    project_id=state["project_id"],
                    logical_event_id=(
                        f"live-analysis:{_SOURCE_EVENT.get()[0]}:{test_case_id}"
                        if _SOURCE_EVENT.get() is not None else None
                    ),
                )

    async def _on_run_complete(self, run_id: str, payload: dict) -> None:
        state = await RedisLiveRunState.complete(run_id)
        if state is None:
            logger.warning("run_complete for unknown run %s", run_id)
            return

        # Persist final status to PostgreSQL
        await _finalise_run_in_db(run_id, state)

        await _broadcast(state["project_id"], {
            "type": "live_run_complete",
            "run_id": run_id,
            **state,
        })

        # NOTE: The full AI analysis pipeline is triggered by close_session()
        # in stream_service.py (workflow_type="offline", countdown=45s) which
        # waits for persist_live_session to finish creating TestCase rows.
        # No duplicate trigger needed here.

    # ── Stream operations ─────────────────────────────────────────────────────

    async def _delivery_count(self, msg_id: str) -> int:
        """How many times this message has been delivered, per Redis' own
        PEL counter. Shared across all consumers and durable across restarts,
        unlike a per-process dict — so MAX_DELIVERY_ATTEMPTS is enforced
        consistently regardless of which worker handles a reclaimed message.

        Best-effort: on any lookup error, return 1 (treat as first delivery)
        so a transient XPENDING failure can never prematurely DLQ a message.
        """
        redis = get_redis()
        try:
            pending = await redis.xpending_range(
                LIVE_EVENTS_STREAM, LIVE_GROUP,
                min=msg_id, max=msg_id, count=1,
            )
            if pending:
                return int(pending[0]["times_delivered"])
        except Exception as exc:
            logger.debug("xpending_range failed for msg=%s: %s", msg_id, exc)
        return 1

    async def _ensure_group(self) -> None:
        """Create the consumer group if it doesn't exist.

        Deliberately *checks* before creating rather than calling XGROUP CREATE
        and catching BUSYGROUP. The group lives in Redis and survives pod
        restarts, so the catch-it path fired on every boot of every uvicorn
        worker — and the OpenTelemetry Redis instrumentation records the
        exception on the span BEFORE this code swallows it. A condition the
        application treats as entirely normal therefore reached operators as
        four ERROR-level stacktraces per backend pod per deploy.

        That is worth avoiding for its own sake: error logs that are routinely
        wrong train people to stop reading them, and these ones actively cost
        us — a log scan of a completely healthy deployment reported errors.

        BUSYGROUP is still handled below, but now as the genuine race it
        describes (two workers creating concurrently) rather than the expected
        case.
        """
        redis = get_redis()
        try:
            # EXISTS never raises on a missing key; XINFO GROUPS does, so it is
            # only reached once the stream is known to exist.
            if await redis.exists(LIVE_EVENTS_STREAM):
                groups = await redis.xinfo_groups(LIVE_EVENTS_STREAM)
                if any(_group_name(g) == LIVE_GROUP for g in groups):
                    return
            await redis.xgroup_create(
                LIVE_EVENTS_STREAM, LIVE_GROUP, id="0", mkstream=True
            )
            logger.info("Created consumer group %s on %s", LIVE_GROUP, LIVE_EVENTS_STREAM)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.error("Failed to create consumer group: %s", exc)

    async def _read_new(self) -> list[tuple[str, dict]]:
        """Read new messages (not yet delivered to any consumer)."""
        redis = get_redis()
        try:
            result = await redis.xreadgroup(
                groupname=LIVE_GROUP,
                consumername=_CONSUMER_NAME,
                streams={LIVE_EVENTS_STREAM: ">"},
                count=CONSUMER_BATCH_SIZE,
                block=CONSUMER_BLOCK_MS,
                noack=False,
            )
            if not result:
                return []
            # result: [[stream_name, [(msg_id, {field: value}), ...]]]
            return cast(list[tuple[str, dict[Any, Any]]], result[0][1])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("xreadgroup error: %s", exc)
            return []

    async def _reclaim_stale(
        self,
        *,
        min_idle_time: int = STALE_IDLE_MS,
        drain_all: bool = False,
    ) -> bool:
        """Re-claim messages idle > STALE_IDLE_MS from any consumer (including crashed ones)."""
        redis = get_redis()
        try:
            # On leadership transfer, walk every XAUTOCLAIM page before reading
            # `>` so newer source entries cannot overtake an inherited PEL.
            # Multiple full passes let poison entries reach the shared delivery
            # threshold and move to the DLQ instead of blocking ordering forever.
            start_id = "0-0"
            while True:
                # xautoclaim returns
                # (next_id, [(msg_id, {data}), ...], [deleted_ids]).
                result = await redis.xautoclaim(
                    LIVE_EVENTS_STREAM,
                    LIVE_GROUP,
                    _CONSUMER_NAME,
                    min_idle_time=min_idle_time,
                    start_id=start_id,
                    count=CONSUMER_BATCH_SIZE,
                )
                raw_next_id = result[0] if result else "0-0"
                next_id = (
                    raw_next_id.decode()
                    if isinstance(raw_next_id, bytes)
                    else str(raw_next_id)
                )
                claimed = result[1] if result and len(result) > 1 else []
                if claimed:
                    logger.info("Reclaimed %d stale live event messages", len(claimed))
                    for msg_id, data in claimed:
                        if not await self._hold_leadership():
                            return False
                        if not await self._handle_message(msg_id, data):
                            return False
                if not drain_all or next_id == "0-0" or next_id == start_id:
                    return True
                start_id = next_id
        except Exception as exc:
            logger.debug("xautoclaim error (non-critical): %s", exc)
            if drain_all:
                # Leadership acquisition must not fall through to `>` after a
                # failed inherited-PEL scan. Let run() back off and retry the
                # drain before it accepts any newer source entries.
                raise
            return False

    async def _ack(self, msg_id: str) -> None:
        redis = get_redis()
        await redis.xack(LIVE_EVENTS_STREAM, LIVE_GROUP, msg_id)

    async def _move_to_dlq(
        self, msg_id: str, data: dict, error: str, attempt: int
    ) -> None:
        """Publish an unprocessable message to the dead-letter queue."""
        redis = get_redis()
        safe_data = sanitize_test_result_payload(data)
        safe_error = sanitize_test_result_payload({"error_message": error})[
            "error_message"
        ]
        await redis.xadd(
            DLQ_STREAM,
            {
                "source_stream":  LIVE_EVENTS_STREAM,
                "original_msg_id": msg_id,
                "original_data":  json.dumps(safe_data),
                "error":          safe_error[:500],
                "attempt_count":  str(attempt),
            },
            maxlen=5000,
            approximate=True,
        )
        logger.error(
            "Moved live event to DLQ: msg=%s attempts=%d error=%s",
            msg_id,
            attempt,
            safe_error,
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

async def _broadcast(project_id: str, payload: dict) -> None:
    # This write is part of processing: if it fails the source message remains
    # pending and is retried instead of being ACKed with its notification lost.
    from app.streams.live_fanout import publish_live_notification

    source = _SOURCE_EVENT.get()
    logical_event_id = None
    if source is not None:
        message_id, ordinal = source
        logical_event_id = f"live-source:{message_id}:{ordinal}"
        _SOURCE_EVENT.set((message_id, ordinal + 1))
    await publish_live_notification(
        project_id, payload, logical_event_id=logical_event_id,
    )


async def _queue_live_analysis(
    test_case_id: str,
    test_name: str,
    run_id: str,
    project_id: str,
    logical_event_id: str | None = None,
) -> None:
    """Queue immediate root-cause analysis for a failing test during live execution."""
    try:
        from app.streams.circuit_breaker import LLMCircuitBreaker
        if not await LLMCircuitBreaker.is_available():
            logger.debug("Circuit open — skipping live analysis for %s", test_name)
            return

        from app.worker.tasks import run_live_test_analysis
        # Publication is deliberately at-least-once. Claiming a dedupe key
        # before apply_async created a crash window that could lose analysis.
        # The task is side-effect free (it returns classification through the
        # Celery result backend), and the stable task id makes retries converge
        # on the same logical result slot.
        run_live_test_analysis.apply_async(
            kwargs={
                "test_case_id": test_case_id,
                "test_name":    test_name,
                "run_id":       run_id,
                "project_id":   project_id,
            },
            queue="critical",
            priority=9,
            countdown=2,   # brief delay so test data is persisted first
            task_id=logical_event_id,
        )
    except Exception as exc:
        logger.debug("Failed to queue live analysis for %s: %s", test_name, exc)


async def _finalise_run_in_db(run_id: str, state: dict) -> None:
    try:
        from sqlalchemy import select
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import LaunchStatus, TestRun

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(TestRun).where(TestRun.id == run_id))
            run = result.scalar_one_or_none()
            if run and run.status == LaunchStatus.IN_PROGRESS:
                from datetime import datetime, timezone

                passed = int(state.get("passed", 0))
                failed = int(state.get("failed", 0))
                skipped = int(state.get("skipped", 0))
                broken = int(state.get("broken", 0))
                unknown = int(state.get("unknown", 0))
                total = int(state.get("total", 0)) or (
                    passed + failed + skipped + broken + unknown
                )

                # Shared grader, not an inline ternary: this path used to grade
                # PASSED whenever failed+broken == 0, so a run whose only
                # non-passing result was uninterpretable went out green.
                from app.services.run_status import terminal_run_status

                run.status = terminal_run_status(
                    passed + failed + broken, failed, broken, unknown
                )
                run.total_tests = total
                run.passed_tests = passed
                run.failed_tests = failed
                run.skipped_tests = skipped
                run.broken_tests = broken
                run.unknown_tests = unknown
                # Pass rate intentionally EXCLUDES skipped from the denominator
                # (passed / passed+failed+broken), consistent with stream_service
                # and ingestion. Skips are neither a pass nor a failure.
                run.pass_rate = round(passed / (passed + failed + broken) * 100, 2) if (passed + failed + broken) > 0 else None
                run.end_time = datetime.now(timezone.utc)
                await db.commit()
    except Exception as exc:
        logger.error("Failed to finalise run %s in DB: %s", run_id, exc)
