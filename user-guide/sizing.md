# Sizing & capacity planning

How much machine does TestLookup need? Short answer: **one modest host runs most teams** — the stack is designed to be operable without a dedicated DevOps specialist. This guide gives three reference profiles, per-service breakdowns, and the disk-growth math to plan retention.

**How to read the numbers.** Figures marked **(measured/configured)** come from the shipped configuration — the Compose resource limits in `docker-compose.yml` and the ingestion constants in `backend/app/core/config.py` (the admission-gate design behind them is [architecture/INGESTION_SCALE.md](../architecture/INGESTION_SCALE.md)). Figures marked **(estimate)** are engineering estimates from the schema shape — validate them against your own data after a week of real traffic (`make preflight` prints your live volume sizes).

## The three profiles

“Results/day” = individual test results ingested per day, across all projects (e.g. 200 CI runs × 500 tests = 100k results/day).

| | **Small** ≤ 50k results/day | **Medium** ≤ 500k results/day | **Large** 1M+ results/day |
|---|---|---|---|
| Typical shape | a few teams, tens of CI runs/day | an engineering org, hundreds of runs/day | central QA platform, many orgs/projects |
| Topology | single host, Docker Compose | single beefy host or small k8s | Kubernetes (`k8s/base` + overlay) |
| CPU | 4 vCPU | 8 vCPU | 16+ vCPU (estimate) |
| RAM | 8 GB | 16 GB | 32+ GB (estimate) |
| Disk (1-year retention, no LLM) | 100 GB SSD | 500 GB SSD | 1+ TB NVMe (estimate) |
| Postgres growth | ~100 MB/day (estimate) | ~1 GB/day (estimate) | ~2+ GB/day (estimate) |
| Celery workers | 1 worker container, concurrency 4 (default) | 2 worker containers or concurrency 8 | dedicated worker deployments per queue + HPA (the k8s base ships this) |
| Local LLM add-on | +4 vCPU / +8 GB RAM / +10 GB disk | same | same, ideally a GPU node (8 GB+ VRAM) |

All three run the same images — scaling is replicas and resources, not architecture. The medium→large jump is where Kubernetes pays for itself: the ingestion path is already sharded into 8 queues **(configured: `LIVE_INGEST_SHARD_COUNT=8`)** and the k8s base ships per-queue worker deployments with HPAs ([architecture/DEPLOYMENT.md §3](../architecture/DEPLOYMENT.md#3-kubernetes--one-base-many-overlays)).

## Per-service breakdown

The dev/self-host Compose stack ships these limits **(configured — `deploy.resources` in `docker-compose.yml`)**; they are a sane baseline for the **small** profile. For medium/large, raise the ones marked ↑.

| Service | CPU limit | Mem limit | Mem reservation | Notes |
|---|---|---|---|---|
| postgres | 2.0 | 4 GB ↑ | 512 MB | The primary store; give it the fastest disk you have. Medium: 8 GB, large: 16 GB + tuned `shared_buffers` (estimate). |
| mongo | 2.0 | 4 GB | 512 MB | Pipeline event logs / audit streams. Grows with ingestion volume; see the math below. |
| redis | 1.0 | 768 MB | 128 MB | Broker + caches + live buffers. `maxmemory 512mb` **(configured)**; ingestion backpressure rejects new batches at 75% of maxmemory **(configured: `INGEST_REDIS_MEMORY_THRESHOLD_PCT`)**. Raise maxmemory for heavy live-streaming (many concurrent SDK sessions). |
| minio | — | — | — | Unbounded by config; sized by your attachments/reports (see disk math). |
| backend | 2.0 | 2 GB | 256 MB | Stateless — scale horizontally behind an ingress if API latency matters. |
| worker | 2.0 | 3 GB ↑ | 512 MB | Ingestion + analysis. First knob to turn: `CELERY_CONCURRENCY` (default 4) or more replicas. Every ingestion worker must subscribe to **all** shard queues ([INGESTION_SCALE.md §3](../architecture/INGESTION_SCALE.md#3-shard-queues-workeringestion_routingpy)). |
| beat / flower / frontend / mcp | — | — | — | Negligible (tens of MB each). |

**Throughput guardrails (configured)** — these protect the stack rather than limit your sizing, but they matter for burst planning:

- Each project's batch ingest is budgeted at **200 batches/minute** (`INGEST_RATE_LIMIT_PER_MINUTE`). SDK event batches, JSON result uploads (`POST /api/v1/ingest`) and result-file uploads (`POST /api/v1/ingest/file`) all draw on this one budget.
- Single live events (`POST /ws/events/{run_id}`) have a separate budget of **20,000 events/minute** per project (`INGEST_EVENT_RATE_LIMIT_PER_MINUTE`).
- A spent budget answers `429` with a `Retry-After` header. While Redis memory is over its backpressure threshold, every result-ingest route answers `503`. Setting a budget to `0` disables it.
- Live run buffers cap at **50,000 events/run** (`LIVE_BUFFER_MAX_EVENTS_PER_RUN`), and older events are drained to Postgres every ~30 s. The live event stream is capped at 100k entries.

## The local-LLM add-on (`make dev-llm`)

Everything above runs the rules/ML analysis tier. Adding the local LLM tier (Ollama + ChromaDB) costs:

| Component | Disk | RAM | Notes |
|---|---|---|---|
| Ollama + `qwen2.5:7b` | ~5 GB | ~6–8 GB during inference (estimate) | CPU inference works but is slow (tens of seconds per analysis); a GPU with 8 GB+ VRAM is the comfortable path. The larger models (`make pull-llm-large`) want 16 GB+ VRAM. |
| `nomic-embed-text` | ~0.3 GB | small | Embeddings for semantic search / RAG. |
| ChromaDB | ~1–2 KB per indexed test (estimate) | ~0.5–1 GB | Rebuildable state — it is deliberately **excluded from backups** and re-indexed hourly from Postgres. |

LLM **cost** ceilings are separate from sizing: analysis is debounced, budgeted per project/day, and sampled at high volume ([INGESTION_SCALE.md §6](../architecture/INGESTION_SCALE.md#6-keeping-the-ai-layer-affordable-under-volume)), so ingestion volume does not translate 1:1 into inference load.

## Disk-growth math (retention planning)

The dominant growth is per-result rows in Postgres (`test_cases`) plus per-result event documents in Mongo. Working from the schema ([architecture/DATABASE_SCHEMA.md — `test_cases`](../architecture/DATABASE_SCHEMA.md)):

- A **passing** result stores names/identifiers (test/full/suite/class/package names, fingerprint, tags) plus a search `tsvector` — **~0.7 KB heap + ~0.8 KB across its 6 indexes ≈ 1.5 KB (estimate)**.
- A **failing** result adds `error_message` + `stack_trace` (Text) — **~4–8 KB (estimate)**.
- At a typical 5–10% failure rate, a blended **~2 KB per result** including index overhead is a safe planning number **(estimate)**. Mongo pipeline events add roughly another **~1 KB per result (estimate)**.

So:

```
Postgres+Mongo growth/day ≈ results_per_day × 3 KB
```

| Profile | Results/day | Growth/day | 30 days | 1 year |
|---|---|---|---|---|
| Small | 50,000 | ~150 MB | ~4.5 GB | ~55 GB |
| Medium | 500,000 | ~1.5 GB | ~45 GB | ~550 GB |
| Large | 1,000,000 | ~3 GB | ~90 GB | ~1.1 TB |

All estimates — multiply by your real failure rate and stack-trace verbosity. Two additional pools:

- **MinIO** grows only with what you attach: screenshots/videos/HTML reports uploaded alongside results, compliance packs, RAG documents. A Playwright shop attaching failure screenshots can easily outgrow the databases here — budget it from your attachment policy, not from result counts.
- **Backups** (`make backup`) are compressed; expect an archive at roughly 20–40% of the live Postgres+Mongo footprint **(estimate)**. Keep at least 2× the newest archive free on the backup destination.

When disk pressure arrives, the levers are per-project retention/cleanup (**Settings → Project Data**) and pruning old backups. Keep free headroom above **2× the Postgres volume size** so restores and migration table rewrites always have working room (`make preflight` checks this for you).

## Related

- Day-2 operations — backup/restore and upgrades: [administration.md](administration.md#backup-restore--upgrades)
- Deployment topologies (Compose vs k8s, overlays): [architecture/DEPLOYMENT.md](../architecture/DEPLOYMENT.md)
- The ingestion-scale machinery behind the guardrails: [architecture/INGESTION_SCALE.md](../architecture/INGESTION_SCALE.md)
