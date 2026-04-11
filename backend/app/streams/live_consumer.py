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
from typing import Any, cast
import socket
import time

from app.db.redis_client import get_redis
from app.streams import (
    CONSUMER_BATCH_SIZE,
    CONSUMER_BLOCK_MS,
    DLQ_STREAM,
    LIVE_EVENTS_STREAM,
    LIVE_GROUP,
    LIVE_TESTCASES_KEY,
    MAX_DELIVERY_ATTEMPTS,
    STALE_CLAIM_INTERVAL_S,
    STALE_IDLE_MS,
)
from app.streams.live_run_state import RedisLiveRunState

logger = logging.getLogger("streams.live_consumer")

# Unique consumer name per process (handles multiple uvicorn workers)
_CONSUMER_NAME = f"{socket.gethostname()}:{__import__('os').getpid()}"


class LiveEventStreamConsumer:
    """
    Async consumer for the live events Redis Stream.
    Start with `asyncio.create_task(consumer.run())` in the FastAPI lifespan.
    """

    def __init__(self):
        self._running = False
        self._last_reclaim = 0.0
        # Per-message retry counts: {msg_id: attempt_count}
        self._retry_counts: dict[str, int] = {}

    async def run(self) -> None:
        """Main consumer loop. Runs until cancelled."""
        self._running = True
        logger.info("Live stream consumer starting (consumer=%s)", _CONSUMER_NAME)

        await self._ensure_group()

        while self._running:
            try:
                # Periodically reclaim stale messages from crashed consumers
                if time.time() - self._last_reclaim > STALE_CLAIM_INTERVAL_S:
                    await self._reclaim_stale()
                    self._last_reclaim = time.time()

                # Read a batch of new messages
                messages = await self._read_new()
                for msg_id, data in messages:
                    await self._handle_message(msg_id, data)

            except asyncio.CancelledError:
                logger.info("Live stream consumer shutting down")
                self._running = False
                break
            except Exception as exc:
                logger.error("Live stream consumer error: %s — retrying in 5s", exc)
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    # ── Message dispatch ──────────────────────────────────────────────────────

    async def _handle_message(self, msg_id: str, raw: dict) -> None:
        """Process one message, ACK on success, or DLQ after max retries."""
        try:
            payload = json.loads(raw.get("payload", "{}"))
            run_id = raw.get("run_id", payload.get("run_id", ""))
            event_type = raw.get("event_type", payload.get("type", "test_result"))

            await self._process(run_id, event_type, payload)
            await self._ack(msg_id)
            self._retry_counts.pop(msg_id, None)

        except Exception as exc:
            attempt = self._retry_counts.get(msg_id, 0) + 1
            self._retry_counts[msg_id] = attempt
            logger.warning(
                "Failed to process live event msg=%s attempt=%d error=%s",
                msg_id, attempt, exc,
            )
            if attempt >= MAX_DELIVERY_ATTEMPTS:
                await self._move_to_dlq(msg_id, raw, str(exc), attempt)
                await self._ack(msg_id)
                self._retry_counts.pop(msg_id, None)
            # If under max retries: leave in pending list for XAUTOCLAIM to re-deliver

    async def _process(self, run_id: str, event_type: str, payload: dict) -> None:
        """Dispatch to the appropriate handler based on event type."""
        if event_type == "run_start":
            await self._on_run_start(run_id, payload)
        elif event_type == "test_result":
            await self._on_test_result(run_id, payload)
        elif event_type == "run_complete":
            await self._on_run_complete(run_id, payload)
        else:
            logger.debug("Unknown live event type=%s run=%s — ignoring", event_type, run_id)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_run_start(self, run_id: str, payload: dict) -> None:
        project_id  = payload.get("project_id", "")
        build_number = payload.get("build_number", run_id)

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

    async def _ensure_group(self) -> None:
        """Create the consumer group if it doesn't exist."""
        redis = get_redis()
        try:
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

    async def _reclaim_stale(self) -> None:
        """Re-claim messages idle > STALE_IDLE_MS from any consumer (including crashed ones)."""
        redis = get_redis()
        try:
            # xautoclaim returns (next_id, [(msg_id, {data}), ...], [deleted_ids])
            result = await redis.xautoclaim(
                LIVE_EVENTS_STREAM,
                LIVE_GROUP,
                _CONSUMER_NAME,
                min_idle_time=STALE_IDLE_MS,
                start_id="0-0",
                count=CONSUMER_BATCH_SIZE,
            )
            claimed = result[1] if result and len(result) > 1 else []
            if claimed:
                logger.info("Reclaimed %d stale live event messages", len(claimed))
                for msg_id, data in claimed:
                    await self._handle_message(msg_id, data)
        except Exception as exc:
            logger.debug("xautoclaim error (non-critical): %s", exc)

    async def _ack(self, msg_id: str) -> None:
        redis = get_redis()
        await redis.xack(LIVE_EVENTS_STREAM, LIVE_GROUP, msg_id)

    async def _move_to_dlq(
        self, msg_id: str, data: dict, error: str, attempt: int
    ) -> None:
        """Publish an unprocessable message to the dead-letter queue."""
        redis = get_redis()
        await redis.xadd(
            DLQ_STREAM,
            {
                "source_stream":  LIVE_EVENTS_STREAM,
                "original_msg_id": msg_id,
                "original_data":  json.dumps(data),
                "error":          error[:500],
                "attempt_count":  str(attempt),
            },
            maxlen=5000,
            approximate=True,
        )
        logger.error(
            "Moved live event to DLQ: msg=%s attempts=%d error=%s", msg_id, attempt, error
        )


    # ── Test event buffer ────────────────────────────────────────────────────

    async def _buffer_test_event(self, run_id: str, payload: dict) -> None:
        """Append a minimal test-result snapshot to the per-run Redis List.
        Used by persist_live_session to recreate TestCase rows after run completion.
        List TTL: 25 h — cleaned up by the Celery task on success.
        """
        try:
            redis = get_redis()
            entry = json.dumps({
                "test_name":     payload.get("test_name", ""),
                "status":        payload.get("status", "UNKNOWN"),
                "duration_ms":   payload.get("duration_ms", 0),
                "suite_name":    payload.get("suite_name"),
                "class_name":    payload.get("class_name"),
                "error_message": payload.get("error_message"),
                "stack_trace":   payload.get("stack_trace"),
                "tags":          payload.get("tags"),
                "timestamp_ms":  payload.get("timestamp_ms"),
            })
            key = LIVE_TESTCASES_KEY.format(run_id=run_id)
            await redis.rpush(key, entry)  # type: ignore[misc]
            await redis.expire(key, 90_000)   # 25 h
        except Exception as exc:
            logger.debug("Failed to buffer test event for run %s: %s", run_id, exc)


# ── Module-level helpers ──────────────────────────────────────────────────────

async def _broadcast(project_id: str, payload: dict) -> None:
    # Fan-out to WebSocket dashboard clients
    try:
        from app.routers.live import manager
        await manager.broadcast(project_id, payload)
    except Exception as exc:
        logger.debug("WebSocket broadcast skipped: %s", exc)

    # Fan-out to SSE dashboard clients (same payload, different transport)
    try:
        from app.routers.stream import push_to_sse
        await push_to_sse(project_id, payload)
    except Exception as exc:
        logger.debug("SSE broadcast skipped: %s", exc)


async def _queue_live_analysis(
    test_case_id: str,
    test_name: str,
    run_id: str,
    project_id: str,
) -> None:
    """Queue immediate root-cause analysis for a failing test during live execution."""
    try:
        from app.streams.circuit_breaker import LLMCircuitBreaker
        if not await LLMCircuitBreaker.is_available():
            logger.debug("Circuit open — skipping live analysis for %s", test_name)
            return

        from app.worker.tasks import run_live_test_analysis
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
                total = int(state.get("total", 0)) or (passed + failed + skipped + broken)

                run.status = LaunchStatus.FAILED if (failed + broken) > 0 else LaunchStatus.PASSED
                run.total_tests = total
                run.passed_tests = passed
                run.failed_tests = failed
                run.skipped_tests = skipped
                run.broken_tests = broken
                run.pass_rate = round(passed / (passed + failed + broken) * 100, 2) if (passed + failed + broken) > 0 else None
                run.end_time = datetime.now(timezone.utc)
                await db.commit()
    except Exception as exc:
        logger.error("Failed to finalise run %s in DB: %s", run_id, exc)
