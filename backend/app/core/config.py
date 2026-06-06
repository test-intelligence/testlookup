"""
TestLookup — Application Configuration
All settings loaded from environment variables with sensible defaults.
"""
import json as _json
import os
from functools import lru_cache
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# BUG-001: disable ChromaDB's anonymous telemetry process-wide. This module is
# imported at startup, before any ``chromadb.HttpClient`` is constructed, so the
# setting reaches every Chroma client (agent memory, knowledge chunking, RAG,
# defect promotion, conversation). Left enabled, Chroma's bundled posthog
# telemetry floods the AI worker logs with
#   "Failed to send telemetry event ClientStartEvent: capture() takes 1
#    positional argument but 3 were given"
# (a posthog-version mismatch) — and an outbound phone-home is wrong for this
# offline-first, privacy-focused app. ``setdefault`` lets an explicit env win.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


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
    CORS_ORIGINS_RAW: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        alias="CORS_ORIGINS",
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
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_WORKER_CONCURRENCY: int = 4          # Set to 16-32 in production for 100+ concurrent users

    # ── Performance / Scalability tunables ────────────────────
    # PostgreSQL pool (None = auto-size by environment)
    PG_POOL_SIZE: Optional[int] = None          # dev=5, staging=15, prod=20
    PG_MAX_OVERFLOW: Optional[int] = None       # dev=10, staging=30, prod=50
    PG_POOL_RECYCLE: int = 1800                 # seconds — recycle idle connections
    PG_POOL_TIMEOUT: int = 30                   # seconds — wait for available connection

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

    # WebSocket limits
    WS_MAX_CONNECTIONS_PER_PROJECT: int = 500
    WS_MAX_TOTAL_CONNECTIONS: int = 5000
    WS_BROADCAST_TIMEOUT: float = 5.0           # seconds before dropping a dead connection

    # ── Live-stream ingestion gate (Phase 1, 2026-05-16) ──────
    # See docs/SCALABLE_INGESTION_DESIGN.md. Both limits operate per
    # project, per minute. Set to 0 to disable.
    INGEST_RATE_LIMIT_PER_MINUTE: int = 200       # batches per project per minute
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
    # ``hash(project_id) mod N``. Increase to widen horizontal worker
    # capacity without touching the consumer code. Set to 0 to fall
    # back to the legacy single-queue routing (used by tests).
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
    LLM_PROVIDER: Literal["ollama", "lmstudio", "localai", "vllm", "openai", "gemini", "anthropic"] = "ollama"
    LLM_MODEL: str = "qwen2.5:7b"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LMSTUDIO_BASE_URL: str = "http://localhost:1234/v1"
    LOCALAI_BASE_URL: str = "http://localhost:8080/v1"
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    # ── Embedding ─────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "ollama"
    EMBEDDING_MODEL: str = "nomic-embed-text"

    # ── ChromaDB ──────────────────────────────────────────────
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8001
    CHROMA_COLLECTION: str = "testlookup_embeddings"

    # ── AI Agent ─────────────────────────────────────────────
    AI_OFFLINE_MODE: bool = True
    AI_CONFIDENCE_THRESHOLD: int = 80
    AI_MAX_RETRIES: int = 3
    AI_TIMEOUT_SECONDS: int = 300
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

    # ── SSO / SAML / SCIM ───────────────────────────────────
    SSO_ENABLED: bool = False
    SCIM_ENABLED: bool = False
    # Default SP entity ID used when generating SP metadata
    SAML_SP_ENTITY_ID: str = "https://testlookup.io/saml/metadata"
    # Base URL for constructing ACS and SLO URLs
    SAML_BASE_URL: str = "http://localhost:8000"
    # Admin fallback: allow local password login for ADMIN users even when SSO is enforced
    SSO_ADMIN_FALLBACK_ENABLED: bool = True
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
    DEEP_CLUSTER_THRESHOLD: float = 0.75          # Jaccard similarity threshold for clustering
    DEEP_MAX_CLUSTERS_PER_RUN: int = 20           # cap clusters to avoid overload

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

    # ── Analysis Mode (LLM-free operation) ──────────────────────────────────────
    # Controls which engine processes test results.
    #   "llm"   — full LangChain ReAct agent (requires running LLM)
    #   "ml"    — scikit-learn ML classifiers (no LLM needed, needs trained model)
    #   "rules" — pattern matching + statistical heuristics (zero dependencies)
    #   "auto"  — ML if trained model available, else LLM if reachable, else rules
    ANALYSIS_MODE: str = "auto"
    ML_MODEL_DIR: str = "models"                     # directory for trained .joblib artifacts
    ML_MIN_TRAINING_SAMPLES: int = 200               # minimum labeled samples before ML activates
    ML_RETRAIN_ENABLED: bool = True                  # enable nightly Celery-beat retraining
    ML_ACCURACY_THRESHOLD: float = 0.80              # minimum accuracy to deploy a new model

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

    # ── Outbound HTTP / TLS ───────────────────────────────────
    # Default to strict certificate verification. Operators with self-signed
    # internal CAs should set HTTP_CA_BUNDLE to the PEM path instead of
    # disabling verification. HTTP_VERIFY_TLS=false is intended only for
    # isolated lab environments and emits a warning at startup.
    HTTP_VERIFY_TLS: bool = True
    HTTP_CA_BUNDLE: Optional[str] = None

    # ── Webhook Security ──────────────────────────────────────
    WEBHOOK_SECRET: str = "change-me-webhook-secret"

    # ── Observability ─────────────────────────────────────────
    # OpenTelemetry
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "testlookup"
    # OTLP HTTP collector endpoint, e.g. "http://jaeger:4318"
    # When empty, spans are written to stdout (development fallback)
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
            if self.JWT_SECRET_KEY in ("change-me-jwt-secret", ""):
                warnings.append("CRITICAL: JWT_SECRET_KEY is set to the default — change it immediately")
            if self.APP_SECRET_KEY in ("change-me-in-production", ""):
                warnings.append("CRITICAL: APP_SECRET_KEY is set to the default — secrets will not be safely encrypted")
            if self.WEBHOOK_SECRET in ("change-me-webhook-secret", ""):
                warnings.append("WARNING: WEBHOOK_SECRET is set to the default — webhook endpoints are not secured")
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
