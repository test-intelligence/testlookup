import { getData, putData } from './http'

export type QuotaCapAction =
  | 'SOFT_WARN'
  | 'AUTO_DOWNGRADE_TO_ML'
  | 'AUTO_DOWNGRADE_TO_RULES'
  | 'HARD_BLOCK'

export type UsageStatus = 'OK' | 'SOFT_WARN' | 'CAPPED' | 'UNLIMITED'

export interface LlmQuotaRead {
  id: string
  project_id: string
  enabled: boolean
  period_type: string
  included_usd: number
  overage_rate_usd: number
  hard_cap_usd: number
  soft_warn_threshold_pct: number
  at_cap_action: QuotaCapAction
  created_at: string
  updated_at: string
  updated_by_user_id: string | null
}

export interface LlmQuotaWrite {
  enabled: boolean
  period_type?: string
  included_usd: number
  overage_rate_usd: number
  hard_cap_usd: number
  soft_warn_threshold_pct: number
  at_cap_action: QuotaCapAction
}

export interface LlmUsageRead {
  project_id: string
  period_start: string
  period_end: string
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  total_llm_calls: number
  cap_hits: number
  included_usd: number | null
  hard_cap_usd: number | null
  utilization_pct: number | null
  status: UsageStatus
}

export interface LlmUsageHistoryEntry {
  period_start: string
  period_end: string
  total_cost_usd: number
  total_llm_calls: number
  cap_hits: number
}

export interface BillingOverviewProject {
  project_id: string
  project_name: string
  current_cost_usd: number
  hard_cap_usd: number | null
  utilization_pct: number | null
  status: UsageStatus
  cap_hits: number
}

export interface BillingOverviewResponse {
  period_start: string
  period_end: string
  total_cost_usd: number
  total_llm_calls: number
  projects: BillingOverviewProject[]
}

export const llmBudgetService = {
  overview: () => getData<BillingOverviewResponse>('/api/v1/billing/overview'),
  getQuota: (projectId: string) =>
    getData<LlmQuotaRead>(`/api/v1/projects/${projectId}/llm-quota`),
  putQuota: (projectId: string, payload: LlmQuotaWrite) =>
    putData<LlmQuotaRead>(`/api/v1/projects/${projectId}/llm-quota`, payload),
  getUsage: (projectId: string) =>
    getData<LlmUsageRead>(`/api/v1/projects/${projectId}/llm-usage`),
  getHistory: (projectId: string, limit = 12) =>
    getData<LlmUsageHistoryEntry[]>(
      `/api/v1/projects/${projectId}/llm-usage/history`,
      { params: { limit } },
    ),
}
