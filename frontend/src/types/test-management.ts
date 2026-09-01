import type { PaginatedResponse } from './common'

export type { PaginatedResponse }

export interface TestStep {
  step_number: number
  action: string
  expected_result?: string
}

export interface TestCaseParameter {
  name: string
  value?: string
  mode?: string
  masked?: boolean
}

export interface AIReviewIssue {
  severity: string
  category: string
  description: string
  step?: number
}

export interface AIReviewSuggestion {
  field: string
  suggestion: string
}

export interface AIReviewResult {
  quality_score?: number
  grade?: string
  summary?: string
  score_breakdown?: Record<string, number>
  issues?: AIReviewIssue[]
  suggestions?: AIReviewSuggestion[]
  best_practices_violations?: string[]
  coverage_gaps?: string[]
  positive_aspects?: string[]
  error?: string
}

export interface ManagedTestCase {
  /**
   * "managed" for rows backed by ``managed_test_cases``, "automation" for
   * synthesised rows derived from per-run ``test_cases`` (returned by
   * /cases when ``include_automation=true``). The Test Management UI
   * uses this to render an "Automation" badge and disable edit
   * affordances on automation rows.
   */
  source?: 'managed' | 'automation' | string
  id: string
  project_id: string
  title: string
  description?: string
  objective?: string
  preconditions?: string
  steps?: TestStep[]
  parameters?: TestCaseParameter[]
  expected_result?: string
  test_data?: string
  test_type: string
  priority: string
  severity: string
  feature_area?: string
  suite_name?: string
  tags?: string[]
  status: string
  version: number
  author_id?: string
  assignee_id?: string
  reviewer_id?: string
  is_automated: boolean
  automation_status: string
  test_fingerprint?: string
  ai_generated: boolean
  ai_quality_score?: number
  ai_review_notes?: AIReviewResult
  estimated_duration_minutes?: number
  last_executed_at?: string
  last_execution_status?: string
  created_at: string
  updated_at: string
}

export interface TestCaseVersion {
  id: string
  test_case_id: string
  version: number
  title: string
  description?: string
  steps?: TestStep[]
  parameters?: TestCaseParameter[]
  expected_result?: string
  status: string
  changed_by_id?: string
  change_summary?: string
  change_type: string
  created_at: string
}

export interface TestCaseReview {
  id: string
  test_case_id: string
  reviewer_id?: string
  requested_by_id?: string
  status: string
  ai_review_completed: boolean
  ai_quality_score?: number
  ai_review_notes?: AIReviewResult
  ai_reviewed_at?: string
  human_notes?: string
  reviewed_at?: string
  created_at: string
  updated_at: string
}

export interface TestCaseComment {
  id: string
  test_case_id: string
  author_id?: string
  content: string
  comment_type: string
  parent_id?: string
  step_number?: number
  is_resolved: boolean
  created_at: string
  updated_at: string
}

export interface TestPlan {
  id: string
  project_id: string
  name: string
  description?: string
  objective?: string
  status: string
  planned_start_date?: string
  planned_end_date?: string
  actual_start_date?: string
  actual_end_date?: string
  created_by_id?: string
  assigned_to_id?: string
  ai_generated: boolean
  total_cases: number
  executed_cases: number
  passed_cases: number
  failed_cases: number
  blocked_cases: number
  created_at: string
  updated_at: string
}

export interface TestPlanItem {
  id: string
  plan_id: string
  test_case_id: string
  order_index: number
  priority_override?: string
  execution_status: string
  executed_by_id?: string
  executed_at?: string
  execution_notes?: string
  actual_duration_minutes?: number
  created_at: string
}

export interface RiskItem {
  risk: string
  likelihood: string
  impact: string
  mitigation: string
}

export interface TestTypeItem {
  type: string
  priority: string
  tools: string[]
  coverage_target_pct: number
  rationale: string
}

export interface EnvironmentItem {
  name: string
  type: string
  purpose: string
}

export interface TestStrategy {
  id: string
  project_id: string
  name: string
  version_label: string
  status: string
  objective?: string
  scope?: string
  out_of_scope?: string
  test_approach?: string
  risk_assessment?: RiskItem[]
  test_types?: TestTypeItem[]
  entry_criteria?: string[]
  exit_criteria?: string[]
  environments?: EnvironmentItem[]
  automation_approach?: string
  defect_management?: string
  ai_generated: boolean
  ai_model_used?: string
  created_by_id?: string
  approved_by_id?: string
  approved_at?: string
  created_at: string
  updated_at: string
}

export interface AuditLogEntry {
  id: string
  entity_type: string
  entity_id: string
  project_id?: string
  action: string
  actor_id?: string
  actor_name?: string
  old_values?: Record<string, unknown>
  new_values?: Record<string, unknown>
  details?: string
  created_at: string
}

export interface AIGenerateResponse {
  test_cases: Partial<ManagedTestCase>[]
  coverage_summary?: string
  gaps_noted: string[]
  created_ids: string[]
}

export interface AICoverageResponse {
  coverage_score?: number
  covered_areas?: string[]
  uncovered_areas?: string[]
  recommended_new_tests?: Array<{ title: string; priority: string; rationale: string }>
  summary?: string
}

// ── Duplicate Detection (Phase 4) ──────────────────────────────────────────
// Mirrors backend ``app.models.schemas`` Duplicate* models exactly.

export type DuplicateBand = 'exact' | 'strong' | 'possible'
export type DuplicateMethod = 'fingerprint' | 'structural' | 'semantic'
export type DuplicateCandidateStatus = 'open' | 'merged' | 'dismissed'

/** Minimal reference to one ManagedTestCase in a duplicate pair. */
export interface DuplicateCaseRef {
  id: string
  title: string
  suite_name?: string | null
  status?: string | null
}

/** One detected near-duplicate pair with its explainable score breakdown. */
export interface DuplicateCandidate {
  id: string
  project_id: string
  band: DuplicateBand
  score: number
  reason?: string | null
  method: DuplicateMethod
  component_scores?: Record<string, number> | null
  status: DuplicateCandidateStatus
  detected_at: string
  case_a: DuplicateCaseRef
  case_b: DuplicateCaseRef
}

/** Paginated list of duplicate candidates for a project's review queue. */
export interface DuplicateCandidateList {
  items: DuplicateCandidate[]
  total: number
  open_count: number
}

/** Non-destructive merge request body. */
export interface DuplicateMergeRequest {
  candidate_id: string
  keep_case_id: string
  deprecate_loser?: boolean
}

/** Result of a dismiss / merge action on a candidate pair. */
export interface DuplicateActionResult {
  candidate_id: string
  status: DuplicateCandidateStatus
  deprecated_case_id?: string | null
}

/** Summary of a triggered detection sweep over a project's authored cases. */
export interface DuplicateDetectionRunResult {
  project_id: string
  candidates_created: number
  candidates_total: number
  cases_scanned: number
  sampled: boolean
  note?: string | null
}
