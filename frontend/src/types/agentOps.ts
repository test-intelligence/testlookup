/**
 * W14 "agent-ops" wire shapes (AGT-05, AGT-06, AGT-08, AGT-12, AGT-07).
 *
 * Mirrors `backend/app/models/schemas.py` (the W14 §1.1 shapes, the replay
 * document) and `backend/app/models/agentic_runtime.py` (only the fields the
 * canvas reads). Enum-like fields are `string`; the few vocabularies the UI
 * renders are mirrored below and pinned against `core/agent_ops_vocab.py` by
 * `backend/tests/test_agent_ops_vocab_frontend_parity.py`.
 */

// ── mirrored vocabularies (pinned by the backend parity test) ──
export const COMPARE_OUTPUT_STATES = ['identical', 'changed', 'unknown'] as const
export const BULK_JOB_STATES = ['active', 'finished', 'cancelled'] as const
export const WORKFLOW_DEFAULT_BASES = ['offline', 'deep'] as const
export const TIER_ORDER = ['deterministic', 'slm', 'llm', 'auto'] as const

export type CompareOutputState = (typeof COMPARE_OUTPUT_STATES)[number]
export type BulkJobState = (typeof BULK_JOB_STATES)[number]
export type WorkflowDefaultBase = (typeof WORKFLOW_DEFAULT_BASES)[number]

// ── AGT-05: the replay (audit) document, GET /agents/pipelines/{id}/replay ──
export interface PipelineReplayAuditGaps {
  missing_start_events: string[]
  missing_terminal_events: string[]
  missing_replay_checksums: string[]
  missing_checkpoints: string[]
  missing_final_state_checksum: boolean
}

export interface PipelineReplayIntegrity {
  replayable: boolean
  audit_gaps: PipelineReplayAuditGaps
}

export interface PipelineReplayStage {
  stage_name: string
  status: string
  started_at?: string | null
  completed_at?: string | null
  input_checksum_sha256?: string | null
  output_checksum_sha256?: string | null
  runtime_versions?: Record<string, string>
  checkpoint_available: boolean
  restored_from_checkpoint: boolean
  decision_count: number
}

export interface PipelineReplayEvent {
  event_type: string
  stage_name?: string | null
  test_case_id?: string | null
  timestamp?: string | null
  detail: Record<string, unknown>
  source?: string | null
}

export interface PipelineMemoryReference {
  memory_entry_id: string
  entity_type: string
  entity_id: string
  source_snapshot_id?: string | null
  payload_sha256: string
  retrieval_audit?: Record<string, unknown> | null
  retrieval_audit_sha256?: string | null
  memory_reference_id?: string | null
  evidence_refs?: Array<Record<string, unknown>>
}

export interface PipelineReplay {
  schema_version: number
  pipeline_run_id: string
  test_run_id: string
  workflow_type: string
  status: string
  started_at?: string | null
  completed_at?: string | null
  analysis_mode_requested?: string | null
  analysis_mode_resolved?: string | null
  analysis_mode_resolution?: Record<string, unknown>
  final_state_checksum_sha256?: string | null
  runtime_versions?: Record<string, string>
  workflow_plan?: Record<string, unknown>
  workflow_verification?: Record<string, unknown>
  route_decisions: Array<Record<string, unknown>>
  stage_replay: PipelineReplayStage[]
  memory_references: PipelineMemoryReference[]
  events: PipelineReplayEvent[]
  event_counts: Record<string, number>
  replayable: boolean
  audit_gaps: PipelineReplayAuditGaps
}

// ── AGT-06: the agentic runtime projection, GET /agents/pipelines/{id}/agentic-runtime ──
export interface AgenticTaskBudget {
  max_llm_calls?: number | null
  max_tokens?: number | null
  max_cost_usd?: number | null
  max_seconds?: number | null
  max_retries?: number | null
}

export interface AgenticTaskUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  llm_calls: number
  cost_usd: number
  duration_ms: number
}

export interface AgenticTask {
  task_id: string
  parent_task_id?: string | null
  source_pipeline_run_id?: string | null
  failure_cluster_id?: string | null
  capability_id: string
  stage_name: string
  /** pending | running | completed | failed | skipped | cancelled | partial */
  status: string
  selected: boolean
  required?: boolean
  selection_reason: string
  dependencies: string[]
  attempt: number
  budget?: AgenticTaskBudget
  usage?: AgenticTaskUsage
  stop_reason?: string | null
  started_at?: string | null
  completed_at?: string | null
  error?: string | null
}

export interface AgenticRun {
  schema_version: number
  agentic_run_id: string
  source_pipeline_run_id: string
  test_run_id: string
  workflow_type: string
  status: string
  public_status: string
  root_task_id: string
  plan_id: string
  plan_sha256?: string | null
  plan_integrity_status?: string
  planner_version?: string
  tasks: AgenticTask[]
  child_agentic_run_ids?: string[]
  observed_child_runs?: number
  children_truncated: boolean
  observed_task_rows: number
  tasks_truncated: boolean
  started_at?: string | null
  completed_at?: string | null
}

// ── AGT-05: pipeline compare, GET /agents/pipelines/compare?a=&b= ──
export interface PipelineCompareStageCell {
  status: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  total_tokens?: number | null
  cost_usd?: number | null
  fallback_used?: boolean | null
  confidence_score?: number | null
  execution_path?: string | null
  output_checksum_sha256?: string | null
}

export interface PipelineCompareStageRow {
  stage_name: string
  a: PipelineCompareStageCell | null
  b: PipelineCompareStageCell | null
  /** COMPARE_FIELDS that differ; ['presence'] when a side is missing. */
  changed: string[]
  output_checksum_equal?: boolean | null
  duration_delta_seconds?: number | null
  tokens_delta?: number | null
  cost_delta_usd?: number | null
}

export interface PipelineCompareSide {
  pipeline_run_id: string
  test_run_id: string
  workflow_type: string
  workflow_ref?: string | null
  status: string
  public_status: string
  attempt?: number | null
  rerun_of?: string | null
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  analysis_mode_resolved?: string | null
  release_verdict?: string | null
  total_tokens?: number | null
  cost_usd?: number | null
  stages_truncated: boolean
}

export interface PipelineCompare {
  a: PipelineCompareSide
  b: PipelineCompareSide
  same_test_run: boolean
  /** COMPARE_OUTPUT_STATES */
  output: string
  changed_stages: number
  total: number
  stages: PipelineCompareStageRow[]
}

// ── AGT-08: project default workflows, GET/PUT/DELETE /projects/{p}/workflows/defaults[/{base}] ──
export interface WorkflowDefault {
  /** WORKFLOW_DEFAULT_BASES */
  base: string
  /** 'project_default' | 'builtin' */
  source: string
  workflow_id: string
  workflow_version?: number | null
  resolved_ref?: string | null
  resolved_name?: string | null
  /** 'ok' | 'unavailable' */
  state: string
  reason?: string | null
  model_stages: string[]
  updated_by?: string | null
  updated_at?: string | null
}

export interface WorkflowDefaults {
  project_id: string
  /** Always both bases, in WORKFLOW_DEFAULT_BASES order. */
  defaults: WorkflowDefault[]
}

// ── AGT-12: bulk jobs ──
/** The Runs list's own filter, resolved on the server. */
export interface BulkRunFilter {
  project_id?: string | null
  release_id?: string[] | null
  days?: number | null
  status?: string | null
  suite_name?: string[] | null
  q?: string | null
  branch?: string[] | null
  recommendation?: string | null
}

/** What `POST /agents/pipelines/bulk-trigger` answers (every W14 field optional for an older payload). */
export interface BulkTriggerResult {
  queued: number
  not_found: number
  workflow_type: string
  not_found_ids: string[]
  requested?: number
  skipped_in_progress?: number
  skipped_in_progress_ids?: string[]
  /** BULK_SOURCES: selection | filter */
  source?: string
  matched?: number | null
  truncated?: boolean
  bulk_job_id?: string | null
  tracking?: boolean
  replayed?: boolean
}

export interface BulkJobProgress {
  started: number
  in_progress: number
  completed: number
  passed: number
  failed: number
  not_started?: number | null
}

export interface BulkJob {
  bulk_job_id: string
  workflow_type: string
  source: string
  /** BULK_JOB_STATES */
  state: string
  /** 'redis' | 'postgres' */
  counters_source: string
  created_at?: string | null
  cancelled_at?: string | null
  requested?: number | null
  queued?: number | null
  skipped_in_progress?: number | null
  not_found?: number | null
  matched?: number | null
  truncated: boolean
  progress: BulkJobProgress
  note?: string | null
}

// ── AGT-07: deep investigation insights ──
export interface PreScanGroup {
  signature_id: string
  representative_error?: string | null
  members: number
  /** ≤ 500 ids, ordered by id: what "Run on selection" sends. */
  member_result_ids: string[]
  members_truncated: boolean
  suites: string[]
  cluster_id?: string | null
  cluster_label?: string | null
}

export interface DeepPreScan {
  run_id: string
  measured: boolean
  failing_results: number
  /** Every signature group (a COUNT), never groups.length. */
  groups_total: number
  groups: PreScanGroup[]
  grouped_by: string
  /** PRESCAN_REFINEMENTS: clusters | signature_only */
  refinement: string
  refinement_note: string
}

export interface DeepHistoryRow {
  run_id: string
  run_seq?: number | null
  build_number?: string | null
  suite_name?: string | null
  run_created_at?: string | null
  failures: number
  pipeline_run_id: string
  status: string
  public_status: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  clusters?: number | null
  findings: number
  /** 0..1; null = no scored finding. */
  avg_confidence?: number | null
  cost_usd?: number | null
}

export interface DeepHistory {
  project_id: string
  total: number
  page: number
  size: number
  items: DeepHistoryRow[]
}

export interface DeepGate {
  allowed: boolean
  /** 'disabled' | 'rules_mode' | null */
  reason_code?: string | null
  message?: string | null
  /** The RESOLVED mode. */
  analysis_mode: string
  deep_investigation_enabled: boolean
}

export interface DeepRunConfiguration {
  selected_failures?: number | null
  failures_analysed: number
  max_failures_analyzed: number
  project_max_failures_analyzed: number
  model_tier: string
  project_model_tier: string
  allowed_tiers: string[]
  evidence_sources: string[]
  project_evidence_sources: string[]
  tool_narrowing_allowed: boolean
}

export interface DeepCost {
  /** false: no price known (usd null) — never a quiet $0.00 */
  priced: boolean
  /** a self-hosted endpoint: usd 0 is a measured answer */
  local_model: boolean
  usd?: number | null
  provider?: string | null
  model?: string | null
  note?: string | null
  price_table_updated: string
}

export interface DeepEstimateStage {
  stage_name: string
  samples: number
  duration_seconds_p50?: number | null
  input_tokens_p50?: number | null
  output_tokens_p50?: number | null
  per_failure: boolean
}

export interface DeepEstimate {
  run_id: string
  gate: DeepGate
  configuration: DeepRunConfiguration
  measured: boolean
  reason?: string | null
  sample_pipelines: number
  /** null when not measured (never 0). */
  minutes_p50?: number | null
  tokens_p50?: number | null
  cost: DeepCost
  stages: DeepEstimateStage[]
}

/** The optional run configuration `POST /deep-investigate/{run_id}` takes. */
export interface DeepRunRequest {
  failure_ids?: string[]
  max_failures_analyzed?: number
  model_tier?: string
  evidence_sources?: string[]
}

/** The estimate's query (`GET /deep-investigate/{run_id}/estimate`); arrays repeat their key. */
export interface DeepEstimateQuery {
  selected_failures?: number
  max_failures_analyzed?: number
  model_tier?: string
  evidence_sources?: string[]
}

/** What `POST /deep-investigate/{run_id}` answers (the W14 fields optional for an older payload). */
export interface TriggerDeepResult {
  task_id?: string | null
  pipeline_run_id?: string | null
  message: string
  run_id: string
  already_in_progress?: boolean
  run_configuration?: DeepRunConfiguration | null
}
