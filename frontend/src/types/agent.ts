// Internal vocabulary of agent_pipeline_runs.status (E7.1, migration 0173).
// `partial` is retired: a finished run with failed stages is `completed` with
// execution_metadata.stage_quality === 'degraded' (see isDegradedPipeline).
export type PipelineStatus = 'pending' | 'running' | 'retry_wait' | 'completed' | 'passed' | 'failed'
// Four-value public projection carried as `public_status` on pipeline responses.
export type PublicPipelineStatus = 'in_progress' | 'completed' | 'failed' | 'passed'

// Mirror of backend workflow_run_state.PUBLIC_STATUS (+ its legacy map), used
// when a payload predates `public_status` (a cached response, an older API).
const PUBLIC_BY_INTERNAL: Record<string, PublicPipelineStatus> = {
  pending: 'in_progress',
  running: 'in_progress',
  retry_wait: 'in_progress',
  in_progress: 'in_progress',
  completed: 'completed',
  partial: 'completed',
  passed: 'passed',
  failed: 'failed',
  cancelled: 'failed',
  canceled: 'failed',
}

export const PUBLIC_PIPELINE_STATUS_LABEL: Record<PublicPipelineStatus, string> = {
  in_progress: 'IN PROGRESS',
  completed: 'COMPLETED',
  failed: 'FAILED',
  passed: 'PASSED',
}

/**
 * The four-value status a user should see (E7.5). Prefers the server's
 * `public_status`; otherwise projects `status` the way the backend does. An
 * empty status is in progress (the backend reads a missing one as `pending`);
 * an unrecognised one is `failed`, never a spinner that spins forever.
 */
export function publicPipelineStatus(
  p: { status?: string | null; public_status?: string | null } | null | undefined,
): PublicPipelineStatus {
  const explicit = (p?.public_status ?? '').toLowerCase()
  if (explicit in PUBLIC_PIPELINE_STATUS_LABEL) return explicit as PublicPipelineStatus
  const raw = (p?.status ?? '').toLowerCase()
  if (!raw) return 'in_progress'
  return PUBLIC_BY_INTERNAL[raw] ?? 'failed'
}

/** True for every internal state that projects to `in_progress`, not just `running`. */
export function isPipelineInProgress(status: string | null | undefined): boolean {
  if (!status) return false
  return PUBLIC_BY_INTERNAL[status.toLowerCase()] === 'in_progress'
}

export function isDegradedPipeline(p: { status?: string | null; execution_metadata?: unknown } | null | undefined): boolean {
  if (!p) return false
  const meta = p.execution_metadata as { stage_quality?: unknown } | null | undefined
  return (p.status ?? '').toLowerCase() === 'completed' && meta?.stage_quality === 'degraded'
}
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
  public_status?: PublicPipelineStatus | null
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
  agent_observability?: {
    schema_version: number
    stage_count: number
    status_counts: Record<string, number>
    latency: {
      total_stage_duration_seconds: number
      max_stage_duration_seconds: number | null
      avg_stage_duration_seconds: number | null
    }
    tokens: {
      input: number
      output: number
      total: number
      llm_calls: number
    }
    cost: {
      total_usd: number
      budget_usd: number
    }
    fallback: {
      count: number
      rate: number
      stages: string[]
    }
    errors: {
      count: number
      by_category: Record<string, number>
    }
    quality: {
      avg_confidence_score: number | null
      total_evidence_count: number
    }
    alerts: {
      count: number
      by_type: Record<string, number>
    }
    per_agent: Array<{
      stage_name: string
      status: string
      duration_seconds: number | null
      input_tokens: number
      output_tokens: number
      total_tokens: number
      llm_calls_count: number
      cost_usd: number
      fallback_used: boolean
      fallback_reason: string | null
      error_category: string | null
      confidence_score: number | null
      evidence_count: number
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
    routing?: {
      primary_owner: string
      escalation_owner: string
      priority: string
      recommended_action: string
    }
  }>
}

export interface AgentPipelineRun {
  id: string
  test_run_id: string
  workflow_type: 'offline' | 'deep' | 'live'
  status: PipelineStatus
  // E7.1/E7.5: render this, not `status`. Optional so a cached or older
  // payload still type-checks; publicPipelineStatus() falls back to `status`.
  public_status?: PublicPipelineStatus | null
  // E7.2/E7.4 retry and cancel state.
  attempt?: number | null
  max_attempts?: number | null
  next_retry_at?: string | null
  cancel_requested?: boolean | null
  rerun_of?: string | null
  started_at: string | null
  completed_at: string | null
  error: string | null
  created_at: string
  execution_metadata: Record<string, unknown> | null
  provenance_metadata: Record<string, unknown> | null
  // Owning-run context (which run/suite this pipeline analysed), attached by the
  // backend so the /agents cards show "Run #N · <suite>" not just a workflow
  // type. Null for legacy rows whose TestRun is missing / run_seq uncomputable.
  build_number?: string | null
  run_seq?: number | null
  suite_name?: string | null
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
  // Run-level suite (testlookup.suite > testlookup.launch). Present once the
  // backend's live-session row carries suite_name; older runs may omit it.
  suite_name?: string | null
  // Per-(project, primary_suite_name) human-readable run number. Null
  // for very-new active sessions whose TestRun row hasn't been created
  // yet (the Phase 4.5 drain creates it ~30s after the first event);
  // the UI falls back to ``Build {build_number}`` in that window.
  run_seq?: number | null
}
