"""
Redis Streams infrastructure for event-driven, fault-tolerant processing.

Stream names and consumer group constants used across producers and consumers.
"""

# ── Stream names ──────────────────────────────────────────────────────────────
LIVE_EVENTS_STREAM  = "testlookup:stream:live_events"   # live test execution events
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
CIRCUIT_KEY     = "testlookup:circuit:llm"           # Circuit breaker state

# Live session management keys
SESSION_TOKEN_KEY = "testlookup:session:token:{token}"   # token → session_id (TTL: 24h)
SESSION_ACTIVE_KEY = "testlookup:session:active"         # Set of active session IDs

# Per-run buffer for individual test results (Redis List, max 24h TTL)
# Written by LiveEventStreamConsumer; drained by persist_live_session Celery task.
LIVE_TESTCASES_KEY = "testlookup:live:testcases:{run_id}"

# ── Limits ────────────────────────────────────────────────────────────────────
LIVE_STREAM_MAXLEN      = 100_000   # max entries retained in live stream
INGESTION_STREAM_MAXLEN = 10_000
ANALYSIS_STREAM_MAXLEN  = 50_000
DLQ_STREAM_MAXLEN       = 5_000

# ── Consumer settings ─────────────────────────────────────────────────────────
CONSUMER_BATCH_SIZE     = 50        # messages read per iteration
CONSUMER_BLOCK_MS       = 1_000     # block timeout for XREADGROUP
STALE_CLAIM_INTERVAL_S  = 30        # seconds between XAUTOCLAIM sweeps
STALE_IDLE_MS           = 30_000    # messages idle > this get reclaimed
MAX_DELIVERY_ATTEMPTS   = 3         # move to DLQ after this many failures
