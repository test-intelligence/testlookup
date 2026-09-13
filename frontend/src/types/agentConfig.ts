/**
 * Per-project agent configuration (architecture E4.1 / E4.3).
 *
 * Mirrors the wire shape of GET/PUT /api/v1/projects/{id}/agent-configs[/{agent_id}]
 * (backend/app/services/agent_config_service.py, AgentConfigV1 + serialize()).
 * The server validates every document; these types only describe it.
 */
import type { AgentMode } from './investigator'

export type AgentTier = 'auto' | 'deterministic' | 'slm' | 'llm'
export type ReviewPolicy = 'human_required' | 'human_required_plus_auto_reviewer'
export type ToolPermission = 'read_only' | 'propose_action' | 'mutating'

export interface ModelEndpointConfig {
  provider: string
  model: string
  temperature: number
  max_tokens: number
}

export interface AgentConfigDocument {
  agent_id: string
  enabled: boolean
  mode: AgentMode
  model: {
    tier: AgentTier
    slm: ModelEndpointConfig | null
    llm: ModelEndpointConfig | null
    escalation: { on_validation_failure: boolean; on_confidence_below: number; max_escalations: number }
  }
  thresholds: { confidence_min: number; max_failures_analyzed: number; degraded_ratio: number }
  retry: { max_attempts: number; base_seconds: number; cap_seconds: number; jitter: number; retry_on: string[] }
  timeout_seconds: number
  tools: { allowlist: string[] }
  budget: {
    max_llm_calls_per_run: number
    max_tokens_per_run: number
    max_cost_usd_per_run: number
    max_runs_per_day: number
  }
  shadow: { sample_rate: number; daily_token_budget: number }
  review: { policy: ReviewPolicy; auto_reviewer: boolean; second_model_check: boolean }
  override_policy: { allow_tier_downgrade: boolean; allow_retry_decrease: boolean; allow_tool_narrowing: boolean }
}

export interface AgentConfigView {
  agent_id: string
  /** `default` when the project has never saved a configuration for this agent. */
  source: 'default' | 'project'
  /** 0 for defaults; every save bumps it. */
  config_version: number
  /** False when a stored row stopped validating (e.g. an environment ceiling was lowered). */
  valid: boolean
  errors: string[]
  updated_at: string | null
  updated_by: string | null
  config: AgentConfigDocument
}

export interface AgentConfigListResponse {
  configs: AgentConfigView[]
  /** Every agent tool and the permission it needs. */
  tools: Record<string, ToolPermission>
}
