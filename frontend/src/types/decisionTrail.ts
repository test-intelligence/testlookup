// Types mirror backend/app/models/schemas.py DecisionTrailResponse.

export interface DecisionLogEntry {
  at: string
  decision_point: string
  chosen: string
  rationale: string
  alternatives?: string[] | null
  test_case_id?: string | null
  context?: Record<string, unknown> | null
}

export interface StageDecisionSummary {
  stage_name: string
  status: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  analysis_mode: string | null
  fallback_used: boolean | null
  fallback_reason: string | null
  route_rationale: string | null
  error_category: string | null
  skipped_reason: string | null
  execution_path: string | null
  confidence_score: number | null
  evidence_count: number | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
  decision_log: DecisionLogEntry[]
}

export interface WorkflowDecisionEvent {
  at: string
  decision_point: string
  chosen: string
  rationale: string
  alternatives?: string[] | null
  context?: Record<string, unknown> | null
}

export interface PerTestRouting {
  test_case_id: string
  test_name: string | null
  analysis_mode: string | null
  mode_requested: string | null
  fallback_from: string | null
  fallback_reason: string | null
  confidence_adjustments: Array<Record<string, unknown>> | null
  retry_count: number | null
  duration_seconds: number | null
}

export interface DecisionTrailResponse {
  run_id: string
  pipeline_run_id: string | null
  workflow_type: string | null
  pipeline_status: string | null
  started_at: string | null
  completed_at: string | null
  total_cost_usd: number
  total_tokens: number
  stages: StageDecisionSummary[]
  workflow_events: WorkflowDecisionEvent[]
  per_test: PerTestRouting[]
  mode_distribution: Record<string, number>
  fallback_count: number
}
