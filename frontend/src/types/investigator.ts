/**
 * Types for the Investigator agent (Agentic plan AI-1) + agent governance
 * (AI-3). These mirror the pinned Wave-B API contract verbatim — the backend
 * implements the same shapes. Do not extend without reconciling with
 * `backend` (see docs/AGENTIC_AI_PLAN_2026_07.md).
 */

// ── Investigations ───────────────────────────────────────────────────────

export type InvestigationStatus =
  | 'queued'
  | 'running'
  | 'synthesizing'
  | 'completed'
  | 'cancelled'
  | 'failed'

/** Trust-ladder mode: shadow (log only) → suggest (propose) → act (gated). */
export type AgentMode = 'shadow' | 'suggest' | 'act'

export type InvestigationTrigger = 'manual' | 'auto:newly_failing' | 'auto:gate_no_go'

export type HypothesisId = 'infra' | 'commit' | 'environment' | 'known_flaky' | 'regression'

export type HypothesisStatus = 'pending' | 'running' | 'validated' | 'invalidated' | 'inconclusive'

/** Same vocabulary as AI-F4 deep findings (types/deep-investigation.ts). */
export type ConfidenceBasis = 'empirical' | 'heuristic_estimate' | 'llm_weighted'

export interface InvestigationBudget {
  max_llm_calls: number
  max_tokens: number
  max_seconds: number
}

export interface InvestigationSpend {
  llm_calls: number
  tokens: number
  cost_usd: number
  seconds: number
}

export interface HypothesisEvidence {
  kind: string
  label: string
  /** Internal app path (e.g. `/runs/<id>`) when the evidence is queryable. */
  url_path: string | null
  detail: string
}

export interface InvestigationHypothesis {
  id: HypothesisId
  title: string
  status: HypothesisStatus
  /** 0-100. */
  confidence: number
  confidence_basis: ConfidenceBasis
  summary: string
  evidence: HypothesisEvidence[]
  started_at: string | null
  completed_at: string | null
}

export interface InvestigationVerdict {
  primary_cause: HypothesisId | 'unknown'
  narrative: string
  confidence: number
  recommended_actions: string[]
}

export interface InvestigationModelInfo {
  provider: string
  model: string
}

export interface InvestigationDetail {
  id: string
  run_id: string
  project_id: string
  status: InvestigationStatus
  mode: AgentMode
  triggered_by: InvestigationTrigger
  started_at: string | null
  completed_at: string | null
  cancelled_by: string | null
  budget: InvestigationBudget
  spend: InvestigationSpend
  hypotheses: InvestigationHypothesis[]
  verdict: InvestigationVerdict | null
  /** Prompt-registry name → version digest, e.g. { investigator_plan: "v3@ab12" }. */
  prompt_versions: Record<string, string>
  model: InvestigationModelInfo | null
}

export interface InvestigationSummary {
  id: string
  run_id: string
  run_build_number: string | number | null
  status: InvestigationStatus
  mode: AgentMode
  triggered_by: InvestigationTrigger
  primary_cause: HypothesisId | 'unknown' | null
  confidence: number | null
  started_at: string | null
  completed_at: string | null
}

export interface InvestigationListResponse {
  items: InvestigationSummary[]
  total: number
}

export interface StartInvestigationResponse {
  investigation_id: string
}

export interface CancelInvestigationResponse {
  status: string
}

/** Statuses during which the cockpit keeps polling the detail endpoint. */
export const ACTIVE_INVESTIGATION_STATUSES: readonly InvestigationStatus[] = [
  'queued',
  'running',
  'synthesizing',
]

export function isInvestigationActive(status: InvestigationStatus | undefined | null): boolean {
  return status != null && ACTIVE_INVESTIGATION_STATUSES.includes(status)
}

// ── Agent governance (policies + activity ledger) ────────────────────────

export interface AgentPolicyBudgets {
  max_runs_per_day: number
  max_llm_calls_per_run: number
  max_tokens_per_run: number
  max_seconds_per_run: number
}

export interface AgentPolicyPromotion {
  shadow_runs_completed: number
  note: string | null
}

export interface AgentPolicy {
  agent_id: string
  enabled: boolean
  mode: AgentMode
  budgets: AgentPolicyBudgets
  promotion: AgentPolicyPromotion
}

export interface AgentPolicyListResponse {
  policies: AgentPolicy[]
}

/** Mutable subset sent on PUT — promotion state is server-owned. */
export interface AgentPolicyUpdate {
  enabled: boolean
  mode: AgentMode
  budgets: AgentPolicyBudgets
}

export interface AgentRunEntry {
  id: string
  agent_id: string
  project_id: string
  run_id: string | null
  mode: AgentMode
  trigger: string
  status: string
  summary: string
  actions_proposed: string[]
  actions_taken: string[]
  tokens: number
  cost_usd: number
  duration_ms: number
  prompt_registry_digest: string | null
  created_at: string
  /** Internal app path to the source view (e.g. the investigator cockpit). */
  details_path: string | null
}

export interface AgentRunListResponse {
  items: AgentRunEntry[]
  total: number
}
