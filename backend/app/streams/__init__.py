"""
Redis Streams infrastructure for event-driven, fault-tolerant processing.

Stream names and consumer group constants used across producers and consumers.
"""

# ── Stream names ──────────────────────────────────────────────────────────────
LIVE_EVENTS_STREAM  = "testlookup:stream:live_events"   # live test execution events
LIVE_FANOUT_STREAM  = "testlookup:stream:live_fanout"   # ordered dashboard notifications
LIVE_FANOUT_PROJECT_STREAM_KEY = "testlookup:stream:live_fanout:{project_id}"
LIVE_FANOUT_DEDUP_KEY = "testlookup:stream:live_fanout:dedupe:{event_id}"
LIVE_PROCESSOR_LEADER_KEY = "testlookup:stream:live_processor:leader"
INGESTION_STREAM    = "testlookup:stream:ingestion_jobs" # report upload notifications
ANALYSIS_STREAM     = "testlookup:stream:analysis_tasks" # per-test analysis requests
DLQ_STREAM          = "testlookup:stream:dlq"            # dead-letter queue

# ── Consumer group names ──────────────────────────────────────────────────────
LIVE_GROUP      = "live-processors"
INGESTION_GROUP = "ingestion-processors"
ANALYSIS_GROUP  = "analysis-processors"

# ── Redis key namespaces ──────────────────────────────────────────────────────
LIVE_STATE_KEY  = "testlookup:live:state:{run_id}"   # Hash per active run
LIVE_ACTIVE_SET = "testlookup:live:active"           # Set of active run IDs
DEDUP_KEY       = "testlookup:dedup:{task}:{key}"    # Deduplication locks

# Live session management keys
SESSION_TOKEN_KEY = "testlookup:session:token:{token}"   # token → session_id (TTL: 24h)
SESSION_ACTIVE_KEY = "testlookup:session:active"         # Set of active session IDs

# Per-run buffer for individual test results (Redis List, max 24h TTL)
# Written by LiveEventStreamConsumer; drained by persist_live_session Celery task.
LIVE_TESTCASES_KEY = "testlookup:live:testcases:{run_id}"

# Durable, run-isolated live evidence. Unlike ``LIVE_EVENTS_STREAM`` (the
# bounded fan-out bus used by websocket consumers), this stream is never
# trimmed by a writer. The persistence drainer removes entries only after the
# matching PostgreSQL projection and checkpoint commit.
LIVE_EVIDENCE_STREAM_KEY = "testlookup:live:evidence:{run_id}"
LIVE_EVIDENCE_GROUP = "live-persistence-v1"
# TTL-less, per-run batch ledger. Each batch field stores its digest, accepted
# count, projection state, and Redis Stream ID. Sibling Sets hold stable event
# IDs for pending capacity, outcome totals, and received-event totals.
LIVE_BATCH_DEDUP_KEY = "testlookup:live:batch_state:{run_id}"

# ── Limits ────────────────────────────────────────────────────────────────────
LIVE_STREAM_MAXLEN      = 100_000   # max entries retained in live stream
LIVE_FANOUT_MAXLEN      = 20_000    # reconnect replay / cross-process fan-out
LIVE_FANOUT_PROJECT_MAXLEN = 2_000  # retained events per project for reconnect
INGESTION_STREAM_MAXLEN = 10_000
ANALYSIS_STREAM_MAXLEN  = 50_000
DLQ_STREAM_MAXLEN       = 5_000

# ── Consumer settings ─────────────────────────────────────────────────────────
CONSUMER_BATCH_SIZE     = 50        # messages read per iteration
CONSUMER_BLOCK_MS       = 1_000     # block timeout for XREADGROUP
STALE_CLAIM_INTERVAL_S  = 30        # seconds between XAUTOCLAIM sweeps
STALE_IDLE_MS           = 30_000    # messages idle > this get reclaimed
MAX_DELIVERY_ATTEMPTS   = 3         # move to DLQ after this many failures
