import { api } from './api'

export interface RunSummary {
  id: string
  build_number: string
  branch: string | null
  status: string
  total_tests: number
  passed_tests: number
  failed_tests: number
  skipped_tests: number
  broken_tests: number
  pass_rate: number | null
  duration_ms: number | null
  start_time: string | null
  end_time: string | null
  ocp_namespace: string | null
}

export interface ExecutivePanelMetrics {
  build_number: string
  branch: string
  total_tests: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
  duration_seconds: number | null
  failure_clusters: number
  anomaly_count: number
}

export interface DominantFailure {
  category: string
  count: number
  percentage: number
}

export interface BaselineComparisonPanel {
  pass_rate_delta: number
  new_failures: number
  resolved: number
  classification: string
}

export interface ExecutivePanel {
  headline: string
  status_signal: 'GO' | 'CONDITIONAL_GO' | 'NO_GO'
  risk_score: number | null
  metrics: ExecutivePanelMetrics
  dominant_failure: DominantFailure | null
  key_takeaways: string[]
  baseline_comparison: BaselineComparisonPanel | null
  next_actions: string[]
}

export interface StructuredSummary {
  executive_summary: string | null
  executive_panel: ExecutivePanel | null
  layer1_executive: string | null
  layer2_incident: {
    what_failed?: string
    likely_cause?: string
    scope?: string
    criticality?: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
    release_impact?: 'GO' | 'CONDITIONAL_GO' | 'NO_GO'
    failure_breakdown?: Record<string, number>
  } | null
  layer3_evidence: {
    top_stack_traces?: string[]
    log_anomalies?: string[]
    flaky_test_ids?: string[]
    similar_historical_failures?: string[]
    data_sources_used?: string[]
  } | null
  layer4_action_plan: {
    immediate_mitigation?: string
    fix_recommendations?: string[]
    validation_steps?: string[]
    rollback_guidance?: string
    owner_hints?: Record<string, string>
  } | null
  generated_at: string | null
  schema_version: number
}

export interface DimensionScore {
  name: string
  label: string
  score: number        // 0-100
  weight: number
  contribution: number // score * weight
}

export interface FailureClusterIntel {
  id: string
  cluster_id: string
  label: string
  size: number
  representative_error: string | null
  member_test_ids: string[]
  cohesion_score: number | null
  criticality_level: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | null
  dimension_scores: DimensionScore[]
}

export interface AnalysisItem {
  test_case_id: string
  failure_category: string
  root_cause_summary: string | null
  confidence_score: number | null
  is_flaky: boolean
  requires_human_review: boolean
  recommended_actions: string[]
  evidence_references: Array<{ source: string; reference_id: string; excerpt: string }>
  role_actions: Record<string, string>
}

export interface ReleaseDecisionIntel {
  recommendation: 'GO' | 'CONDITIONAL_GO' | 'NO_GO'
  risk_score: number
  composite_risk: number | null
  blocking_issues: string[]
  conditions_for_go: string[]
  reasoning: string
}

export interface PipelineStage {
  stage_name: string
  status: string
  started_at: string | null
  completed_at: string | null
  skipped_reason: string | null
  execution_path: string | null
  fallback_used: boolean | null
}

export interface RegressionCluster {
  cluster_id: string
  label: string
  size: number
  classification: string
}

export interface ClassifiedFailure {
  name: string
  classification: string
}

export interface CommitRange {
  from_commit: string | null
  to_commit: string | null
  same_commit: boolean
}

export interface ConfigDriftEntry {
  field: string
  old_value: string | null
  new_value: string | null
}

export interface BaselineDiff {
  baseline_run_id: string | null
  baseline_build_number: string | null
  pass_rate_delta: number | null
  new_failures: string[]
  resolved_failures: string[]
  regression_classification: string
  // Cluster-level classification
  regression_clusters: RegressionCluster[]
  classified_new_failures: ClassifiedFailure[]
  suites_impacted_delta: number
  current_suite_count: number
  baseline_suite_count: number
  // Phase 2 Epic 3: commit/config drift
  commit_range: CommitRange | null
  config_drift: ConfigDriftEntry[]
  selection_reason: string
}

export interface DefectCandidate {
  cluster_id: string
  label: string
  severity_hint: string
  failure_category: string
  confidence: number
  recommended_actions: string[]
  // ROI-01: lifecycle fields from persisted candidates
  status?: 'pending' | 'promoted' | 'duplicate' | 'dismissed'
  duplicate_detected?: boolean
  duplicate_defect_id?: string | null
  promoted_defect_id?: string | null
  composite_score?: number
}

export interface SummaryModes {
  available: string[]
  default: string
}

export interface Provenance {
  schema_version: number
  fallback_used: boolean
  generated_by: string
  tools_used_count: number
  generated_at: string | null
  // Epic 4: trustworthy AI provenance
  confidence: number | null
  confidence_reason: string | null
  evidence_count: number
  sources_used: string[]
  deterministic_checks_used: string[]
}

export interface RunIntelligence {
  run: RunSummary
  structured_summary: StructuredSummary | null
  failure_clusters: FailureClusterIntel[]
  category_breakdown: Record<string, number>
  affected_suites: Array<{ suite: string; failed_count: number }>
  top_analyses: AnalysisItem[]
  avg_confidence: number
  release_decision: ReleaseDecisionIntel | null
  role_actions: Record<string, string>
  pipeline_stages: PipelineStage[]
  intelligence_available: boolean
  all_green: boolean
  dimension_scores: DimensionScore[]
  what_changed_since_last_good_run: BaselineDiff | null
  defect_candidates: DefectCandidate[]
  summary_modes: SummaryModes | null
  provenance: Provenance | null
  partial_errors: string[] | null
  deep_pipeline_status?: {
    pipeline_run_id?: string | null
    status: string
    started_at: string | null
    completed_at: string | null
    workflow_type?: string
  } | null
  _snapshot?: { cached: boolean; stale: boolean; just_refreshed?: boolean }
}

export interface Citation {
  source: string
  excerpt: string
  test_id: string
}

export interface RunModeSummary {
  test_run_id: string
  mode: string
  executive_summary: string | null
  markdown_report: string
  layer1_executive: string | null
  layer2_incident: StructuredSummary['layer2_incident']
  layer3_evidence: StructuredSummary['layer3_evidence']
  layer4_action_plan: StructuredSummary['layer4_action_plan']
  executive_panel?: ExecutivePanel | null
  fallback_used: boolean
  generated_at: string | null
  citations: Citation[]
  similar_failures: Array<{ test_name?: string } | string>
  provenance: {
    schema_version: number
    data_sources_used: string[]
    fallback_used: boolean
    generated_at: string
  } | null
}

export interface ScoringDimension {
  name: string
  weight: number
  description: string
}

export interface ScoringModel {
  version: number
  go_threshold: number
  no_go_threshold: number
  dimensions: ScoringDimension[]
}

export const runIntelligenceService = {
  async get(runId: string, include?: string): Promise<RunIntelligence> {
    const params = include ? { include } : {}
    const { data } = await api.get<RunIntelligence>(`/api/v1/runs/${runId}/intelligence`, { params })
    return data
  },

  async getSummary(runId: string, mode: 'executive' | 'developer' | 'manager'): Promise<RunModeSummary> {
    const { data } = await api.get<RunModeSummary>(`/api/v1/runs/${runId}/summary`, { params: { mode } })
    return data
  },

  async getBaselineDiff(runId: string): Promise<BaselineDiff> {
    const { data } = await api.get<BaselineDiff>(`/api/v1/runs/${runId}/baseline-diff`)
    return data
  },

  async getScoringModel(): Promise<ScoringModel> {
    const { data } = await api.get<ScoringModel>('/api/v1/scoring-model')
    return data
  },

  async refreshIntelligence(runId: string): Promise<RunIntelligence> {
    const { data } = await api.post<RunIntelligence>(`/api/v1/runs/${runId}/intelligence/refresh`)
    return data
  },
}
