"""
TestLookup — Application Configuration
All settings loaded from environment variables with sensible defaults.
"""
import json as _json
from functools import lru_cache
from typing import List, Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — loaded from environment variables."""

    # ── Application ─────────────────────────────────────────
    APP_NAME: str = "TestLookup"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_SECRET_KEY: str = "change-me-in-production"
    APP_DEBUG: bool = False
    APP_VERSION: str = "0.0.1"
    CORS_ORIGINS_RAW: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        alias="CORS_ORIGINS",
    )

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
    CELERY_WORKER_CONCURRENCY: int = 4

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

    # ── LLM Provider ─────────────────────────────────────────
    LLM_PROVIDER: Literal["ollama", "lmstudio", "localai", "vllm", "openai", "gemini"] = "ollama"
    LLM_MODEL: str = "qwen2.5:7b"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LMSTUDIO_BASE_URL: str = "http://localhost:1234/v1"
    LOCALAI_BASE_URL: str = "http://localhost:8080/v1"
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None

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

    # ── Jira ─────────────────────────────────────────────────
    JIRA_ENABLED: bool = False
    JIRA_DOMAIN: Optional[str] = None
    JIRA_EMAIL: Optional[str] = None
    JIRA_API_TOKEN: Optional[str] = None
    JIRA_DEFAULT_PROJECT_KEY: str = "QA"

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
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
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
        return warnings


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
