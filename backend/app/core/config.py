"""
TestLookup — Application Configuration
All settings loaded from environment variables with sensible defaults.
"""
import json as _json
import os as _os
import re as _re
from functools import lru_cache
from typing import List, Literal, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The Redis client's socket timeout (app.db.redis_client). One Redis call --
#: an LLM slot renew, say -- can hang this long before it fails.
REDIS_SOCKET_TIMEOUT_SECONDS = 5

# Belt-and-suspenders: disable ChromaDB's anonymous telemetry via env var.
# NOTE: the installed chromadb (0.5.20) does NOT reliably honour this env var
# for HttpClient — it still emits "Failed to send telemetry event ..." errors.
# The authoritative fix is to pass an explicit Settings(anonymized_telemetry=
# False) to every client; see app/db/chroma.get_chroma_client(). This setdefault
# is kept as a harmless additional safeguard.
_os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


#: Every convention this repo uses to write "you must replace this".
#:
#: Kept as a list because the repo does not have one convention. ``.env.example``
#: writes ``change-me-…``; ``.env.gcp-vm.example`` and
#: ``infra/cloudrun/backend.env.example`` write ``replace-with-…``; the k8s
#: secret template writes ``<base64-encoded-…>``. The first version of this
#: guard knew only the first of those, so it closed the hole for one of three
#: shipped example files.
#:
#: None of these can match a generated secret. ``openssl rand -hex`` — what
#: every script and document here tells the operator to run — emits
#: hexadecimal, which contains none of these substrings.
_SECRET_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "change-me",
    "change_me",
    "replace-with",
    "replace_with",
    "your-",
    "set-a-",
)

#: The templates also write a placeholder as ONE angle-bracketed token --
#: ``<base64-encoded-strong-random-secret>``, ``<set-a-strong-password>``,
#: ``<pw>``, and in deploymentsteps.md ``<your generated key from Step 9.3>``.
#: A bare ``"<"`` used to stand for that, which refused a real secret that
#: merely contains the symbol (QA of re-audit N12). A token found ANYWHERE in
#: the value then still refused about one random 32-character password in 157
#: (``Tr0ub<A>dor&3``), and missed the guide's, which has spaces (code review
#: and QA of that fix). So the token must be the WHOLE value: it opens with a
#: letter or digit, and holds only letters, digits, spaces, dots, hyphens and
#: underscores.
_ANGLE_PLACEHOLDER = _re.compile("<[a-z0-9][a-z0-9 ._-]*>")


def _is_placeholder_secret(value: str) -> bool:
    """True when a secret is unset or still one of the shipped placeholders.

    This compared against the field DEFAULT exactly, which missed every
    placeholder an operator is actually likely to be running. ``.env.example``
    ships ``JWT_SECRET_KEY=change-me-generate-with-openssl-rand-hex-32``,
    ``APP_SECRET_KEY=change-me-in-production-use-openssl-rand-hex-32`` and
    ``WEBHOOK_SECRET=change-me-generate-with-openssl-rand-hex-32`` — none equal
    to its default, so copying the example file and deploying it booted
    production with three published secrets and no complaint. The GCP and Cloud
    Run example files, which both ship ``APP_ENV=production``, use a different
    wording again and were missed by the first fix for the same reason.

    Substring, not prefix: a placeholder is sometimes embedded rather than
    leading (a connection URI carrying the password, for instance). The
    angle-bracketed token is the exception: it must be the whole value (a
    URI's password is judged on its own, by ``_uri_password``).
    """
    text = str(value or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in _SECRET_PLACEHOLDER_MARKERS) or bool(
        _ANGLE_PLACEHOLDER.fullmatch(text)
    )


def _uri_password(uri: str) -> Optional[str]:
    """The password embedded in a connection URI, or None when there is none.

    Only the credential is judged: a password-less URI (the default, or a
    deployment using another auth mechanism) is never a placeholder, and the
    host and database parts are not secrets.
    """
    from urllib.parse import unquote, urlsplit

    try:
        password = urlsplit(str(uri or "")).password
    except ValueError:
        return None
    return unquote(password) if password else None


# The most test results one ingest may carry. The JSON batch schema enforces it
# at the API (``IngestPayload.results``); the upload worker enforces it after
# parsing a file, through ``INGEST_MAX_RESULTS_PER_UPLOAD``, which defaults to
# it. One number, so the two paths cannot drift apart (re-audit M5).
MAX_RESULTS_PER_INGEST = 50_000


class Settings(BaseSettings):
    """Application settings — loaded from environment variables."""

    # ── Application ─────────────────────────────────────────
    APP_NAME: str = "TestLookup"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_SECRET_KEY: str = "change-me-in-production"
    # Optional previous key for secret-at-rest rotation. When set, the secret
    # service decrypts with a MultiFernet [current, previous] so values
    # encrypted under the old key keep decrypting while new writes use the
    # current key. Rotate, re-encrypt on next write, then drop this.
    APP_SECRET_KEY_PREVIOUS: Optional[str] = None
    APP_DEBUG: bool = False
    APP_VERSION: str = "0.0.1"
    # ── Build provenance ────────────────────────────────────
    # Injected at image-build time (backend/Dockerfile ARGs, wired from
    # release.yml) so a running container can self-report exactly which
    # commit + build it is. Surfaced by GET /health/details so a self-host
    # operator can match a pinned image digest back to its source without
    # shelling into the pod. Left empty in local/dev runs → reported as
    # "unknown".
    BUILD_REVISION: str = ""
    BUILD_DATE: str = ""
    CORS_ORIGINS_RAW: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        validation_alias=AliasChoices("CORS_ORIGINS_RAW", "CORS_ORIGINS"),
    )
    # Externally-reachable base URL for the dashboard. Used to build links
    # rendered in Jira tickets, Slack/Teams notifications, and emails. Should
    # match the ingress hostname users actually open in their browser
    # (e.g. https://testlookup.example.com or http://testlookup.local).
    # When unset, falls back to the first CORS_ORIGINS entry, or localhost in
    # development.
    PUBLIC_BASE_URL: str = ""

    # ── Database (PostgreSQL) ────────────────────────────────
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433  # Docker maps 5433→5432 to avoid conflict with any local PostgreSQL
    POSTGRES_DB: str = "testlookup"
    POSTGRES_USER: str = "testlookup_user"
    POSTGRES_PASSWORD: str = ""  # Must be set via .env — no default
    DATABASE_URL: str = ""       # Must be set via .env — no default

    # ── MongoDB ──────────────────────────────────────────────
    MONGO_HOST: str = "localhost"
    MONGO_PORT: int = 27017
    MONGO_DB: str = "testlookup_logs"
    MONGO_URI: str = "mongodb://localhost:27017"

    # ── Storage (S3 / MinIO / Local) ─────────────────────────
    STORAGE_BACKEND: Literal["minio", "s3", "local"] = "minio"
    LOCAL_STORAGE_PATH: str = "/tmp/testlookup_data"
    
    # ── MinIO / S3 Settings ──────────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "testlookup_minio"
    MINIO_SECRET_KEY: str = ""  # Must be set via .env — no default
    MINIO_BUCKET_NAME: str = "test-telemetry"
    MINIO_USE_SSL: bool = False

    # ── Redis ─────────────────────────────────────────────────
    # Redis is the Celery broker + JWT-revocation store + membership authz
    # cache, so it must not be reachable unauthenticated. Set REDIS_PASSWORD and
    # it flows into the connection URLs below (compose passes it to the server's
    # --requirepass). Empty = no auth (backward-compatible for a purely-internal,
    # non-published Redis). Prefer embedding it in the URLs; this field documents
    # the source and is read by tooling/compose.
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_WORKER_CONCURRENCY: int = 4          # Set to 16-32 in production for 100+ concurrent users

    # ── Performance / Scalability tunables ────────────────────
    # PostgreSQL pool. These limits apply to *each OS process*. Production
    # manifests set them explicitly because Gunicorn and every Celery prefork
    # child own an independent SQLAlchemy pool.
    PG_POOL_SIZE: Optional[int] = Field(default=None, ge=1)
    PG_MAX_OVERFLOW: Optional[int] = Field(default=None, ge=0)
    PG_POOL_RECYCLE: int = Field(default=1800, ge=1)
    PG_POOL_TIMEOUT: int = Field(default=30, ge=1)
    PG_PROCESS_ROLE: Literal["api", "worker", "operation"] = "operation"
    PG_PROCESSES_PER_POD: int = Field(default=1, ge=1)
    # Fleet contract used by deployment validation and operator diagnostics.
    # PG_FLEET_MAX_CONNECTIONS must match SHOW max_connections on an external
    # production database before rollout.
    PG_FLEET_MAX_CONNECTIONS: int = Field(default=400, ge=1)
    PG_FLEET_OPERATIONAL_RESERVE: int = Field(default=50, ge=1)
    PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS: int = Field(default=3, ge=0)
    PG_FLEET_RESERVED_CONNECTIONS: int = Field(default=0, ge=0)
    PG_FLEET_MIGRATION_CONNECTIONS: int = Field(default=1, ge=1)
    PG_FLEET_REQUIRED_CONNECTIONS: int = Field(default=273, ge=1)

    # MongoDB pool
    MONGO_MAX_POOL_SIZE: int = 50
    MONGO_MIN_POOL_SIZE: int = 5
    MONGO_SOCKET_TIMEOUT_MS: int = 30000
    MONGO_MAX_IDLE_TIME_MS: int = 60000

    # S3 / MinIO connection pool
    S3_MAX_POOL_CONNECTIONS: int = 50
    INGESTION_S3_CONCURRENCY: int = 10          # max parallel S3 fetches per ingestion run

    # AI analysis concurrency
    LLM_MAX_CONCURRENT_ANALYSES: int = 3        # max parallel LLM root-cause calls

    # Agent invocations over the public API (architecture E1.2, section 3.1).
    # mode=sync requests waiting at once, per process; a full pool is 503 + Retry-After.
    AGENT_INVOKE_SYNC_CONCURRENCY: int = 4
    # How long a mode=sync request waits for its run before answering 202.
    AGENT_INVOKE_SYNC_WAIT_SECONDS: int = 25
    # Lifetime of a single-use invocation event-stream ticket.
    AGENT_INVOKE_STREAM_TICKET_SECONDS: int = 60

    # WebSocket limits
    WS_MAX_CONNECTIONS_PER_PROJECT: int = 500
    WS_MAX_TOTAL_CONNECTIONS: int = 5000
    WS_BROADCAST_TIMEOUT: float = 5.0           # seconds before dropping a dead connection

    # ── Live-stream ingestion gate (Phase 1, 2026-05-16) ──────
    # See docs/SCALABLE_INGESTION_DESIGN.md. Both limits operate per
    # project, per minute. Set to 0 to disable.
    INGEST_RATE_LIMIT_PER_MINUTE: int = 200       # batches per project per minute
    # Re-audit M4/M3. POST /ws/events takes ONE event per call, so it cannot
    # share the batch budget above: charging a batch token per event would
    # throttle an ordinary run to 200 results a minute. This is the
    # event-equivalent of that budget (200 batches x ~100 events). 0 disables.
    INGEST_EVENT_RATE_LIMIT_PER_MINUTE: int = 20000   # single events per project per minute

    # ── Manual upload: archive (zip) safety limits (MRU-12) ───
    # Bound the DECOMPRESSED footprint of an uploaded report zip (the
    # compressed wire size is already capped by the 50MB multipart limit).
    # Enforced by services.safe_archive.safe_extract_zip.
    MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 200 * 1024 * 1024  # 200 MB total
    MAX_ARCHIVE_ENTRIES: int = 5_000
    MAX_ARCHIVE_ENTRY_BYTES: int = 50 * 1024 * 1024          # 50 MB per entry
    MAX_ARCHIVE_RATIO: int = 100                             # uncompressed/compressed
    # Cap on an application/x-www-form-urlencoded request body (starlette
    # CVE-2026-54283: request.form() ignores its limits for this type, and
    # POST /api/v1/auth/login takes one unauthenticated). The only forms are
    # login/token exchanges, a few hundred bytes. middleware/request_hardening.py
    FORM_URLENCODED_MAX_BYTES: int = 64 * 1024
    # ...except the SAML ACS (POST binding), whose SAMLResponse may reach the
    # route's own 1,000,000-char limit (routers/sso.py) plus urlencoding growth.
    FORM_URLENCODED_ACS_MAX_BYTES: int = 2 * 1024 * 1024
    # Re-audit M5. The most test results one uploaded report may carry: the
    # same cap as a JSON batch, so a file is not a way around it. The 50MB
    # size limit bounds bytes, not rows -- 50MB of minimal JUnit elements is
    # ~1.3M results, and parsing that alone peaked at 1.3GB (measured). Checked
    # by a cheap count before parsing and exactly after; see
    # services/upload_limits.py. 0 disables.
    INGEST_MAX_RESULTS_PER_UPLOAD: int = MAX_RESULTS_PER_INGEST
    # Adaptive Redis-memory backpressure. When ``maxmemory`` is set on
    # Redis, the percentage gate fires; when it isn't, the absolute
    # byte gate kicks in instead. Both 0 disables the check entirely.
    INGEST_REDIS_MEMORY_THRESHOLD_PCT: float = 75.0   # of maxmemory
    INGEST_REDIS_MEMORY_ABSOLUTE_BYTES: int = 0       # 0 = disabled

    # ── Phase 2 buffer + worker fairness (2026-05-16) ─────────
    # Per-run Redis-list cap. ``LTRIM`` keeps the newest N events;
    # older events are evicted when the list exceeds the cap. 50K
    # events × 500B/event = 25 MB max per run. 0 disables the cap
    # (legacy behaviour — unbounded list growth).
    LIVE_BUFFER_MAX_EVENTS_PER_RUN: int = 50_000
    # Chunk size for the bulk-insert path in persist_live_session.
    # Larger chunks = fewer round-trips but more memory per session
    # transaction. 1000 is a healthy middle ground for the asyncpg
    # driver — round-trip cost amortises while staying under the
    # 1MB statement-size sweet spot.
    PERSIST_LIVE_BULK_INSERT_CHUNK: int = 1_000
    # Number of Celery shards for live-stream persist tasks. Workers
    # subscribe to ``ingestion.shard.<i>`` queues; tasks route by
    # ``hash(project_id) mod N``. Changing N remaps most projects; follow the
    # pause-and-drain runbook first. Set 0 for legacy single-queue routing.
    LIVE_INGEST_SHARD_COUNT: int = 8

    # ── Phase 4.5 incremental drain (2026-05-18) ──────────────
    # Live sessions stream events to a per-run Redis LIST capped at
    # ``LIVE_BUFFER_MAX_EVENTS_PER_RUN``. When the cap fires, the OLDEST
    # events fall off; aggregates from the HINCRBY hash stay accurate
    # but per-test rows are lost. The incremental drain task persists
    # buffered events to ``test_cases`` mid-session so the LTRIM only
    # ever evicts events that are already durable in Postgres. Default
    # ON. Disable to fall back to the legacy "drain only at
    # close_session" behaviour.
    LIVE_SESSION_DRAIN_ENABLED: bool = True
    # Max events drained per run per beat tick. The drain task runs
    # every 30s (see Celery beat ``drain-active-live-sessions``); at
    # 50K events/run × 30s window that's ~1667 events/sec sustained
    # per run, well within asyncpg single-connection throughput. Raise
    # for very-high-volume runs; the bulk-insert path scales linearly.
    LIVE_SESSION_DRAIN_BATCH_SIZE: int = 5_000

    # ── Phase 3 AI pipeline debouncer (2026-05-16) ────────────
    # When True, ``stream_service.close_session`` no longer fires
    # ``run_agent_pipeline`` directly; the run lands in a Redis
    # SortedSet drained every 2 minutes by the beat task. Set to
    # False to revert to the legacy per-run direct dispatch — used
    # by tests + any deploy that hasn't enabled the beat schedule.
    AI_PIPELINE_DEBOUNCE_ENABLED: bool = True
    # Minimum age a run must reach before the debouncer flushes it.
    # Shorter = lower per-run analysis latency but less burst-coalescing.
    # The default matches a 2-minute beat cadence (debounce ≪ cadence).
    AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS: int = 60

    # ── Pipeline wall-clock budget ────────────────────────────
    # The agent pipeline runs as a Celery task under a 1740s soft /
    # 1800s hard limit. Without an in-graph budget an oversized run
    # does not degrade -- it is killed at the soft limit and retried
    # WHOLE, twice, before reaching the DLQ. That is ~87 minutes of
    # ai_analysis capacity spent on one run while other runs queue
    # behind it, and the report that eventually publishes never says
    # it took three attempts to produce.
    #
    # This deadline is checked between stages AND before each per-test
    # analysis, so the pipeline stops starting new work and publishes a
    # *degraded* report naming what it skipped. Terminal synthesis is
    # exempt (see _DEADLINE_EXEMPT_STAGES) -- it is deterministic and
    # it is what turns a truncated run into a self-describing report
    # rather than nothing at all.
    #
    # Default leaves ~4 minutes under the soft limit for that terminal
    # synthesis, persistence, and finalisation. 0 disables the budget.
    AI_PIPELINE_DEADLINE_SECONDS: int = 1500
    # Operational alert grace after the wall-clock pipeline deadline. This is
    # applied by the scrape-time E2.3 collector before the overdue gauge moves
    # above zero, so deployments with a narrower deadline keep correct alerts.
    AGENT_PIPELINE_ALERT_GRACE_SECONDS: int = 300

    # ── Phase 4 high-volume sampling (2026-05-16) ─────────────
    # Auto-flag a project as ``high_volume`` when it sustains
    # ``HIGH_VOLUME_TESTS_PER_MINUTE`` test events per minute for
    # ``HIGH_VOLUME_CONSECUTIVE_MINUTES`` consecutive minutes. The
    # flag drives the sampler in ``persist_live_session`` to store
    # only 1-of-N TestCase rows. Aggregates remain 100% accurate
    # because they come from the live-state HINCRBY counters.
    HIGH_VOLUME_AUTO_DETECT_ENABLED: bool = True
    HIGH_VOLUME_TESTS_PER_MINUTE: int = 1_000
    HIGH_VOLUME_CONSECUTIVE_MINUTES: int = 3
    # Sample rate for flagged projects. ``N=2`` stores 1-of-2 rows
    # (50% per the design doc § 5b). ``N=1`` disables sampling even
    # when the flag is active (used to dial back if persistence
    # becomes the bottleneck again).
    HIGH_VOLUME_SAMPLE_EVERY_N: int = 2

    # ── Phase 4.3 ingestion dead-letter queue (2026-05-16) ────
    # When ``persist_live_session`` exhausts its retry budget, a
    # structured failure record is written to the Redis-backed DLQ
    # for operator inspection. Set to False to disable the writes
    # (used by tests that don't want the DLQ side effect).
    INGESTION_DLQ_ENABLED: bool = True

    # ── Phase I — canonical-deletion detection (2026-05-17) ───
    # A CanonicalTestCase is marked ``status='deleted'`` when its
    # fingerprint hasn't appeared in any of the project's last
    # ``CANONICAL_DELETION_WINDOW_RUNS`` runs. The window guards
    # against false positives from a single run that was scoped to
    # a tag filter or partial suite. The reconciler runs both
    # synchronously at the tail of ``finalize_run`` and as a nightly
    # beat safety net (``nightly-canonical-deletion-reconcile``).
    # Set to 0 to disable deletion detection entirely (e.g. while
    # comparing canonical vs legacy suite_memberships during the
    # Phase 2b cutover window).
    CANONICAL_DELETION_WINDOW_RUNS: int = 5

    # ── LLM Provider ─────────────────────────────────────────
    LLM_PROVIDER: Literal[
        "ollama", "lmstudio", "localai", "vllm",
        "openai", "gemini", "anthropic", "openrouter",
    ] = "ollama"
    LLM_MODEL: str = "qwen2.5:3b-instruct-q5_K_M"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Ollama's CONTEXT window. Never set before, so Ollama applied its own
    # default (2048 on many builds) and silently truncated any longer prompt
    # from the left -- taking the system prompt and, on the ReAct path, the
    # tool instructions with it, which surfaces later as a parse failure with
    # no stated cause. Distinct from LLM_MAX_TOKENS, which caps OUTPUT
    # (num_predict); one number cannot do both jobs.
    OLLAMA_NUM_CTX: int = 8192
    LMSTUDIO_BASE_URL: str = "http://localhost:1234/v1"
    LOCALAI_BASE_URL: str = "http://localhost:8080/v1"
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    # OpenRouter fronts many vendors behind one OpenAI-compatible endpoint and
    # one key, which makes it the cheapest way to exercise the AI paths on a
    # deployment that cannot host a model. Gated by AI_OFFLINE_MODE like every
    # other remote provider.
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # OpenRouter attributes traffic by these two headers and shows them on its
    # dashboard. Optional to the API, but without them the bill is one
    # anonymous lump with no way to tell which app produced it.
    OPENROUTER_SITE_URL: str = "https://github.com/anandtopu/testlookup"
    OPENROUTER_APP_NAME: str = "TestLookup"

    # Per-deployment token rates, overriding the built-in table in
    # ``services/llm_pricing.py``. List prices rarely match what an
    # organisation actually pays. JSON keyed "provider:model-regex":
    #   {"anthropic:sonnet": {"input_per_mtok": 2.4, "output_per_mtok": 12.0}}
    LLM_PRICE_OVERRIDES: Optional[str] = None

    # ── Embedding ─────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "ollama"
    EMBEDDING_MODEL: str = "nomic-embed-text:v1.5"

    # ── ChromaDB ──────────────────────────────────────────────
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8001
    CHROMA_COLLECTION: str = "testlookup_embeddings"
    # Where a locally-available ONNX embedding model lives. ChromaDB's default
    # embedder is NOT bundled — on first use it fetches 79 MB from AWS S3, which
    # ``AI_OFFLINE_MODE`` must forbid (it governs weight acquisition, not just
    # inference — see ``services/local_embedder_guard.py``). Point this at a
    # side-loaded model to keep semantic features working in a sealed install;
    # empty means "wherever ChromaDB would put it", which a baked-in image
    # satisfies.
    CHROMA_ONNX_MODEL_DIR: str = ""

    # ── AI Agent ─────────────────────────────────────────────
    AI_OFFLINE_MODE: bool = True
    # E8.4: enforce the human-review gate on AI report distribution (report PDF
    # export, share links, the release-readiness value). OFF (default): nothing
    # is refused or changed; every would-be refusal is recorded in
    # access_audit_logs, so the impact is visible before anything stops being
    # sent. ON: an unreviewed AI report is refused (409) unless the project sets
    # allow_unreviewed_distribution (watermarked, audited), and the release gate
    # reads PENDING_REVIEW. Existing runs have no accepted reviews, which is why
    # this is not simply on.
    REVIEW_GATE_ENFORCED: bool = False
    # Optional defense-in-depth provider allowlist. Empty preserves the
    # configured provider set; when populated, every get_llm() construction
    # and invocation must use one of these normalized provider ids.
    AI_LLM_PROVIDER_ALLOWLIST: str = ""
    # Optional comma-separated HTTP(S) origins for provider endpoint overrides.
    AI_LLM_ALLOWED_BASE_URLS: str = ""
    # Off-box hosts the deployment's own Slack and Teams webhooks and SMTP
    # relay may still reach while AI_OFFLINE_MODE is on: comma-separated host
    # names, and ".example.com" matches subdomains. A shared host admits every
    # tenant on it (any Slack workspace), so a webhook set per user or per team
    # never uses this list. Empty: every off-box destination is refused.
    OFFLINE_NOTIFICATION_ALLOWED_HOSTS: str = ""
    # Re-audit N25: recipient domains email may reach while AI_OFFLINE_MODE is
    # on (comma-separated; "corp.example" exactly, ".corp.example" subdomains).
    # Set: every recipient must match, whatever the relay. Empty: an on-box
    # relay may deliver (its own MTA policy governs onward routing); an
    # off-box relay admitted by OFFLINE_NOTIFICATION_ALLOWED_HOSTS is refused.
    OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS: str = ""
    AGENT_MEMORY_RETENTION_DAYS: int = 365
    AI_CONFIDENCE_THRESHOLD: int = 80
    AIQ_GAP_REFINEMENT_ENABLED: bool = False         # AIQ-P4: gap_detection + report_refinement deep stages (default off)
    AIQ_CONTRACT_VALIDATION_ENABLED: bool = False    # AIQ-P4: API contract specialist (default off)
    AIQ_CHANGE_OWNERSHIP_ENABLED: bool = False      # AIQ-P4: baseline/change + ownership specialist (default off)
    AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED: bool = False  # Phase 3: dark by default
    # Report-level evaluation must use published, tenant-authorized reports in
    # deployed environments.  Tests/fixture automation may opt in explicitly.
    AI_REPORT_EVAL_ALLOW_CALLER_CORPUS: bool = False
    # Retries after the first attempt, for outbound AI-layer calls (re-audit L2):
    # every LLM client get_llm() builds (the provider SDK's own max_retries for
    # the OpenAI-wire, Anthropic and Gemini clients; connect-phase failures only
    # for ChatOllama, which has no retry of its own) and Jira ticket creation.
    # A read timeout is not retried for Ollama: it would multiply wall clock.
    AI_MAX_RETRIES: int = 3
    AI_TIMEOUT_SECONDS: int = 300
    # E7.2: run-level retry policy for agent pipelines (requirement 8). A failed
    # attempt moves the row to retry_wait and schedules a same-id resume with
    # exponential backoff; the count lives on agent_pipeline_runs.attempt.
    # AGENT_PIPELINE_MAX_ATTEMPTS is the default (5); AGENT_MAX_ATTEMPTS_CEILING
    # is the environment ceiling no project or row may exceed.
    AGENT_PIPELINE_MAX_ATTEMPTS: int = 5
    AGENT_MAX_ATTEMPTS_CEILING: int = 10
    # E4.1: no project may configure an agent timeout above this (section 4.3).
    AGENT_MAX_TIMEOUT_CEILING: int = 600
    AGENT_RETRY_BASE_SECONDS: int = 30
    AGENT_RETRY_CAP_SECONDS: int = 600
    # Re-audit M12: cluster-wide cap on concurrent LLM calls per provider,
    # held as Redis leases so a crashed holder frees its slot when its lease
    # lapses. 0 disables the cluster bound; the per-process bound
    # (LLM_MAX_CONCURRENT_ANALYSES, the analysis stage's own semaphore) applies
    # either way. A waiter gives up after AI_TIMEOUT_SECONDS.
    # The lease must comfortably outlast one Redis call: at least
    # 2 x REDIS_SOCKET_TIMEOUT_SECONDS + 5 s (0 = the default, 60).
    LLM_CLUSTER_MAX_CONCURRENT: int = 4
    LLM_CLUSTER_SLOT_LEASE_SECONDS: int = 60
    AI_ANALYSIS_CACHE_TTL: int = 3600                # seconds — Redis cache TTL for analysis results
    SEMANTIC_SIMILARITY_THRESHOLD: float = 0.85      # min cosine similarity for semantic cache hit
    PROMPT_OVERHEAD_TOKENS: int = 1500               # reserved tokens for system prompt + reasoning
    SEMANTIC_CACHE_MAX_DOCUMENTS: int = 10000        # cap ChromaDB collection size

    # ── Jira ─────────────────────────────────────────────────
    JIRA_ENABLED: bool = False
    JIRA_DOMAIN: Optional[str] = None
    JIRA_EMAIL: Optional[str] = None
    JIRA_API_TOKEN: Optional[str] = None
    JIRA_DEFAULT_PROJECT_KEY: str = "QA"
    # The secret configured on the Jira webhook. Jira signs each delivery with
    # it: ``X-Hub-Signature: sha256=<hex HMAC-SHA256 of the raw body>``.
    # POST /api/v1/feedback/jira-webhook refuses every delivery (403) while
    # it is unset or a placeholder, in every environment.
    JIRA_WEBHOOK_SECRET: Optional[str] = None

    # ── Confluence (Knowledge RAG) ──────────────────────────────
    CONFLUENCE_ENABLED: bool = False
    CONFLUENCE_DOMAIN: Optional[str] = None
    CONFLUENCE_EMAIL: Optional[str] = None
    CONFLUENCE_API_TOKEN: Optional[str] = None

    # ── Splunk ────────────────────────────────────────────────
    SPLUNK_ENABLED: bool = False
    SPLUNK_BASE_URL: Optional[str] = None
    SPLUNK_API_TOKEN: Optional[str] = None
    SPLUNK_INDEX: str = "main"

    # ── OpenShift / Kubernetes ────────────────────────────────
    OCP_ENABLED: bool = False
    OCP_API_URL: Optional[str] = None
    OCP_SA_TOKEN: Optional[str] = None
    OCP_DEFAULT_NAMESPACE: str = "qa-testing"

    # ── Slack ─────────────────────────────────────────────────
    SLACK_ENABLED: bool = False
    SLACK_BOT_TOKEN: Optional[str] = None
    SLACK_WEBHOOK_URL: Optional[str] = None   # Incoming webhook — preferred over bot token
    SLACK_DEFAULT_CHANNEL: str = "#qa-alerts"

    # ── Microsoft Teams ───────────────────────────────────────
    TEAMS_ENABLED: bool = False
    TEAMS_WEBHOOK_URL: Optional[str] = None

    # ── Email / SMTP ──────────────────────────────────────────
    SMTP_ENABLED: bool = False
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "noreply@testlookup.io"
    SMTP_TLS: bool = True

    # ── Search & Indexing (OPS-03) ──────────────────────────────
    SEARCH_INDEX_BATCH_SIZE: int = 200        # documents per ChromaDB upsert batch
    SEARCH_INDEX_INCREMENTAL_LIMIT: int = 5000  # max docs per incremental reindex
    SEARCH_QUERY_TIMEOUT_MS: int = 5000       # max time for a single search query
    SEARCH_MAX_RESULTS: int = 200             # safety cap on search results

    # ── Development helpers ───────────────────────────────────
    # When true AND APP_ENV=development, a /api/v1/auth/dev-login endpoint
    # is available that issues a JWT for the admin user without credentials.
    # This lets developers access the UI immediately if seed data has not
    # been loaded yet.  Always false in staging/production.
    DEV_AUTO_LOGIN_ENABLED: bool = True

    # ── Authentication & JWT ──────────────────────────────────
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Escape hatch for the fail-CLOSED token-revocation check. Default False:
    # when the revocation store (Redis) is unreachable, authenticated requests
    # get 503 rather than being honoured unchecked. Setting this True restores
    # the old fail-open behaviour — a revoked token may keep working during a
    # Redis outage — and logs an ERROR on every use. Environment-only on
    # purpose: there is no in-app toggle for it.
    AUTH_REVOCATION_FAIL_OPEN: bool = False

    # ── Multi-factor authentication (TOTP) ────────────────────
    # Label shown in the authenticator app next to the account name.
    MFA_ISSUER_NAME: str = "TestLookup"
    # Lifetime of the short-lived challenge / enrollment tokens minted between
    # "password accepted" and "second factor accepted". These are NOT access
    # tokens — they carry their own ``type`` claim and are rejected by
    # ``get_current_user`` at the decode layer. Keep this small: it is the
    # window in which a stolen challenge is useful to someone who also has the
    # user's TOTP code.
    MFA_CHALLENGE_TTL_SECONDS: int = 300
    # Number of single-use recovery codes minted when MFA is enabled.
    MFA_RECOVERY_CODE_COUNT: int = 10
    # Breakglass: ``scripts/mfa_breakglass.py`` refuses to run unless this is
    # true in the backend environment. Mirrors SSO_ADMIN_FALLBACK_ENABLED —
    # environment-only, no in-app toggle, and every use writes a loud
    # ``MFA_BREAKGLASS_RESET`` identity event.
    MFA_BREAKGLASS_ENABLED: bool = False

    # ── SSO / SAML / SCIM ───────────────────────────────────
    SSO_ENABLED: bool = False
    SCIM_ENABLED: bool = False
    # Default SP entity ID used when generating SP metadata
    SAML_SP_ENTITY_ID: str = "https://testlookup.io/saml/metadata"
    # Base URL for constructing ACS and SLO URLs
    SAML_BASE_URL: str = "http://localhost:8000"
    # Admin fallback: allow local password login for ADMIN users even when SSO is enforced
    SSO_ADMIN_FALLBACK_ENABLED: bool = True
    # Test-connection requests reject private/link-local IdP targets by default
    # to prevent an admin-configurable URL becoming a blind SSRF primitive.
    # Self-hosted enterprises with an internal-only IdP can opt in explicitly.
    SSO_ALLOW_PRIVATE_IDP_ENDPOINTS: bool = False
    # When False (default), reject SAML assertions that carry no ``InResponseTo``
    # (i.e. IdP-initiated logins). SP-initiated flow only — the ACS binds each
    # response to a single-use request id minted at /login-url. Operators who
    # genuinely need IdP-initiated SSO can opt in, but they lose the
    # request-binding leg of replay protection (the assertion-ID single-use
    # cache + audience + recipient + signature checks still apply).
    SAML_ALLOW_IDP_INITIATED: bool = False
    # Allowed IdP/SP clock drift (seconds) applied to SAML assertion
    # NotBefore / NotOnOrAfter so minor NTP skew doesn't reject valid logins.
    SAML_CLOCK_SKEW_SECONDS: int = 60
    # Hard ceiling on the role an IdP group-mapping (SSO JIT) or SCIM provision
    # may grant. Resolved roles above this are clamped down and a WARNING audit
    # is emitted. Default ADMIN honours deliberate admin-configured mappings;
    # set lower (e.g. QA_LEAD) to refuse IdP-driven admin grants entirely.
    SSO_MAX_PROVISIONED_ROLE: str = "ADMIN"

    @field_validator("LLM_CLUSTER_SLOT_LEASE_SECONDS")
    @classmethod
    def _validate_llm_cluster_slot_lease(cls, v):
        # QA-B45-R2-3: a holder renews every lease/3 and each renew is one
        # Redis call that can hang for the socket timeout. A lease that a
        # single stalled call can outlast leaves no room to renew again or
        # to stop the holder before a second one is admitted.
        minimum = 2 * REDIS_SOCKET_TIMEOUT_SECONDS + 5
        if v and v < minimum:
            raise ValueError(
                f"LLM_CLUSTER_SLOT_LEASE_SECONDS must be 0 (use the built-in 60) or at least {minimum} "
                f"(2 x the {REDIS_SOCKET_TIMEOUT_SECONDS}s Redis socket timeout + 5s); got {v}"
            )
        return v

    @field_validator("SSO_MAX_PROVISIONED_ROLE")
    @classmethod
    def _validate_sso_max_provisioned_role(cls, v):
        # Fail fast on a typo'd role. Otherwise the SSO/SCIM role clamp silently
        # falls back to the least-restrictive ceiling (ADMIN) at request time,
        # defeating the guard. Normalise to upper-case.
        valid_roles = {"VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"}
        normalized = (v or "").strip().upper()
        if normalized not in valid_roles:
            raise ValueError(
                f"SSO_MAX_PROVISIONED_ROLE must be one of {sorted(valid_roles)} (got {v!r})"
            )
        return normalized

    # ── Fine-Tuning / Continuous Learning ────────────────────
    FINETUNE_ENABLED: bool = False                    # master switch
    FINETUNE_CLASSIFIER_MIN_EXAMPLES: int = 500       # trigger Track 1 classifier fine-tune
    FINETUNE_REASONING_MIN_EXAMPLES: int = 2000       # trigger Track 2 full ReAct fine-tune
    FINETUNE_EMBED_MIN_PAIRS: int = 1000              # trigger Track 3 embedding fine-tune
    FINETUNE_INCREMENTAL_TRIGGER: int = 200           # re-trigger after N new verified examples
    FINETUNE_EVAL_HOLDOUT: float = 0.10               # fraction held out for evaluation
    FINETUNE_MIN_ACCURACY_GAIN: float = 0.02          # candidate must beat current model by ≥2%
    FINETUNE_EXPORT_BUCKET: str = "training-data"     # MinIO bucket for JSONL exports
    FINETUNE_OPENAI_SUFFIX: str = "testlookup"         # suffix for OpenAI fine-tune job names
    CLASSIFIER_CONFIDENCE_THRESHOLD: int = 85         # min confidence to trust fast classifier
    CLASSIFIER_MODEL: Optional[str] = None            # None = use LLM_MODEL

    # ── Deep Investigation ────────────────────────────────────────────────────────
    DEEP_INVESTIGATION_ENABLED: bool = True
    RELEASE_PASS_RATE_THRESHOLD: float = 90.0     # minimum pass rate to consider GO
    # NOTE: DEEP_CLUSTER_THRESHOLD (Jaccard similarity) and
    # DEEP_MAX_CLUSTERS_PER_RUN were removed 2026-08-08. They described a
    # similarity-clustering design that was never built: flaky_investigator's
    # ``cluster_failures`` groups by *exact* error signature and stack
    # fingerprint, so there was no similarity threshold to tune and no cluster
    # list to cap. Both read as live tuning knobs and did nothing.

    # ── Knowledge-Grounded Test Generation (RAG) ───────────────────────────────
    KNOWLEDGE_RAG_ENABLED: bool = False
    KNOWLEDGE_SYNC_TIMEOUT_SECONDS: int = 60
    KNOWLEDGE_MAX_SOURCES_PER_PROJECT: int = 100
    KNOWLEDGE_DOCS_BUCKET: str = "knowledge-docs"
    KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS: int = 24
    KNOWLEDGE_STALE_THRESHOLD_URL_HOURS: int = 168
    KNOWLEDGE_CHUNK_TARGET_TOKENS: int = 400
    KNOWLEDGE_CHUNK_MAX_TOKENS: int = 800
    KNOWLEDGE_CHUNK_OVERLAP_TOKENS: int = 50
    KNOWLEDGE_RESYNC_BATCH_CAP: int = 50
    # A source is marked SYNCING and COMMITTED before a fetch+chunk+embed
    # that can outlive the process (OOM kill, pod eviction, Celery hard
    # time limit). Those deaths run no `except` block, so the row keeps a
    # SYNCING it can never leave -- and list_stale_sources excludes SYNCING
    # to prevent concurrent syncs, so nothing ever picks it up again.
    # Must stay ABOVE celery task_time_limit (1860s = 31min) or the reaper
    # would fail a sync that is merely slow, not dead.
    KNOWLEDGE_SYNC_STUCK_MINUTES: int = 45

    # ── Analysis Mode (LLM-free operation) ──────────────────────────────────────
    # Controls which engine processes test results.
    #   "llm"   — full LangChain ReAct agent (requires running LLM)
    #   "ml"    — scikit-learn ML classifiers (no LLM needed, needs trained model)
    #   "rules" — pattern matching + statistical heuristics (zero dependencies)
    #   "auto"  — ML if trained model available, else LLM if reachable, else rules
    ANALYSIS_MODE: str = "auto"
    ML_MODEL_DIR: str = "models"                     # directory for trained .joblib artifacts
    # Re-audit M14: with several pods, ML_MODEL_DIR is a pod-local cache and the
    # object store is the source of truth. A retrain publishes there; every pod
    # pulls new versions (services/ml/model_store.py). On in the k8s base config.
    ML_MODEL_SYNC_ENABLED: bool = False
    ML_MODEL_STORE_PREFIX: str = "ml-models/"
    ML_MIN_TRAINING_SAMPLES: int = 200               # minimum labeled samples before ML activates
    ML_RETRAIN_ENABLED: bool = True                  # enable nightly Celery-beat retraining
    ML_ACCURACY_THRESHOLD: float = 0.80              # minimum accuracy to deploy a new model
    # ── Label integrity (AI-F1): break the circular LLM→ML pseudo-label loop ──
    # LLM pseudo-labels (high-confidence analyses with no human confirmation) are
    # capped as a fraction of the final training set and down-weighted so human
    # labels dominate what the classifier learns.
    ML_PSEUDO_LABEL_CAP: float = 0.30                # max fraction of training set from LLM pseudo-labels
    ML_PSEUDO_LABEL_WEIGHT: float = 0.3              # sample_weight for pseudo-labels (human labels = 1.0)
    ML_HUMAN_LABEL_FLOOR: int = 50                   # below this many human labels, ML reports bootstrap mode

    # ── Anomaly Detection Tunables ────────────────────────────────────────────────
    # Pass-rate regression
    ANOMALY_REGRESSION_THRESHOLD: float = 10.0   # % drop vs median baseline to flag regression
    ANOMALY_MIN_HISTORY_RUNS: int = 3             # min prior runs required before regression check

    # Flakiness detection
    ANOMALY_FLAKY_WINDOW_RUNS: int = 10           # max history entries evaluated per fingerprint
    ANOMALY_FLAKY_LOOKBACK_DAYS: int = 30         # how far back to look for history

    # Performance spike detection (Median/MAD based)
    ANOMALY_PERF_HISTORY_DAYS: int = 30           # history window for duration baselines
    ANOMALY_MIN_PERF_SAMPLES: int = 5             # min samples before flagging a duration spike
    ANOMALY_PERF_SPIKE_MULTIPLIER: float = 2.0    # current must be > multiplier × median to flag
    ANOMALY_PERF_MIN_ABSOLUTE_DELTA_MS: int = 1000  # minimum absolute increase in ms to flag spike

    # LLM summary
    ANOMALY_SUMMARY_MAX_ITEMS: int = 8            # max anomaly items to include in LLM prompt

    # ── Risk Scoring Model Weights (must sum to 1.0) ──────────────
    # Each dimension is scored 0-100; composite = Σ(dim_i × weight_i)
    # Defaults match the original hardcoded values in release_risk_agent.py
    RISK_WEIGHT_USER_IMPACT:       float = 0.25
    RISK_WEIGHT_ENV_SENSITIVITY:   float = 0.10
    RISK_WEIGHT_REPRODUCIBILITY:   float = 0.15
    RISK_WEIGHT_REGRESSION_LIKELY: float = 0.20
    RISK_WEIGHT_HIST_RECURRENCE:   float = 0.10
    RISK_WEIGHT_BLAST_RADIUS:      float = 0.15
    RISK_WEIGHT_DIAGNOSIS_CONF:    float = 0.05
    PROMETHEUS_URL: Optional[str] = None          # e.g. http://prometheus:9090
    GITHUB_TOKEN: Optional[str] = None            # GitHub PAT for build change lookup
    GITHUB_REPO: Optional[str] = None             # e.g. "org/repo"

    # ── Commit attribution (Epic 8) / TIA corpus (Epic 10) ────
    # How many commits in a resolved range get their changed-file list
    # fetched. Each one is a separate GitHub API call, so this is a direct
    # rate-limit knob (see commit_attribution_service._file_fetch_limit for
    # the arithmetic). Hard-capped at the range size (100) by the service.
    # 25 is the safe default for suspect ranking; deployments building a
    # test-impact-analysis corpus want it higher, because a commit stored
    # with ``files: []`` contributes nothing to a path→test model.
    COMMIT_RANGE_FILE_FETCH_LIMIT: int = 25

    # ── Outbound HTTP / TLS ───────────────────────────────────
    # Default to strict certificate verification. Operators with self-signed
    # internal CAs should set HTTP_CA_BUNDLE to the PEM path instead of
    # disabling verification. HTTP_VERIFY_TLS=false is intended only for
    # isolated lab environments and emits a warning at startup.
    HTTP_VERIFY_TLS: bool = True
    HTTP_CA_BUNDLE: Optional[str] = None

    # ── Webhook Security ──────────────────────────────────────
    WEBHOOK_SECRET: str = "change-me-webhook-secret"
    # When true, POST /ws/events/{run_id} accepts ONLY a project-scoped API key
    # and refuses the shared webhook secret. The secret authenticates a caller
    # but names no tenant, so it cannot express "may write to THIS project";
    # a project-scoped key derives the project server-side. Defaults False so
    # existing direct integrations keep working; set True to close the shared
    # secret path outright (re-audit H1).
    #: Peer addresses allowed to supply X-Forwarded-For / X-Forwarded-Proto.
    #:
    #: Comma-separated addresses OR CIDR networks. Empty means trust nothing:
    #: request.client.host stays the immediate peer, which is correct for a
    #: deployment that has not declared a proxy.
    #:
    #: NOT named FORWARDED_ALLOW_IPS on purpose. gunicorn reads that name
    #: itself, validates it with ipaddress.ip_address() -- which rejects every
    #: network -- and does so while building its Config, before the config file
    #: is read. A CIDR under that name therefore kills the process at startup
    #: with "does not appear to be an IPv4 or IPv6 address" and no way for
    #: gunicorn_conf.py to intervene. Applying the boundary in the app instead
    #: works identically under gunicorn and under a bare uvicorn.
    TRUSTED_PROXY_IPS: str = ""

    LIVE_EVENTS_REQUIRE_PROJECT_KEY: bool = True

    # ── Observability ─────────────────────────────────────────
    # OpenTelemetry
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "testlookup"
    # OTLP HTTP collector endpoint, e.g. "http://jaeger:4318"
    # When empty: stdout in development only; elsewhere nothing is exported
    # and one warning is logged (core/tracing.py, re-audit N22)
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    # Prometheus metrics endpoint
    METRICS_ENABLED: bool = True

    # ── Logging ───────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_ignore_empty=True,
        populate_by_name=True,
    )

    @property
    def CORS_ORIGINS(self) -> List[str]:
        value = (self.CORS_ORIGINS_RAW or "").strip()
        if not value:
            return ["http://localhost:3000", "http://localhost:5173"]

        try:
            parsed = _json.loads(value)
            if isinstance(parsed, list):
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
        except (_json.JSONDecodeError, ValueError):
            pass

        if value.startswith("'") and value.endswith("'"):
            inner = value[1:-1].strip()
            try:
                parsed = _json.loads(inner)
                if isinstance(parsed, list):
                    return [str(origin).strip() for origin in parsed if str(origin).strip()]
            except (_json.JSONDecodeError, ValueError):
                value = inner

        return [origin.strip() for origin in value.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def chroma_host_url(self) -> str:
        return f"http://{self.CHROMA_HOST}:{self.CHROMA_PORT}"

    @property
    def public_base_url(self) -> str:
        """Externally-reachable dashboard URL for notification links.

        Resolution order:
          1. ``PUBLIC_BASE_URL`` env var (explicit, recommended in production).
          2. First entry of ``CORS_ORIGINS`` (typically the ingress hostname).
          3. ``http://localhost:3000`` (development fallback).

        Always returned without a trailing slash so callers can append paths
        directly: ``f"{settings.public_base_url}/runs/{run_id}"``.
        """
        explicit = (self.PUBLIC_BASE_URL or "").strip().rstrip("/")
        if explicit:
            return explicit
        origins = self.CORS_ORIGINS
        if origins:
            return origins[0].rstrip("/")
        return "http://localhost:3000"


    def validate_production_secrets(self) -> list[str]:
        """
        Check for insecure default secrets in production/staging.
        Returns a list of warning messages. Empty list = all clear.
        Called at startup to fail-fast on misconfiguration.
        """
        warnings: list[str] = []
        if self.APP_ENV in ("production", "staging"):
            if _is_placeholder_secret(self.JWT_SECRET_KEY):
                warnings.append("CRITICAL: JWT_SECRET_KEY is set to the default — change it immediately")
            if _is_placeholder_secret(self.APP_SECRET_KEY):
                warnings.append("CRITICAL: APP_SECRET_KEY is set to the default — secrets will not be safely encrypted")
            if _is_placeholder_secret(self.WEBHOOK_SECRET):
                # CRITICAL, not WARNING (re-audit H1). This secret is the only
                # credential in front of POST /ws/events/{run_id}, which injects
                # live test results into a caller-named project. Booting
                # production with the literal, version-controlled default let
                # anyone who read the source forge results for any tenant.
                warnings.append(
                    "CRITICAL: WEBHOOK_SECRET is set to the default — live-event and "
                    "webhook endpoints accept anyone who read the source"
                )
            # Re-audit N12. .env.example ships
            # MONGO_URI=mongodb://testlookup:change-me-to-a-strong-password@...
            # and nothing checked it, so copying the example file and deploying
            # booted production on a published database password -- the same
            # path the JWT/APP/WEBHOOK checks above were fixed for.
            mongo_password = _uri_password(self.MONGO_URI)
            if mongo_password is not None and _is_placeholder_secret(mongo_password):
                warnings.append(
                    "CRITICAL: MONGO_URI carries a placeholder password — the "
                    "database password is the one published in .env.example"
                )
            # WARNING, not CRITICAL: the Jira webhook fails closed on its own (403
            # for every delivery), so an unset secret denies rather than exposes,
            # and refusing startup would break every deployment without Jira.
            if self.JIRA_ENABLED and _is_placeholder_secret(self.JIRA_WEBHOOK_SECRET or ""):
                warnings.append(
                    "WARNING: JIRA_WEBHOOK_SECRET is unset — POST "
                    "/api/v1/feedback/jira-webhook refuses every Jira delivery"
                )
            if self.DEV_AUTO_LOGIN_ENABLED:
                warnings.append("CRITICAL: DEV_AUTO_LOGIN_ENABLED is True in production — disable it")
            if self.SSO_ENABLED and self.SAML_BASE_URL == "http://localhost:8000":
                warnings.append("WARNING: SAML_BASE_URL is set to localhost — update it for production")
            # S4-audit S11: a wildcard CORS origin lets any site make
            # credentialed cross-origin calls to the API. CORS_ORIGINS never
            # defaults to '*' (it falls back to localhost), so this only fires
            # when an operator set it explicitly.
            if any("*" in origin for origin in self.CORS_ORIGINS):
                warnings.append(
                    "WARNING: CORS_ORIGINS contains a wildcard '*' — set explicit allowed origins"
                )
        return warnings

    def critical_security_failures(self) -> list[str]:
        """
        CRITICAL-severity subset of validate_production_secrets().

        Drives the startup fail-fast guard. Returns [] outside production/staging
        (validate_production_secrets only emits there), so booting dev is never blocked.
        Staging is treated like production: a CRITICAL default secret refuses startup.
        """
        return [w for w in self.validate_production_secrets() if w.startswith("CRITICAL")]


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
