import { api } from './api'
import type { DimensionScore, FailureClusterIntel, BaselineDiff } from './runIntelligenceService'

// ── Types ────────────────────────────────────────────────────────────────────

export interface RuleEvaluationEntry {
  rule_id: string
  rule_name: string
  rule_type: string
  passed: boolean
  action: string
  message: string
  actual_value: number | null
  threshold_value: number | null
}

export interface OverrideAuditEntry {
  timestamp: string
  actor_id: string | null
  actor_name: string | null
  before_recommendation: string
  before_risk_score: number
  after_recommendation: string
  reason: string
  policy_id: string | null
  policy_version: number | null
}

export interface ReleaseCouncilDecision {
  run_id: string
  recommendation: 'GO' | 'CONDITIONAL_GO' | 'NO_GO'
  risk_score: number
  composite_risk: number | null
  dimension_scores: DimensionScore[]
  blocking_issues: string[]
  conditions_for_go: string[]
  reasoning: string | null
  score_model_version: number | null
  input_snapshot: Record<string, unknown> | null
  cluster_insights: FailureClusterIntel[]
  baseline_diff: BaselineDiff | null
  open_defects_by_component: Array<{ component: string; count: number }>
  human_override: string | null
  overridden_by: string | null
  original_recommendation: string | null
  original_risk_score: number | null
  override_audit: OverrideAuditEntry[]
  pass_rate: number | null
  build_number: string | null
  // Policy context (ENT-02)
  policy_id: string | null
  policy_version: number | null
  policy_level: string | null
  rule_evaluations: RuleEvaluationEntry[]
  // True when the backend synthesised this view from the run's
  // aggregates because no persisted ReleaseDecision row exists yet.
  // The page surfaces a "quick-look" note + a CTA to run deep
  // investigation for cluster / defect / narrative context.
  synthesized?: boolean
}

// ── Service ──────────────────────────────────────────────────────────────────

export const releaseCouncilService = {
  async get(runId: string): Promise<ReleaseCouncilDecision> {
    const { data } = await api.get<ReleaseCouncilDecision>(`/api/v1/release-readiness/${runId}`)
    return data
  },

  async override(
    runId: string,
    overrideRecommendation: string,
    reason: string,
  ): Promise<ReleaseCouncilDecision> {
    const { data } = await api.post<ReleaseCouncilDecision>(
      `/api/v1/release-readiness/${runId}/override`,
      { override_recommendation: overrideRecommendation, reason },
    )
    return data
  },
}
