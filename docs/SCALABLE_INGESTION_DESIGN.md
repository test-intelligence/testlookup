# Scalable live-run ingestion — design

**Status:** Phases 1 + 2 + 3 + 4-partial (high-volume sampling + DLQ) landed 2026-05-16. Phase 3b (coalesced finalize_run), Phase 4.5 (WebSocket coalescer), Phase 4.6 (SDK retry alignment) staged in `docs/BACKLOG.md` for future sessions.
**Owners:** ingestion + worker subsystem.
**Companion code:** `backend/app/routers/stream.py`, `backend/app/services/stream_service.py`, `backend/app/streams/live_consumer.py`, `backend/app/worker/tasks.py::persist_live_session`, `backend/app/services/ingestion_pipeline.py`.

---

## 1. Problem statement

The user reported the scenario:

> *"In realistic scenarios there are hundreds of live test runs ingesting data, the testlookup should not be overwhelmed and crash."*

The current path is functionally correct but is built for a single CI pipeline pushing one live run at a time. When **hundreds of concurrent live runs** push events into the system simultaneously, several pre-existing single-points-of-strain compound into a hard failure mode:

1. Stream consumer can't drain the buffer fast enough → Redis memory grows unbounded → OOM kill.
2. Celery `persist_live_session` tasks pile up faster than workers can process them → backlog blooms in the `ingestion` queue.
3. `finalize_run` fires one `run_agent_pipeline` per run → AI queue + LLM provider (Ollama / hosted) saturate, knock-on tail latency on **every** run.
4. PostgreSQL connection pool exhausts → request handlers start blocking on `db.connect()` → 503s on unrelated endpoints.
5. WebSocket broadcasters fan out every event to every connected client → browser tabs crash → users reload → reconnection storm.
6. No backpressure to the SDK → a misbehaving client / runaway loop floods the system before operators notice.

Result: the dashboard and ingestion both go down at the same time. The user can't see *why* the system fell over because the dashboard reads from the same Redis / Postgres that's saturated.

---

## 2. Current architecture (as of 2026-05-16)

```
┌─────────────┐    POST /api/v1/stream/ingest  (or /events/batch)
│ SDK / CI    │──────────────────────────────────────────┐
└─────────────┘                                          ▼
                                          ┌───────────────────────┐
                                          │ stream_service        │
                                          │ ._persist_event_batch │
                                          │   • publish XADD      │
                                          │   • RPUSH per-run buf │
                                          │   • HINCRBY counters  │
                                          │   • EXPIRE keys       │
                                          └────┬──────────────────┘
                                               │
                                               ▼
                ┌──────────────────────────────────────────┐
                │ Redis                                    │
                │   stream: testlookup:stream:live_events  │
                │   list:   testlookup:live:testcases:{r}  │
                │   hash:   testlookup:live:state:{run}    │
                └──────┬──────────────────────────────────┘
                       │  XREADGROUP   (LiveEventStreamConsumer asyncio task)
                       ▼
              ┌──────────────────────────────┐
              │  websocket / SSE broadcast    │
              │  + run_complete detection     │
              └──────────────┬────────────────┘
                             │  close_session  (synchronous DB)
                             ▼
              ┌────────────────────────────────────────────┐
              │  worker.tasks.persist_live_session         │
              │   • drain Redis list                       │
              │   • bulk-add TestCase rows                 │
              │   • event_archive write (migration 0086)   │
              │   • finalize_run(...)                       │
              └─────────────────┬──────────────────────────┘
                                │
                                ▼
              ┌────────────────────────────────────────────┐
              │  ingestion_pipeline.finalize_run            │
              │   • _update_run_aggregates                  │
              │   • sync_suite_membership   (own session)   │
              │   • sync_canonical_test_cases (own session) │
              │   • assign_failed_tests     (own session)   │
              │   • auto_tag_test_cases/run  (own session)  │
              │   • quarantine tags         (own session)   │
              │   • run_agent_pipeline (Celery)             │
              └────────────────────────────────────────────┘
```

**Key sync points + their failure mode at scale:**

| Step | What it does | Failure mode at 100+ concurrent runs |
|------|--------------|--------------------------------------|
| `_persist_event_batch` | RPUSH every event to per-run Redis list + HINCRBY counters in a pipeline | Redis memory grows linearly with runs × events. No per-project cap. 25h TTL on the list means peak memory ≈ peak runs over a sliding 25h window. |
| `LiveEventStreamConsumer` | Single asyncio reader, sequential per-event WS fan-out | Single point of throughput. No batching. WS broadcast is per-event, not per-batch. |
| `close_session → persist_live_session` | Queues exactly one Celery task per run | OK, but the task is heavy: read N events from Redis, bulk-add N TestCase rows, then call `finalize_run` which opens 5 separate sessions. |
| `finalize_run` | 5 isolated sessions + 1 AI pipeline trigger | 5× DB sessions per run × 100 runs = 500 concurrent connection attempts. Default pool size is 20 — saturates immediately. |
| `run_agent_pipeline` | One LLM-backed pipeline per run | LLM concurrency capped at `LLM_MAX_CONCURRENT_ANALYSES=3`. 100 pipelines queue serially behind 3 workers = tail latency hours. |
| WebSocket broadcast | One frame per event per client per project | 100K events/min × N tabs = browser-killer fan-out. |

**No backpressure exists today.** `_persist_event_batch` accepts every batch. Redis only complains when memory runs out. The first user-visible signal of overload is `503` from the readiness probe after Redis OOMs.

---

## 3. Target architecture

The design has four layers; each has its own bottleneck-management mechanism so a problem in one layer can't cascade.

### Layer A — Gate (per-request admission)

Goal: reject before we waste a Redis op.

* **Per-project token-bucket rate limit** at `/stream/ingest` and `/stream/events/batch`. Default budget: 200 batches/minute per project (≈ 20K events/minute assuming the SDK batches at 100 events). Configurable per-project later; default suffices for v1.
* **Adaptive Redis-memory backpressure** — when Redis `used_memory` is above a threshold (default 75%, configurable), reject new ingest with `503 Service Unavailable` + `Retry-After: 5`. Lets in-flight runs drain without piling on.
* **Soft circuit breaker per project** — if a project trips rate limit > N times in a window, escalate the `Retry-After` so the client backs off harder.

### Layer B — Buffer (capacity-bounded staging)

Goal: keep Redis memory bounded under burst.

* **Per-run buffer cap** — `LIST LIMIT` style: write to a capped list (`LTRIM` to keep last N events). When sampling kicks in (Layer D), the buffer reflects the sample, not the raw stream.
* **Per-project event-archive eviction** — `event_archive` is bounded by the 15-day retention from migration 0086, but under burst we add a project-wide soft cap on archived events (configurable, default 5M events × 500 bytes ≈ 2.5 GB per project). Excess evicts oldest archive first.
* **Stream maxlen** — `XADD MAXLEN ~ 1_000_000` (already in `streams/__init__.py`). Auto-trims the stream so consumers can lag without unbounded growth.

### Layer C — Worker pool (fair scheduling)

Goal: never let one project starve another.

* **Per-project Celery routing** — `persist_live_session.<project_id>` shard. A noisy project can saturate one shard without blocking others. Existing routing config sends everything to `ingestion`; this introduces shard-aware routing without breaking the queue contract.
* **Fleet-budgeted connection pools** — keep each worker process within the centrally calculated PostgreSQL fleet budget. The production Kubernetes worker contract uses `PG_POOL_SIZE=1, PG_MAX_OVERFLOW=1`; increasing either value requires recalculating `PG_FLEET_REQUIRED_CONNECTIONS` for maximum HPA replicas and proving the database still retains its operational reserve.
* **Coalesced finalize** — when N persist_live_session tasks for the same project complete within a 30s window, schedule **one** `finalize_run` per project that fans out to all runs in the window. Saves 4× session creations per run.

### Layer D — AI pipeline (debounced fan-in)

Goal: stop one run-completion = one LLM call.

* **Pipeline debouncer** — instead of `finalize_run → enqueue run_agent_pipeline(run_id)`, the trigger is `enqueue debounced_pipeline_trigger(project_id, run_id)`. The debouncer keeps a Redis SortedSet of pending runs per project; a beat task fires once per N minutes (default 2min) and pops all queued runs into a **batch pipeline** call.
* **Per-project AI budget** — hard daily LLM-call cap per project (already partly via `llm_cost_budget_tables`, just needs wiring into the debouncer's reject path). When the budget is hit, the debouncer falls back to rules+ML for the rest of the day and surfaces a degraded-mode banner in the UI.
* **Sampling for high-volume projects** — config flag: store 1-of-N test_cases rows for runs flagged `high_volume`. Aggregates remain accurate (read from `test_runs.*_tests` columns), per-test rows are sampled. Saves persist time + DB size + AI input length.

---

## 4. Phased rollout

Each phase ships standalone so we never have a half-built abstraction in `main`.

### Phase 1 — Gate (this session)

* ✅ Per-project token-bucket rate limit at `/stream/ingest` + `/stream/events/batch`.
* ✅ Adaptive backpressure on Redis memory pressure (>75% used = 503 + Retry-After).
* ✅ `/api/v1/health/ingestion` — surfaces queue depths, Redis memory, recent ingest-rate, recent reject-rate, active live sessions.
* ✅ Tests for all three.
* Outcome: a misbehaving project gets clear, immediate feedback (429 / 503 with structured retry guidance) before it can OOM Redis.

### Phase 2 — Buffer + worker fairness — ✅ SHIPPED 2026-05-16

* ✅ Per-run `LTRIM`-bounded buffer (`LIVE_BUFFER_MAX_EVENTS_PER_RUN=50000`).
* ✅ Per-project Celery queue routing for `persist_live_session` (`LIVE_INGEST_SHARD_COUNT=8`, queue prefix `ingestion.shard.<i>`).
* ✅ PostgreSQL pools are configured per process role and checked against the production fleet budget; ingestion workers use size 1 plus overflow 1 in Kubernetes.
* ✅ Bulk-insert TestCase rows via chunked `execute_many` (`PERSIST_LIVE_BULK_INSERT_CHUNK=1000`).
* Pending: synthetic 500-run × 5K-event stress test. Code paths are ready; the test needs a real Redis + worker pool to run (not a unit test). Tracked in BACKLOG.

### Phase 3 — AI pipeline debouncer — ✅ SHIPPED 2026-05-16

* ✅ `run_agent_pipeline` trigger moved to a Redis SortedSet (`testlookup:ai_pipeline_debounce`) → beat-task debouncer firing every 2 minutes (`flush-ai-pipeline-queue`).
* ✅ Per-project LLM-budget integration via `services.llm_cost_budget.check_and_apply_cap`. Hard-block drops the project's queued pipelines for the day; `mode_override="rules"` / `mode_override="ml"` threads through as a Celery header so the analysis-router transparently downgrades.
* ✅ `enqueue_pipeline_for_run` is idempotent (SortedSet member dedup) and falls back to direct `apply_async` when Redis is unreachable so a Redis hiccup never loses a pipeline trigger.
* ✅ `/health/ingestion` now surfaces `ai_pipeline.pending_in_debouncer` + `ai_pipeline.degraded_projects` for ops dashboards.
* **Phase 3b (deferred):** Coalesced `finalize_run` — process N runs in one transaction when they complete in the same 30s window. Requires refactoring `sync_suite_membership`, `sync_canonical_test_cases`, `assign_failed_tests`, `auto_tag_test_cases/run`, and `_apply_quarantine_tags` to accept batched run_id lists. High regression risk against the live-stream gap fix; not worth it until stress-test data tells us the remaining session-creation overhead matters.

### Phase 4 — Sampling + WebSocket coalescing

* Per-project `high_volume` config flag → sample 1-of-N TestCase rows.
* WebSocket coalescer: pace frames at ≤ 10/sec per client, server-side aggregate updates.
* Dead-letter queue for `persist_live_session` after N retries.

---

## 5. Open questions — ANSWERED 2026-05-16

The user locked these in mid-session; Phases 2-4 now have concrete numbers to plan against.

| # | Question | Answer | Phase 2-4 implication |
|---|----------|--------|-----------------------|
| 1 | Target peak scale | **500 concurrent live runs** | At ~100 events/sec/run sustained = 50K events/sec aggregate peak. Phase 2 sizing: per-project Celery shard pool must absorb 50K/sec without queue depth blowing past a few thousand. Rate-limit default of 200 batches/min/project (= 20K events/min) supports ~25 events/sec/run — enough headroom for a 500-run mix with 90th-percentile burstiness. |
| 2 | LLM cost ceiling per project per day | **$10/project/day** | At Sonnet 4.6 input pricing (~$3/M input tokens) = 3.3M tokens/day. Typical analysis pipeline consumes ~5K tokens/run → ~660 pipelines/day → ~28/hour. Phase 3 debouncer must enforce this hard via `llm_cost_budget_tables`; when the cap hits, fall back to rules+ML for the rest of the day. |
| 3 | SDK retry contract on 429 / 503 | **Retry on both 429 and 503** (user-confirmed 2026-05-16) | SDK retries both response codes with exponential backoff capped at the `Retry-After` header. **Server-side: no change** — the existing per-minute fixed bucket refreshes automatically, so an SDK that honours `Retry-After` retries successfully on the next minute boundary. **SDK-side: align both clients** (Python + Java) to: (a) treat 429 + 503 as retryable, (b) honour `Retry-After` exactly (not a doubled/halved value), (c) cap total retry duration at 5 minutes so a misconfigured project still surfaces a hard failure to the user eventually rather than buffering forever. Tracked in BACKLOG under "SDK retry-policy alignment". |
| 4 | Sampling tolerance for high-volume projects | **50% — store 1-of-2 events** | Phase 4 sampler keeps every other per-test row when the project is auto-flagged `high_volume` (e.g. sustained > 1000 tests/min). Aggregates (`test_runs.passed_tests` etc.) still read directly from the SDK's run-state hash, so they remain 100% accurate. Only `test_cases` rows are sampled. |
| 5 | Horizontal vs vertical scale-out | **Horizontal** | Phase 2 routing partitions deterministically by `hash(project_id) mod N` for a fixed shard count. Replica scaling does not change routing. Changing N rebalances most projects and requires the pause-and-drain procedure in `docs/operations/ingestion-shard-count-change.md`. Per-pod resource ceiling stays modest (~4 CPU / 4 GB) so K3s schedules them on existing nodes. |

**Closed 2026-05-16:** SDK retries both 429 and 503 with `Retry-After` backoff. Server-side rate-limit fixed-bucket already accommodates this (bucket refreshes on the minute boundary; an SDK that respects `Retry-After` retries cleanly). Phase 2 includes a separate SDK-alignment task for the Python + Java clients to ensure consistent retry behaviour.

## 5b. Phase 2-4 sizing locked in

* **Phase 2 shard count** = 8 (covers 500 runs / 8 shards = ~63 runs per shard, well under what one worker can handle with bulk-insert).
* **Phase 3 debouncer cadence** = every 2 min per project (already in design § 3 D) — capped at $10 implies ~28 pipelines/hour/project which is far below 30/hour, so the cadence has headroom.
* **Phase 4 sampler trigger** = sustained > 1000 tests/min for ≥ 3 consecutive minutes flips the project's `high_volume` flag. Manual override available via Settings → Project Data.

---

## 6. Phase 1 — implementation notes

### 6.1 Rate limit

`backend/app/services/ingestion_rate_limit.py` — a small token-bucket implementation backed by Redis. Keyed by `project_id`. Each `/stream/ingest` and `/stream/events/batch` call consumes 1 token.

* Default: `INGEST_RATE_LIMIT_PER_MINUTE=200` (≈ 20K events/min/project at the SDK's 100-event batches).
* Tunable via env var.
* When over budget: raises `HTTPException(429)` with `Retry-After: <seconds_until_next_token>` header.
* Storage: Redis `INCR` + `EXPIRE` per minute bucket. O(1) per request.

### 6.2 Adaptive backpressure

`backend/app/services/ingestion_backpressure.py` — checks Redis `INFO memory` every N seconds (cached) and refuses new ingest when `used_memory` ratio crosses the threshold.

* Default: `INGEST_REDIS_MEMORY_THRESHOLD_PCT=75`.
* Cache TTL: 5 seconds — Redis `INFO` is cheap but called once per HTTP request would dominate the path.
* When over threshold: raises `HTTPException(503)` with `Retry-After: 5`.

### 6.3 `/api/v1/health/ingestion`

`backend/app/routers/health.py` — new endpoint, ADMIN / QA_LEAD readable. Returns:

```json
{
  "status": "ok | degraded | overload",
  "redis": { "used_memory_pct": 42.0, "memory_threshold_pct": 75 },
  "queues": { "ingestion": 12, "ai_analysis": 3, "critical": 0, "default": 1 },
  "live_sessions": { "active": 7 },
  "recent": {
    "ingest_rate_per_minute": 120,
    "reject_rate_per_minute": 0,
    "reject_reason_breakdown": {}
  },
  "thresholds": {
    "rate_limit_per_minute": 200,
    "redis_memory_threshold_pct": 75
  }
}
```

A future Grafana panel will alert on `status == "overload"` so on-call doesn't have to know which sub-system blew up.
