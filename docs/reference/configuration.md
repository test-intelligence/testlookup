# Environment settings reference

[Documentation home](../README.md) · [Regeneration](../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../handoff/verification.md).

Source declarations only; no `.env` or running secrets are read. Compose/Helm/Kustomize can override these defaults. `Field` aliases and constraints are retained. For production requirements and profiles see [deployment](../operations/deployment.md).

| Setting | Type | Source default/constraints |
|---|---|---|
| `APP_NAME` | `str` | `'TestLookup'` |
| `APP_ENV` | `Literal['development', 'staging', 'production']` | `'development'` |
| `APP_SECRET_KEY` | `str` | `'change-me-in-production'` |
| `APP_SECRET_KEY_PREVIOUS` | `Optional[str]` | `None` |
| `APP_DEBUG` | `bool` | `False` |
| `APP_VERSION` | `str` | `'0.0.1'` |
| `BUILD_REVISION` | `str` | `''` |
| `BUILD_DATE` | `str` | `''` |
| `CORS_ORIGINS_RAW` | `str` | `Field(default='http://localhost:3000,http://localhost:5173', validation_alias=AliasChoices('CORS_ORIGINS_RAW', 'CORS_ORIGINS'))` |
| `PUBLIC_BASE_URL` | `str` | `''` |
| `POSTGRES_HOST` | `str` | `'localhost'` |
| `POSTGRES_PORT` | `int` | `5433` |
| `POSTGRES_DB` | `str` | `'testlookup'` |
| `POSTGRES_USER` | `str` | `'testlookup_user'` |
| `POSTGRES_PASSWORD` | `str` | `''` |
| `DATABASE_URL` | `str` | `''` |
| `MONGO_HOST` | `str` | `'localhost'` |
| `MONGO_PORT` | `int` | `27017` |
| `MONGO_DB` | `str` | `'testlookup_logs'` |
| `MONGO_URI` | `str` | `'mongodb://localhost:27017'` |
| `STORAGE_BACKEND` | `Literal['minio', 's3', 'local']` | `'minio'` |
| `LOCAL_STORAGE_PATH` | `str` | `'/tmp/testlookup_data'` |
| `MINIO_ENDPOINT` | `str` | `'localhost:9000'` |
| `MINIO_ACCESS_KEY` | `str` | `'testlookup_minio'` |
| `MINIO_SECRET_KEY` | `str` | `''` |
| `MINIO_BUCKET_NAME` | `str` | `'test-telemetry'` |
| `MINIO_USE_SSL` | `bool` | `False` |
| `REDIS_PASSWORD` | `str` | `''` |
| `REDIS_URL` | `str` | `'redis://localhost:6379/0'` |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` |
| `CELERY_RESULT_BACKEND` | `str` | `'redis://localhost:6379/1'` |
| `CELERY_WORKER_CONCURRENCY` | `int` | `4` |
| `PG_POOL_SIZE` | `Optional[int]` | `Field(default=None, ge=1)` |
| `PG_MAX_OVERFLOW` | `Optional[int]` | `Field(default=None, ge=0)` |
| `PG_POOL_RECYCLE` | `int` | `Field(default=1800, ge=1)` |
| `PG_POOL_TIMEOUT` | `int` | `Field(default=30, ge=1)` |
| `PG_PROCESS_ROLE` | `Literal['api', 'worker', 'operation']` | `'operation'` |
| `PG_PROCESSES_PER_POD` | `int` | `Field(default=1, ge=1)` |
| `PG_FLEET_MAX_CONNECTIONS` | `int` | `Field(default=400, ge=1)` |
| `PG_FLEET_OPERATIONAL_RESERVE` | `int` | `Field(default=50, ge=1)` |
| `PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS` | `int` | `Field(default=3, ge=0)` |
| `PG_FLEET_RESERVED_CONNECTIONS` | `int` | `Field(default=0, ge=0)` |
| `PG_FLEET_MIGRATION_CONNECTIONS` | `int` | `Field(default=1, ge=1)` |
| `PG_FLEET_REQUIRED_CONNECTIONS` | `int` | `Field(default=273, ge=1)` |
| `MONGO_MAX_POOL_SIZE` | `int` | `50` |
| `MONGO_MIN_POOL_SIZE` | `int` | `5` |
| `MONGO_SOCKET_TIMEOUT_MS` | `int` | `30000` |
| `MONGO_MAX_IDLE_TIME_MS` | `int` | `60000` |
| `S3_MAX_POOL_CONNECTIONS` | `int` | `50` |
| `INGESTION_S3_CONCURRENCY` | `int` | `10` |
| `LLM_MAX_CONCURRENT_ANALYSES` | `int` | `3` |
| `AGENT_INVOKE_SYNC_CONCURRENCY` | `int` | `4` |
| `AGENT_INVOKE_SYNC_WAIT_SECONDS` | `int` | `25` |
| `AGENT_INVOKE_STREAM_TICKET_SECONDS` | `int` | `60` |
| `WS_MAX_CONNECTIONS_PER_PROJECT` | `int` | `500` |
| `WS_MAX_TOTAL_CONNECTIONS` | `int` | `5000` |
| `WS_BROADCAST_TIMEOUT` | `float` | `5.0` |
| `INGEST_RATE_LIMIT_PER_MINUTE` | `int` | `200` |
| `INGEST_EVENT_RATE_LIMIT_PER_MINUTE` | `int` | `20000` |
| `MAX_ARCHIVE_UNCOMPRESSED_BYTES` | `int` | `200 * 1024 * 1024` |
| `MAX_ARCHIVE_ENTRIES` | `int` | `5000` |
| `MAX_ARCHIVE_ENTRY_BYTES` | `int` | `50 * 1024 * 1024` |
| `MAX_ARCHIVE_RATIO` | `int` | `100` |
| `FORM_URLENCODED_MAX_BYTES` | `int` | `64 * 1024` |
| `FORM_URLENCODED_ACS_MAX_BYTES` | `int` | `2 * 1024 * 1024` |
| `INGEST_MAX_RESULTS_PER_UPLOAD` | `int` | `MAX_RESULTS_PER_INGEST` |
| `INGEST_REDIS_MEMORY_THRESHOLD_PCT` | `float` | `75.0` |
| `INGEST_REDIS_MEMORY_ABSOLUTE_BYTES` | `int` | `0` |
| `LIVE_BUFFER_MAX_EVENTS_PER_RUN` | `int` | `50000` |
| `PERSIST_LIVE_BULK_INSERT_CHUNK` | `int` | `1000` |
| `LIVE_INGEST_SHARD_COUNT` | `int` | `8` |
| `LIVE_SESSION_DRAIN_ENABLED` | `bool` | `True` |
| `LIVE_SESSION_DRAIN_BATCH_SIZE` | `int` | `5000` |
| `AI_PIPELINE_DEBOUNCE_ENABLED` | `bool` | `True` |
| `AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS` | `int` | `60` |
| `AI_PIPELINE_DEADLINE_SECONDS` | `int` | `1500` |
| `AGENT_PIPELINE_ALERT_GRACE_SECONDS` | `int` | `300` |
| `HIGH_VOLUME_AUTO_DETECT_ENABLED` | `bool` | `True` |
| `HIGH_VOLUME_TESTS_PER_MINUTE` | `int` | `1000` |
| `HIGH_VOLUME_CONSECUTIVE_MINUTES` | `int` | `3` |
| `HIGH_VOLUME_SAMPLE_EVERY_N` | `int` | `2` |
| `INGESTION_DLQ_ENABLED` | `bool` | `True` |
| `CANONICAL_DELETION_WINDOW_RUNS` | `int` | `5` |
| `LLM_PROVIDER` | `Literal['ollama', 'lmstudio', 'localai', 'vllm', 'openai', 'gemini', 'anthropic', 'openrouter']` | `'ollama'` |
| `LLM_MODEL` | `str` | `'qwen2.5:3b-instruct-q5_K_M'` |
| `LLM_TEMPERATURE` | `float` | `0.1` |
| `LLM_MAX_TOKENS` | `int` | `4096` |
| `OLLAMA_BASE_URL` | `str` | `'http://localhost:11434'` |
| `OLLAMA_NUM_CTX` | `int` | `8192` |
| `LMSTUDIO_BASE_URL` | `str` | `'http://localhost:1234/v1'` |
| `LOCALAI_BASE_URL` | `str` | `'http://localhost:8080/v1'` |
| `VLLM_BASE_URL` | `str` | `'http://localhost:8000/v1'` |
| `OPENAI_API_KEY` | `Optional[str]` | `None` |
| `GOOGLE_API_KEY` | `Optional[str]` | `None` |
| `ANTHROPIC_API_KEY` | `Optional[str]` | `None` |
| `OPENROUTER_API_KEY` | `Optional[str]` | `None` |
| `OPENROUTER_BASE_URL` | `str` | `'https://openrouter.ai/api/v1'` |
| `OPENROUTER_SITE_URL` | `str` | `'https://github.com/anandtopu/testlookup'` |
| `OPENROUTER_APP_NAME` | `str` | `'TestLookup'` |
| `LLM_PRICE_OVERRIDES` | `Optional[str]` | `None` |
| `EMBEDDING_PROVIDER` | `str` | `'ollama'` |
| `EMBEDDING_MODEL` | `str` | `'nomic-embed-text:v1.5'` |
| `CHROMA_HOST` | `str` | `'localhost'` |
| `CHROMA_PORT` | `int` | `8001` |
| `CHROMA_COLLECTION` | `str` | `'testlookup_embeddings'` |
| `CHROMA_ONNX_MODEL_DIR` | `str` | `''` |
| `AI_OFFLINE_MODE` | `bool` | `True` |
| `REVIEW_GATE_ENFORCED` | `bool` | `False` |
| `AI_LLM_PROVIDER_ALLOWLIST` | `str` | `''` |
| `AI_LLM_ALLOWED_BASE_URLS` | `str` | `''` |
| `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` | `str` | `''` |
| `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` | `str` | `''` |
| `AGENT_MEMORY_RETENTION_DAYS` | `int` | `365` |
| `AI_CONFIDENCE_THRESHOLD` | `int` | `80` |
| `AIQ_GAP_REFINEMENT_ENABLED` | `bool` | `False` |
| `AIQ_CONTRACT_VALIDATION_ENABLED` | `bool` | `False` |
| `AIQ_CHANGE_OWNERSHIP_ENABLED` | `bool` | `False` |
| `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | `bool` | `False` |
| `AI_REPORT_EVAL_ALLOW_CALLER_CORPUS` | `bool` | `False` |
| `AI_MAX_RETRIES` | `int` | `3` |
| `AI_TIMEOUT_SECONDS` | `int` | `300` |
| `AGENT_PIPELINE_MAX_ATTEMPTS` | `int` | `5` |
| `AGENT_MAX_ATTEMPTS_CEILING` | `int` | `10` |
| `AGENT_MAX_TIMEOUT_CEILING` | `int` | `600` |
| `AGENT_RETRY_BASE_SECONDS` | `int` | `30` |
| `AGENT_RETRY_CAP_SECONDS` | `int` | `600` |
| `LLM_CLUSTER_MAX_CONCURRENT` | `int` | `4` |
| `LLM_CLUSTER_SLOT_LEASE_SECONDS` | `int` | `60` |
| `AI_ANALYSIS_CACHE_TTL` | `int` | `3600` |
| `SEMANTIC_SIMILARITY_THRESHOLD` | `float` | `0.85` |
| `PROMPT_OVERHEAD_TOKENS` | `int` | `1500` |
| `SEMANTIC_CACHE_MAX_DOCUMENTS` | `int` | `10000` |
| `JIRA_ENABLED` | `bool` | `False` |
| `JIRA_DOMAIN` | `Optional[str]` | `None` |
| `JIRA_EMAIL` | `Optional[str]` | `None` |
| `JIRA_API_TOKEN` | `Optional[str]` | `None` |
| `JIRA_DEFAULT_PROJECT_KEY` | `str` | `'QA'` |
| `JIRA_WEBHOOK_SECRET` | `Optional[str]` | `None` |
| `CONFLUENCE_ENABLED` | `bool` | `False` |
| `CONFLUENCE_DOMAIN` | `Optional[str]` | `None` |
| `CONFLUENCE_EMAIL` | `Optional[str]` | `None` |
| `CONFLUENCE_API_TOKEN` | `Optional[str]` | `None` |
| `SPLUNK_ENABLED` | `bool` | `False` |
| `SPLUNK_BASE_URL` | `Optional[str]` | `None` |
| `SPLUNK_API_TOKEN` | `Optional[str]` | `None` |
| `SPLUNK_INDEX` | `str` | `'main'` |
| `OCP_ENABLED` | `bool` | `False` |
| `OCP_API_URL` | `Optional[str]` | `None` |
| `OCP_SA_TOKEN` | `Optional[str]` | `None` |
| `OCP_DEFAULT_NAMESPACE` | `str` | `'qa-testing'` |
| `SLACK_ENABLED` | `bool` | `False` |
| `SLACK_BOT_TOKEN` | `Optional[str]` | `None` |
| `SLACK_WEBHOOK_URL` | `Optional[str]` | `None` |
| `SLACK_DEFAULT_CHANNEL` | `str` | `'#qa-alerts'` |
| `TEAMS_ENABLED` | `bool` | `False` |
| `TEAMS_WEBHOOK_URL` | `Optional[str]` | `None` |
| `SMTP_ENABLED` | `bool` | `False` |
| `SMTP_HOST` | `str` | `'localhost'` |
| `SMTP_PORT` | `int` | `587` |
| `SMTP_USER` | `Optional[str]` | `None` |
| `SMTP_PASSWORD` | `Optional[str]` | `None` |
| `SMTP_FROM` | `str` | `'noreply@testlookup.io'` |
| `SMTP_TLS` | `bool` | `True` |
| `SEARCH_INDEX_BATCH_SIZE` | `int` | `200` |
| `SEARCH_INDEX_INCREMENTAL_LIMIT` | `int` | `5000` |
| `SEARCH_QUERY_TIMEOUT_MS` | `int` | `5000` |
| `SEARCH_MAX_RESULTS` | `int` | `200` |
| `DEV_AUTO_LOGIN_ENABLED` | `bool` | `True` |
| `JWT_SECRET_KEY` | `str` | `'change-me-jwt-secret'` |
| `JWT_ALGORITHM` | `str` | `'HS256'` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `int` | `720` |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `int` | `7` |
| `AUTH_REVOCATION_FAIL_OPEN` | `bool` | `False` |
| `MFA_ISSUER_NAME` | `str` | `'TestLookup'` |
| `MFA_CHALLENGE_TTL_SECONDS` | `int` | `300` |
| `MFA_RECOVERY_CODE_COUNT` | `int` | `10` |
| `MFA_BREAKGLASS_ENABLED` | `bool` | `False` |
| `SSO_ENABLED` | `bool` | `False` |
| `SCIM_ENABLED` | `bool` | `False` |
| `SAML_SP_ENTITY_ID` | `str` | `'https://testlookup.io/saml/metadata'` |
| `SAML_BASE_URL` | `str` | `'http://localhost:8000'` |
| `SSO_ADMIN_FALLBACK_ENABLED` | `bool` | `True` |
| `SSO_ALLOW_PRIVATE_IDP_ENDPOINTS` | `bool` | `False` |
| `SAML_ALLOW_IDP_INITIATED` | `bool` | `False` |
| `SAML_CLOCK_SKEW_SECONDS` | `int` | `60` |
| `SSO_MAX_PROVISIONED_ROLE` | `str` | `'ADMIN'` |
| `FINETUNE_ENABLED` | `bool` | `False` |
| `FINETUNE_CLASSIFIER_MIN_EXAMPLES` | `int` | `500` |
| `FINETUNE_REASONING_MIN_EXAMPLES` | `int` | `2000` |
| `FINETUNE_EMBED_MIN_PAIRS` | `int` | `1000` |
| `FINETUNE_INCREMENTAL_TRIGGER` | `int` | `200` |
| `FINETUNE_EVAL_HOLDOUT` | `float` | `0.1` |
| `FINETUNE_MIN_ACCURACY_GAIN` | `float` | `0.02` |
| `FINETUNE_EXPORT_BUCKET` | `str` | `'training-data'` |
| `FINETUNE_OPENAI_SUFFIX` | `str` | `'testlookup'` |
| `CLASSIFIER_CONFIDENCE_THRESHOLD` | `int` | `85` |
| `CLASSIFIER_MODEL` | `Optional[str]` | `None` |
| `DEEP_INVESTIGATION_ENABLED` | `bool` | `True` |
| `RELEASE_PASS_RATE_THRESHOLD` | `float` | `90.0` |
| `KNOWLEDGE_RAG_ENABLED` | `bool` | `False` |
| `KNOWLEDGE_SYNC_TIMEOUT_SECONDS` | `int` | `60` |
| `KNOWLEDGE_MAX_SOURCES_PER_PROJECT` | `int` | `100` |
| `KNOWLEDGE_DOCS_BUCKET` | `str` | `'knowledge-docs'` |
| `KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS` | `int` | `24` |
| `KNOWLEDGE_STALE_THRESHOLD_URL_HOURS` | `int` | `168` |
| `KNOWLEDGE_CHUNK_TARGET_TOKENS` | `int` | `400` |
| `KNOWLEDGE_CHUNK_MAX_TOKENS` | `int` | `800` |
| `KNOWLEDGE_CHUNK_OVERLAP_TOKENS` | `int` | `50` |
| `KNOWLEDGE_RESYNC_BATCH_CAP` | `int` | `50` |
| `KNOWLEDGE_SYNC_STUCK_MINUTES` | `int` | `45` |
| `ANALYSIS_MODE` | `str` | `'auto'` |
| `ML_MODEL_DIR` | `str` | `'models'` |
| `ML_MODEL_SYNC_ENABLED` | `bool` | `False` |
| `ML_MODEL_STORE_PREFIX` | `str` | `'ml-models/'` |
| `ML_MIN_TRAINING_SAMPLES` | `int` | `200` |
| `ML_RETRAIN_ENABLED` | `bool` | `True` |
| `ML_ACCURACY_THRESHOLD` | `float` | `0.8` |
| `ML_PSEUDO_LABEL_CAP` | `float` | `0.3` |
| `ML_PSEUDO_LABEL_WEIGHT` | `float` | `0.3` |
| `ML_HUMAN_LABEL_FLOOR` | `int` | `50` |
| `ANOMALY_REGRESSION_THRESHOLD` | `float` | `10.0` |
| `ANOMALY_MIN_HISTORY_RUNS` | `int` | `3` |
| `ANOMALY_FLAKY_WINDOW_RUNS` | `int` | `10` |
| `ANOMALY_FLAKY_LOOKBACK_DAYS` | `int` | `30` |
| `ANOMALY_PERF_HISTORY_DAYS` | `int` | `30` |
| `ANOMALY_MIN_PERF_SAMPLES` | `int` | `5` |
| `ANOMALY_PERF_SPIKE_MULTIPLIER` | `float` | `2.0` |
| `ANOMALY_PERF_MIN_ABSOLUTE_DELTA_MS` | `int` | `1000` |
| `ANOMALY_SUMMARY_MAX_ITEMS` | `int` | `8` |
| `RISK_WEIGHT_USER_IMPACT` | `float` | `0.25` |
| `RISK_WEIGHT_ENV_SENSITIVITY` | `float` | `0.1` |
| `RISK_WEIGHT_REPRODUCIBILITY` | `float` | `0.15` |
| `RISK_WEIGHT_REGRESSION_LIKELY` | `float` | `0.2` |
| `RISK_WEIGHT_HIST_RECURRENCE` | `float` | `0.1` |
| `RISK_WEIGHT_BLAST_RADIUS` | `float` | `0.15` |
| `RISK_WEIGHT_DIAGNOSIS_CONF` | `float` | `0.05` |
| `PROMETHEUS_URL` | `Optional[str]` | `None` |
| `GITHUB_TOKEN` | `Optional[str]` | `None` |
| `GITHUB_REPO` | `Optional[str]` | `None` |
| `COMMIT_RANGE_FILE_FETCH_LIMIT` | `int` | `25` |
| `HTTP_VERIFY_TLS` | `bool` | `True` |
| `HTTP_CA_BUNDLE` | `Optional[str]` | `None` |
| `WEBHOOK_SECRET` | `str` | `'change-me-webhook-secret'` |
| `TRUSTED_PROXY_IPS` | `str` | `''` |
| `LIVE_EVENTS_REQUIRE_PROJECT_KEY` | `bool` | `True` |
| `OTEL_ENABLED` | `bool` | `True` |
| `OTEL_SERVICE_NAME` | `str` | `'testlookup'` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `Optional[str]` | `None` |
| `METRICS_ENABLED` | `bool` | `True` |
| `LOG_LEVEL` | `str` | `'INFO'` |
| `LOG_FORMAT` | `Literal['json', 'text']` | `'json'` |
