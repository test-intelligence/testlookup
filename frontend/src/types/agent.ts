export type PipelineStatus = 'pending' | 'running' | 'completed' | 'failed' | 'partial'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface AgentStageResult {
  stage_name: string
  status: StageStatus
  started_at: string | null
  completed_at: string | null
  result_data: Record<string, unknown> | null
  error: string | null
  // Provenance fields added in migration 0017
  skipped_reason: string | null
  execution_path: string | null
  fallback_used: boolean | null
  // Phase 6: Observability fields
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  llm_calls_count: number | null
  cost_usd: number | null
  error_category: string | null
  confidence_score: number | null
  evidence_count: number | null
  route_rationale: string | null
}

export interface PipelineTimeline {
  schema_version: number
  pipeline_run_id: string
  workflow_type: string
  status: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  summary: {
    total_stages: number
    completed_stages: number
    running_stages: number
    failed_stages: number
    skipped_stages: number
    pending_stages: number
    progress_percent: number
  }
  cost_summary: {
    total_cost_usd: number
    total_input_tokens: number
    total_output_tokens: number
    total_tokens: number
    total_llm_calls: number
    stages: Array<{
      stage_name: string
      status: string
      duration_seconds: number | null
      input_tokens: number
      output_tokens: number
      total_tokens: number
      llm_calls_count: number
      cost_usd: number
      error_category: string | null
      fallback_used: boolean
      confidence_score: number | null
      evidence_count: number | null
      route_rationale: string | null
    }>
  }
  stages: AgentStageResult[]
  events: Array<{
    event_type: string
    stage_name: string | null
    test_case_id: string | null
    timestamp: string
    detail: Record<string, unknown>
  }>
  alerts: Array<{
    type: string
    severity: string
    message: string
    detail: Record<string, unknown>
  }>
}

export interface AgentPipelineRun {
  id: string
  test_run_id: string
  workflow_type: 'offline' | 'deep' | 'live'
  status: PipelineStatus
  started_at: string | null
  completed_at: string | null
  error: string | null
  created_at: string
  execution_metadata: Record<string, unknown> | null
  provenance_metadata: Record<string, unknown> | null
}

export interface RunSummary {
  test_run_id: string
  project_id: string
  build_number: string
  executive_summary: string
  markdown_report: string
  executive_panel?: Record<string, unknown> | null
  anomaly_count: number
  is_regression: boolean
  analysis_count: number
  generated_at: string
}

export interface ActiveLiveRun {
  run_id: string
  project_id: string
  build_number: string
  total: number
  passed: number
  failed: number
  skipped: number
  broken: number
  pass_rate: number
  current_test: string | null
  started_at: string
}
