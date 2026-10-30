import type { AxiosResponse } from 'axios'
import type { PrometheusMetricTemplate } from '@/types/prometheus'
import { api } from './api'
import { getData, postData } from './http'

export interface SmtpConfigRead {
  enabled: boolean
  host: string
  port: number
  user: string | null
  from_address: string
  /** True = implicit TLS (port 465); False = STARTTLS (port 587). Plain SMTP is not supported. */
  implicit_tls: boolean
  password_set: boolean
}

export interface SmtpConfigUpdate {
  enabled: boolean
  host: string
  port: number
  user: string | null
  password: string | null  // null = keep existing
  from_address: string
  /** True = implicit TLS (port 465); False = STARTTLS (port 587). Plain SMTP is not supported. */
  implicit_tls: boolean
}

export interface SmtpTestResult {
  success: boolean
  message: string
}

// ── AI Configuration ─────────────────────────────────────────

export type AnalysisMode = 'llm' | 'ml' | 'rules' | 'auto'

/**
 * Where a Settings › AI value comes from (W04 AIC-01/02/04): `'ai_config'` = a
 * valid value is stored on this page ("Set here"); `'env_default'` = nothing
 * valid is stored and the server's environment default applies. A string, not a
 * union: an unknown value renders as "Server default", never as a crash.
 */
export type ConfigSource = 'ai_config' | 'env_default' | string

/** `GET /settings/ai/mode`: what every role's screens branch on, and nothing else of the config. */
export interface AIModeRead {
  analysis_mode: AnalysisMode
  /** Effective offline ceiling: true when AI stays on this deployment. */
  ai_offline_mode: boolean
  /**
   * EFFECTIVE Deep Investigation toggle (the stored value over the env default,
   * both ways). Optional: a pod of the previous build does not send it, and a
   * missing value means "offered" (W04 contract R-8).
   */
  deep_investigation_enabled?: boolean
}

export interface AIConfigRead {
  llm_provider: string
  llm_model: string
  llm_temperature: number
  llm_max_tokens: number
  /**
   * EFFECTIVE offline state, after the AI_OFFLINE_MODE environment ceiling.
   * The env var can only tighten it — a stored override never loosens it.
   */
  ai_offline_mode: boolean
  /** Why: 'env' (pinned by the environment) | 'override' | 'not_offline'. */
  ai_offline_mode_source: string
  /** True ⇒ the toggle is read-only here; say so instead of eating the click. */
  ai_offline_mode_env_pinned: boolean
  embedding_provider: string
  embedding_model: string
  ai_confidence_threshold: number
  ai_confidence_threshold_source: ConfigSource
  ai_timeout_seconds: number
  ai_timeout_seconds_source: ConfigSource
  deep_investigation_enabled: boolean
  deep_investigation_enabled_source: ConfigSource
  finetune_enabled: boolean
  finetune_enabled_source: ConfigSource
  openai_key_set: boolean
  google_key_set: boolean
  anthropic_key_set: boolean
  openrouter_key_set: boolean
  // Analysis mode — LLM-free operation
  analysis_mode: AnalysisMode
  analysis_mode_source: ConfigSource
  /** `settings.ML_MIN_TRAINING_SAMPLES`: the labelled-sample floor before ML can train. */
  ml_min_training_samples: number
  ml_model_available: boolean
  ml_model_accuracy: number | null
  ml_training_sample_count: number
  // AI-F1 label integrity — honest learning-loop status
  ml_human_label_count: number
  ml_human_label_floor: number
  ml_maturity: 'not_trained' | 'bootstrap_llm_imitating' | 'human_calibrated'
  // Knowledge RAG
  knowledge_rag_enabled: boolean
  /** The stored faithfulness strategy; round-trips through the PUT. */
  rag_faithfulness_evaluator: RagFaithfulnessEvaluator
  /** The strategy the gate runs, which offline mode or a missing package can override. */
  rag_faithfulness_evaluator_effective: RagFaithfulnessEvaluator
  /** Why: 'configured' | 'offline_mode' | 'ragas_not_installed'. */
  rag_faithfulness_evaluator_reason: string
  /** The rag_faithfulness_gate flag, on in any scope. Off: no case is scored. */
  rag_faithfulness_gate_enabled: boolean
  /**
   * W12 SRCH-02: which embedder every Chroma collection uses NOW, what is
   * configured, and any re-embed. `null` = the server could not read the state
   * (never a guess); missing = a server older than W12. Both read as
   * "Could not read the embedding index state."
   */
  embedding_index?: EmbeddingIndex | null
}

// ── Embedding index (W12 SRCH-02, contract §1.1) ─────────────
// Every enum-like field is a string: an unknown value renders, it never throws.

/** One embedding generation: the Collection prefix + the embedder. */
export interface EmbeddingGeneration {
  /** 'default' (Bundled MiniLM) | 'ollama' | 'openai' — a stored unsupported value is shown, never applied. */
  provider: string
  model: string
  /** '' = no prefix. */
  prefix: string
  /** 'default' | 'ollama:<model>' | 'openai:<model>' */
  key: string
  /** '' for the legacy generation, else 'e' + 8 hex. */
  tag: string
}

export interface EmbeddingFamilyProgress {
  /** The collection family's base name (test_case_search, knowledge_chunks, …). */
  family: string
  /** 'pending' | 'running' | 'done' | 'skipped' */
  state: string
  processed: number
  /** null = not counted. */
  total: number | null
}

export interface EmbeddingIndex {
  /** What every read and write uses NOW. */
  active: EmbeddingGeneration
  /** ai_config.embedding_* + the storage prefix. */
  configured: EmbeddingGeneration
  in_sync: boolean
  /** 'idle' | 'building' | 'failed' */
  status: string
  /**
   * With `status: 'building'`: the build's worker died (its lease expired) or nothing claimed it in time.
   * Re-embed now resumes it (the rebuild re-enqueues instead of a 409). null / missing = not stalled.
   */
  stalled?: boolean | null
  target?: EmbeddingGeneration | null
  families?: EmbeddingFamilyProgress[]
  processed?: number
  total?: number | null
  requested_at?: string | null
  /** When the active generation last changed (null = never: the legacy generation). */
  switched_at?: string | null
  /** A sentence (no host, no key). */
  last_error?: string | null
  /** The server's note when the stored provider or prefix is not applied. */
  configured_note?: string | null
}

export type RagFaithfulnessEvaluator = 'ollama' | 'ragas'

export interface AIConfigUpdate {
  llm_provider?: string
  llm_model?: string
  llm_temperature?: number
  llm_max_tokens?: number
  ai_offline_mode?: boolean
  embedding_provider?: string
  embedding_model?: string
  ai_confidence_threshold?: number
  /** 5..1800 (the floor was 30 before W04 AIC-02). */
  ai_timeout_seconds?: number
  deep_investigation_enabled?: boolean
  finetune_enabled?: boolean
  openai_api_key?: string
  google_api_key?: string
  anthropic_api_key?: string
  openrouter_api_key?: string
  analysis_mode?: AnalysisMode
  knowledge_rag_enabled?: boolean
  rag_faithfulness_evaluator?: RagFaithfulnessEvaluator
}

// ── AI Model Status (US-13.2 — offline model pack) ───────────

export type ModelPurpose = 'llm' | 'embedding' | 'classifier'

export interface RequiredModel {
  name: string
  /** Widened to string on purpose — the backend may add purposes. */
  purpose: ModelPurpose | string
  present: boolean
  /**
   * Runtime-aware fix for a missing model (the exact `ollama pull` for this
   * deployment). Null when present, and null while Ollama is unreachable —
   * pulling can't be the fix until the daemon answers.
   */
  remedy: string | null
  /** W12 SRCH-02: the embedding model is the ACTIVE embedder (false = the one a re-embed builds). */
  in_use?: boolean | null
}

export interface FallbackChainEntry {
  mode: AnalysisMode | string
  available: boolean
  /** Why it can't run — or a caveat on an available tier. Human-readable. */
  reason: string | null
  /**
   * The same distinction, machine-readable. Branch badges on this rather than
   * re-deriving the cause from unrelated fields: the settings page used to
   * guess from `ollama_reachable`, so a missing OpenRouter API key rendered as
   * "Model Missing" and sent the operator to install a model.
   *
   * Optional because an older backend won't send it — callers must handle
   * `null` rather than assume a code is present.
   */
  reason_code?: string | null
}

/**
 * Live state of the local model backend.
 *
 * `ollama_reachable === false` and "a required model is missing" are
 * different problems (connectivity vs an incomplete model-pack import) —
 * the UI must never render them as the same thing.
 */
export interface AIModelStatusRead {
  ollama_reachable: boolean
  ollama_error: string | null
  ollama_base_url: string
  installed_models: string[]
  required: RequiredModel[]
  fallback_chain: FallbackChainEntry[]
  offline_mode: boolean
  llm_provider: string
  analysis_mode: AnalysisMode | string
  checked_at: string
}

// ── ML classifier training (W04 AIC-05) ─────────────────────
// Mirrors backend/app/models/ml_training_schemas.py. Every enum-like field is a
// string: an unknown status renders, it does not throw. `null` = not known.

export interface MLTrainingState {
  /** 'idle' | 'queued' | 'running' */
  state: 'idle' | 'queued' | 'running' | string
  task_id: string | null
  queued_at: string | null
  started_at: string | null
  /** 'user:<username>' | 'beat' */
  triggered_by: string | null
}

export interface MLLastRun {
  /** trained | insufficient_data | insufficient_class_diversity | below_threshold | disabled | skipped_running | error */
  status: string
  message: string | null
  finished_at: string
  triggered_by: string
  /** 0..1 */
  accuracy: number | null
  version: string | null
  sample_count: number | null
}

export interface MLStatusRead {
  model_available: boolean
  /** 'YYYYMMDD_HHMMSS' */
  version: string | null
  /** 0..1 holdout accuracy */
  accuracy: number | null
  trained_at: string | null
  /** Samples the CURRENT model was trained on. */
  model_sample_count: number | null
  /** Usable labels now: feedback rows + high-confidence pseudo-labels. */
  available_samples: number
  min_training_samples: number
  human_labels: number
  human_label_floor: number
  maturity: 'not_trained' | 'bootstrap_llm_imitating' | 'human_calibrated' | string
  deps_installed: boolean
  /** ML_RETRAIN_ENABLED: gates the NIGHTLY run only; Train now ignores it. */
  retrain_enabled: boolean
  can_train: boolean
  /** insufficient_samples | deps_missing | status_unavailable | null */
  cannot_train_reason: string | null
  cannot_train_message: string | null
  training: MLTrainingState
  last_run: MLLastRun | null
}

export interface MLRetrainAccepted {
  task_id: string
  /** 'queued' | 'running' */
  state: string
  already_running: boolean
}

/** The `detail` of a 409 from `POST /settings/ai/ml/retrain`. */
export interface MLRetrainRefused {
  /** 'insufficient_samples' | 'deps_missing' */
  code: string
  message: string
  have: number
  need: number
}

// ── Integrations Configuration ──────────────────────────────

export interface IntegrationsConfigRead {
  jira_enabled: boolean
  jira_domain: string | null
  jira_email: string | null
  jira_token_set: boolean
  jira_default_project_key: string
  splunk_enabled: boolean
  splunk_base_url: string | null
  splunk_token_set: boolean
  ocp_enabled: boolean
  ocp_api_url: string | null
  ocp_token_set: boolean
  ocp_default_namespace: string
  slack_enabled: boolean
  slack_webhook_url: string | null
  slack_webhook_set: boolean
  /** @deprecated W10 INT-02: ignored by the server; kept because old servers and fixtures send it. */
  slack_default_channel: string
  teams_enabled: boolean
  teams_webhook_url: string | null
  teams_webhook_set: boolean
  github_repo: string | null
  github_token_set: boolean
  /**
   * W10 INT-01: the text and secret keys whose shown value (or `*_set`) comes
   * from the deployment's environment because the key was never saved here.
   * Names only, never a value. Optional: an older server sends nothing (read `[]`).
   */
  environment_fields?: string[]
  /** W20 ADM-10: secret fields stored as Vault references (`vault:<path>#<key>`). */
  vault_fields?: string[]
  /**
   * W23d AGT-15: the Prometheus block (SAVED-ONLY — never an environment value). Optional: an older server
   * sends none (read: off, no URL, no token, the defaults).
   */
  prometheus_enabled?: boolean
  prometheus_base_url?: string | null
  /** A bearer token is stored (never the value). */
  prometheus_token_set?: boolean
  /** The saved templates, else the server's defaults (`prometheus_templates_source: 'default'`). */
  prometheus_metric_templates?: PrometheusMetricTemplate[]
  prometheus_templates_source?: 'saved' | 'default' | string
}

export interface IntegrationsConfigUpdate {
  jira_enabled?: boolean
  jira_domain?: string
  jira_email?: string
  jira_api_token?: string
  jira_default_project_key?: string
  splunk_enabled?: boolean
  splunk_base_url?: string
  splunk_api_token?: string
  ocp_enabled?: boolean
  ocp_api_url?: string
  ocp_sa_token?: string
  ocp_default_namespace?: string
  slack_enabled?: boolean
  slack_webhook_url?: string
  teams_enabled?: boolean
  teams_webhook_url?: string
  github_repo?: string
  github_token?: string
  /** W23d AGT-15 (ADMIN only). `prometheus_bearer_token: ''` expires the stored token. */
  prometheus_enabled?: boolean
  prometheus_base_url?: string
  prometheus_bearer_token?: string
  /** ≤ TEMPLATES_MAX; `[]` = back to the defaults. */
  prometheus_metric_templates?: PrometheusMetricTemplate[]
}

// ── Data & Storage Configuration ────────────────────────────

export interface StorageConfigRead {
  storage_backend: string
  postgres_connected: boolean
  mongo_connected: boolean
  redis_connected: boolean
  minio_endpoint: string
  minio_bucket_name: string
  minio_use_ssl: boolean
  chroma_host: string
  chroma_port: number
  /** The STORED value (W12: read as the Collection prefix; see `collection_prefix`). */
  chroma_collection: string
  /** W12 SRCH-02: the configured prefix the stored value means ('' = no prefix). Missing = an older server. */
  collection_prefix?: string
  /** Set when the stored value is not a valid prefix and is ignored. */
  collection_prefix_note?: string | null
  /** The ACTIVE generation's prefix; null when the embedding index state could not be read. */
  active_collection_prefix?: string | null
}

export interface StorageConfigUpdate {
  storage_backend?: string
  chroma_host?: string
  chroma_port?: number
  chroma_collection?: string
  minio_endpoint?: string
  minio_bucket_name?: string
  minio_use_ssl?: boolean
}

export const appSettingsService = {
  // SMTP
  getSmtpConfig(): Promise<SmtpConfigRead> {
    return api.get<SmtpConfigRead>('/api/v1/settings/smtp').then((r: AxiosResponse<SmtpConfigRead>) => r.data)
  },
  updateSmtpConfig(payload: SmtpConfigUpdate): Promise<SmtpConfigRead> {
    return api.put<SmtpConfigRead>('/api/v1/settings/smtp', payload).then((r: AxiosResponse<SmtpConfigRead>) => r.data)
  },
  testSmtpConfig(): Promise<SmtpTestResult> {
    return api.post<SmtpTestResult>('/api/v1/settings/smtp/test').then((r: AxiosResponse<SmtpTestResult>) => r.data)
  },

  // AI Configuration
  /** The analysis mode alone, for every signed-in role (`/settings/ai` is QA lead and above). */
  getAIMode(): Promise<AIModeRead> {
    return api.get<AIModeRead>('/api/v1/settings/ai/mode').then(r => r.data)
  },
  getAIConfig(): Promise<AIConfigRead> {
    return api.get<AIConfigRead>('/api/v1/settings/ai').then(r => r.data)
  },
  /**
   * `opts.suppressToast`: the caller renders the failure itself (W12 SRCH-02: a
   * changed embedder's 422 sentence shows under the Embedding card).
   */
  updateAIConfig(payload: AIConfigUpdate, opts?: { suppressToast?: boolean }): Promise<AIConfigRead> {
    return api
      .put<AIConfigRead>('/api/v1/settings/ai', payload, opts?.suppressToast ? { suppressToast: true } : undefined)
      .then(r => r.data)
  },
  /** The embedding index alone (QA lead+); polled only while a re-embed runs (W12 SRCH-02). */
  getEmbeddingIndex(): Promise<EmbeddingIndex> {
    return getData<EmbeddingIndex>('/api/v1/settings/ai/embedding-index')
  },
  /**
   * Re-embed now / Retry (ADMIN): a build toward the CONFIGURED generation. No
   * global toast: the Embedding card renders a 409 / 422 sentence itself.
   */
  rebuildEmbeddingIndex(): Promise<EmbeddingIndex> {
    return postData<EmbeddingIndex>('/api/v1/settings/ai/embedding-index/rebuild', undefined, { suppressToast: true })
  },
  getAIModelStatus(): Promise<AIModelStatusRead> {
    return api.get<AIModelStatusRead>('/api/v1/settings/ai/model-status').then(r => r.data)
  },
  /** ML classifier status: samples, the current model, the retrain lock, the last attempt (QA lead+). */
  getMLStatus(): Promise<MLStatusRead> {
    return getData<MLStatusRead>('/api/v1/settings/ai/ml/status')
  },
  /**
   * "Train now" (ADMIN). 202 even when a run is already going (same task id,
   * `already_running: true`). No global toast: the ML card renders a 409's
   * `detail.message` itself (insufficient samples / dependencies missing).
   */
  retrainML(): Promise<MLRetrainAccepted> {
    return postData<MLRetrainAccepted>('/api/v1/settings/ai/ml/retrain', undefined, { suppressToast: true })
  },

  // Integrations
  getIntegrationsConfig(): Promise<IntegrationsConfigRead> {
    return api.get<IntegrationsConfigRead>('/api/v1/settings/integrations').then(r => r.data)
  },
  /**
   * `opts.suppressToast`: the caller renders the failure itself (W10 INT-01:
   * a field-level 422 shows under its field; anything else is one toast).
   */
  updateIntegrationsConfig(payload: IntegrationsConfigUpdate, opts?: { suppressToast?: boolean }): Promise<IntegrationsConfigRead> {
    return api
      .put<IntegrationsConfigRead>('/api/v1/settings/integrations', payload, opts?.suppressToast ? { suppressToast: true } : undefined)
      .then(r => r.data)
  },

  // Data & Storage
  getStorageConfig(): Promise<StorageConfigRead> {
    return api.get<StorageConfigRead>('/api/v1/settings/storage').then(r => r.data)
  },
  /** `opts.suppressToast`: the caller renders the failure itself (W12: a changed prefix's 422 under its field). */
  updateStorageConfig(payload: StorageConfigUpdate, opts?: { suppressToast?: boolean }): Promise<StorageConfigRead> {
    return api
      .put<StorageConfigRead>('/api/v1/settings/storage', payload, opts?.suppressToast ? { suppressToast: true } : undefined)
      .then(r => r.data)
  },
}

/** W20 ADM-10: `GET /settings/secret-providers` (ADMIN) — the deployment's Vault reference provider. */
export interface SecretProviderStatus {
  name: string
  configured: boolean
  address_host: string | null
  auth_method: string
  healthy: boolean
  message: string
  references: number
}

export async function getSecretProviders(): Promise<{ providers: SecretProviderStatus[]; reference_syntax: string }> {
  const { data } = await api.get<{ providers: SecretProviderStatus[]; reference_syntax: string }>('/api/v1/settings/secret-providers')
  return data
}
