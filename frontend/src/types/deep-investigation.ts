export interface FailureCluster {
  cluster_id: string
  label: string
  representative_error: string | null
  member_test_ids: string[]
  size: number
  cohesion_score: number | null
}

export interface CausalStep {
  step: number
  service: string
  finding: string
}

export interface EvidenceItem {
  source: string
  excerpt: string
}

export interface ContractViolation {
  field_path: string
  violation_type: string
  expected: string
  actual: string
  severity: 'critical' | 'warning' | 'info'
  endpoint?: string
  test_case_id?: string
}

export interface DeepFinding {
  cluster_id: string
  root_cause: string | null
  failure_category: string | null
  confidence_score: number | null
  causal_chain: CausalStep[] | null
  evidence: EvidenceItem[] | null
  affected_services: string[] | null
  contract_violations: ContractViolation[] | null
  recommended_actions: string[] | null
  /** AI-F4: row provenance — "pipeline" (real deep-pipeline output),
   *  "seed" (demo data), or "unknown" (legacy rows before tagging). */
  origin?: 'pipeline' | 'seed' | 'unknown'
  /** AI-F4 calibration basis of confidence_score, when known. */
  confidence_basis?: 'empirical' | 'heuristic_estimate' | 'human_corrected' | null
}

export interface ReleaseDecision {
  run_id: string
  recommendation: 'GO' | 'NO_GO' | 'CONDITIONAL_GO'
  risk_score: number
  blocking_issues: string[]
  conditions_for_go: string[]
  reasoning: string | null
  human_override: string | null
  pass_rate: number | null
  build_number: string | null
  // Epic 5 additions
  dimension_scores: Record<string, number> | null
  composite_risk: number | null
  score_model_version: number | null
}
