import type { AxiosResponse } from 'axios'
import { api } from './api'

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

export interface AIConfigRead {
  llm_provider: string
  llm_model: string
  llm_temperature: number
  llm_max_tokens: number
  ai_offline_mode: boolean
  embedding_provider: string
  embedding_model: string
  ai_confidence_threshold: number
  ai_timeout_seconds: number
  deep_investigation_enabled: boolean
  finetune_enabled: boolean
  openai_key_set: boolean
  google_key_set: boolean
  // Analysis mode — LLM-free operation
  analysis_mode: AnalysisMode
  ml_model_available: boolean
  ml_model_accuracy: number | null
  ml_training_sample_count: number
}

export interface AIConfigUpdate {
  llm_provider?: string
  llm_model?: string
  llm_temperature?: number
  llm_max_tokens?: number
  ai_offline_mode?: boolean
  embedding_provider?: string
  embedding_model?: string
  ai_confidence_threshold?: number
  ai_timeout_seconds?: number
  deep_investigation_enabled?: boolean
  finetune_enabled?: boolean
  openai_api_key?: string
  google_api_key?: string
  analysis_mode?: AnalysisMode
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
  slack_default_channel: string
  teams_enabled: boolean
  teams_webhook_url: string | null
  github_repo: string | null
  github_token_set: boolean
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
  slack_default_channel?: string
  teams_enabled?: boolean
  teams_webhook_url?: string
  github_repo?: string
  github_token?: string
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
  chroma_collection: string
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
  getAIConfig(): Promise<AIConfigRead> {
    return api.get<AIConfigRead>('/api/v1/settings/ai').then(r => r.data)
  },
  updateAIConfig(payload: AIConfigUpdate): Promise<AIConfigRead> {
    return api.put<AIConfigRead>('/api/v1/settings/ai', payload).then(r => r.data)
  },

  // Integrations
  getIntegrationsConfig(): Promise<IntegrationsConfigRead> {
    return api.get<IntegrationsConfigRead>('/api/v1/settings/integrations').then(r => r.data)
  },
  updateIntegrationsConfig(payload: IntegrationsConfigUpdate): Promise<IntegrationsConfigRead> {
    return api.put<IntegrationsConfigRead>('/api/v1/settings/integrations', payload).then(r => r.data)
  },

  // Data & Storage
  getStorageConfig(): Promise<StorageConfigRead> {
    return api.get<StorageConfigRead>('/api/v1/settings/storage').then(r => r.data)
  },
  updateStorageConfig(payload: StorageConfigUpdate): Promise<StorageConfigRead> {
    return api.put<StorageConfigRead>('/api/v1/settings/storage', payload).then(r => r.data)
  },
}
