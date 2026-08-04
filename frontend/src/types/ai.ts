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
  /** AI-F4 calibration basis: "empirical" = measured precision on labeled
   *  eval samples; "heuristic_estimate" = engineering estimate, not
   *  empirically calibrated. Null/absent for LLM/ML analyses. */
  confidence_basis?: 'empirical' | 'heuristic_estimate' | 'human_corrected' | null
}

/**
 * Routing provenance for an analysis (US-15.1). EVERY field is optional by
 * contract: this block is newer than the analyses table, so legitimately
 * absent on older rows. `AISuggestion.normalizeProvenance` degrades to no
 * provenance line rather than inventing one.
 */
export interface AnalysisProvenance {
  /** Engine that actually answered: "rules" / "ml" / "llm" / … */
  mode_used?: string | null
  /** Engine the caller asked for, when it differs from mode_used. */
  mode_requested?: string | null
  /** Engine we fell back FROM — set ⇒ the UI shows a fallback notice. */
  fallback_from?: string | null
  fallback_reason?: string | null
}

export interface AnalysisResult {
  test_case_id: string
  /** Feedback target for confirm/correct. Absent on responses that predate
   *  the field — the trust chrome then renders no feedback buttons rather
   *  than dead ones. */
  analysis_id?: string | null
  /** US-15.1 provenance block, built backend-side in parallel. Optional. */
  provenance?: AnalysisProvenance | null
  /** Explicit low-confidence marker. Falls back to requires_human_review. */
  low_confidence?: boolean | null
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
