export interface AnalyzeRequest {
  test_case_id: string
  service_name?: string
  timestamp?: string
  ocp_pod_name?: string
  ocp_namespace?: string
}

export interface AnalysisEvidenceReference {
  source: string
  reference_id: string
  excerpt: string
}

export interface RoleActions {
  qa: string
  developer: string
  sre: string
  release_manager: string
}

export interface ConfidenceWhy {
  evidence_count: number
  data_sources: string[]
  is_llm_inference: boolean
  investigation_depth: 'fast_path' | 'standard' | 'deep'
}

export interface AnalysisResult {
  test_case_id: string
  root_cause_summary: string
  failure_category: string
  backend_error_found: boolean
  pod_issue_found: boolean
  is_flaky: boolean
  confidence_score: number
  recommended_actions: string[]
  role_actions: RoleActions
  evidence_references: AnalysisEvidenceReference[]
  tools_used: string[]
  confidence_why: ConfidenceWhy
  llm_provider: string
  llm_model: string
  requires_human_review: boolean
}
