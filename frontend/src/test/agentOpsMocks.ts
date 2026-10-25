/**
 * Stubs for `@/hooks/useAgentOps` (W14), for the page vitests. One set-up line
 * per file:
 *
 *   vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())
 *
 * Every hook is a `vi.fn` answering the IDLE SWR shape (no data, no error, not
 * loading, a `mutate`), so a page renders without a request
 * (`src/test/networkGuard.ts` fails a test that makes one). Data per hook:
 * `agentOpsHookMocks({ usePreScan: { data: PRESCAN_FIXTURE } })`, or
 * `vi.mocked(usePreScan).mockReturnValue(...)` in a case.
 *
 * Deliberately imports nothing from `@/hooks/useAgentOps`: this module is
 * loaded INSIDE that module's mock factory (that module exports hooks only).
 */
import { vi, type Mock } from 'vitest'

import type {
  AgenticRun,
  DeepEstimate,
  DeepHistory,
  DeepPreScan,
  PipelineCompare,
  PipelineReplay,
} from '@/types/agentOps'

/** The hooks of `@/hooks/useAgentOps`, in the module's order. */
export const AGENT_OPS_HOOK_NAMES = [
  'usePipelineReplay',
  'useAgenticRuntime',
  'usePipelineCompare',
  'useWorkflowChoices',
  'useWorkflowDefaults',
  'useBulkJob',
  'usePreScan',
  'useDeepHistory',
  'useDeepEstimate',
] as const

export type AgentOpsHookName = (typeof AGENT_OPS_HOOK_NAMES)[number]

/** An SWR answer; `T` is inferred from `data`, so `vi.mocked(usePreScan).mockReturnValue(idleSwr({ data }))` type-checks. */
export interface SwrStub<T = unknown> {
  data: T | undefined
  error: unknown
  isLoading: boolean
  isValidating: boolean
  mutate: Mock
}

/** The idle SWR answer: no data, no error, not loading, a fresh `mutate`. */
export function idleSwr<T = never>(overrides: Partial<SwrStub<T>> = {}): SwrStub<T> {
  return { data: undefined, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn(), ...overrides }
}

/** Every `useAgentOps` hook as a `vi.fn` answering `idleSwr()` merged with that hook's override. */
export function agentOpsHookMocks(
  overrides: Partial<Record<AgentOpsHookName, Partial<SwrStub>>> = {},
): Record<AgentOpsHookName, Mock> {
  const mocks = {} as Record<AgentOpsHookName, Mock>
  for (const name of AGENT_OPS_HOOK_NAMES) {
    const answer = idleSwr(overrides[name])
    mocks[name] = vi.fn(() => answer)
  }
  return mocks
}

// ── AGT-05: an old pipeline's replay document with TWO gaps (a missing
//    checkpoint and no final-state checksum); seven stage rows. ──
const REPLAY_STAGES = [
  'ingestion', 'anomaly_detection', 'root_cause_analysis', 'summary', 'triage', 'failure_clustering', 'release_risk',
]

export const REPLAY_FIXTURE: PipelineReplay = {
  schema_version: 1,
  pipeline_run_id: 'pipe-replay-1',
  test_run_id: 'run-replay-1',
  workflow_type: 'deep',
  status: 'completed',
  started_at: '2026-10-20T10:00:00Z',
  completed_at: '2026-10-20T10:04:12Z',
  analysis_mode_requested: 'auto',
  analysis_mode_resolved: 'llm',
  analysis_mode_resolution: { source: 'settings' },
  final_state_checksum_sha256: null,
  runtime_versions: { workflow: '2' },
  workflow_plan: { workflow_id: 'deep', version: 1 },
  workflow_verification: {},
  route_decisions: [
    { at: '2026-10-20T10:00:05Z', decision_point: 'ingestion', chosen: 'root_cause_analysis', failed_count: 12 },
  ],
  stage_replay: REPLAY_STAGES.map((stage, index) => ({
    stage_name: stage,
    status: 'completed',
    started_at: `2026-10-20T10:0${index}:00Z`,
    completed_at: `2026-10-20T10:0${index}:30Z`,
    input_checksum_sha256: `${index}`.repeat(64),
    output_checksum_sha256: `${(index + 1) % 10}`.repeat(64),
    runtime_versions: {},
    checkpoint_available: stage !== 'summary',
    restored_from_checkpoint: false,
    decision_count: stage === 'ingestion' ? 1 : 0,
  })),
  memory_references: [
    {
      memory_entry_id: '00000000-0000-4000-8000-0000000000aa',
      entity_type: 'failure_cluster',
      entity_id: 'cluster-1',
      payload_sha256: 'a'.repeat(64),
      memory_reference_id: 'mem-1',
    },
  ],
  events: [
    { event_type: 'stage_started', stage_name: 'ingestion', timestamp: '2026-10-20T10:00:00Z', detail: {} },
    { event_type: 'stage_completed', stage_name: 'ingestion', timestamp: '2026-10-20T10:00:30Z', detail: { failed: 12 } },
  ],
  event_counts: { stage_started: 7, stage_completed: 7 },
  replayable: false,
  audit_gaps: {
    missing_start_events: [],
    missing_terminal_events: [],
    missing_replay_checksums: [],
    missing_checkpoints: ['summary'],
    missing_final_state_checksum: true,
  },
}

/** The same document with nothing missing: `Replayable ✓`. */
export const REPLAY_REPLAYABLE_FIXTURE: PipelineReplay = {
  ...REPLAY_FIXTURE,
  final_state_checksum_sha256: 'f'.repeat(64),
  stage_replay: REPLAY_FIXTURE.stage_replay.map((stage) => ({ ...stage, checkpoint_available: true })),
  replayable: true,
  audit_gaps: {
    missing_start_events: [],
    missing_terminal_events: [],
    missing_replay_checksums: [],
    missing_checkpoints: [],
    missing_final_state_checksum: false,
  },
}

// ── AGT-06: an investigation's task graph: plan → five hypotheses → synthesis
//    (plus the root task the canvas leaves out). ──
const INVESTIGATION_PIPELINE = 'pipe-investigation-1'
const HYPOTHESES = [
  'hypothesis_infra', 'hypothesis_commit', 'hypothesis_environment', 'hypothesis_known_flaky', 'hypothesis_regression',
]

function runtimeTask(
  stage: string, status: string, dependencies: string[], extra: Partial<AgenticRun['tasks'][number]> = {},
): AgenticRun['tasks'][number] {
  return {
    task_id: `${INVESTIGATION_PIPELINE}:${stage}`,
    parent_task_id: stage === 'pipeline' ? null : `${INVESTIGATION_PIPELINE}:pipeline`,
    source_pipeline_run_id: INVESTIGATION_PIPELINE,
    capability_id: `agent.${stage}.v1`,
    stage_name: stage,
    status,
    selected: true,
    required: stage === 'investigator_plan',
    selection_reason: 'planned',
    dependencies,
    attempt: 1,
    usage: { input_tokens: 400, output_tokens: 100, total_tokens: 500, llm_calls: 1, cost_usd: 0, duration_ms: 1200 },
    stop_reason: status === 'running' ? null : status === 'failed' ? 'capability_error' : 'completed',
    ...extra,
  }
}

export const RUNTIME_INVESTIGATION_FIXTURE: AgenticRun = {
  schema_version: 1,
  agentic_run_id: `agentic:${INVESTIGATION_PIPELINE}`,
  source_pipeline_run_id: INVESTIGATION_PIPELINE,
  test_run_id: 'run-investigation-1',
  workflow_type: 'investigation',
  status: 'running',
  public_status: 'in_progress',
  root_task_id: `${INVESTIGATION_PIPELINE}:pipeline`,
  plan_id: 'plan-investigation-1',
  plan_sha256: null,
  plan_integrity_status: 'legacy_unhashed',
  planner_version: 'investigator-1',
  tasks: [
    runtimeTask('pipeline', 'running', []),
    runtimeTask('investigator_plan', 'completed', [], { usage: undefined }),
    ...HYPOTHESES.map((stage, index) =>
      runtimeTask(stage, index === 1 ? 'failed' : index === 4 ? 'running' : 'completed', ['investigator_plan']),
    ),
    runtimeTask('investigator_synthesis', 'pending', HYPOTHESES, { stop_reason: null }),
  ],
  child_agentic_run_ids: [],
  observed_child_runs: 0,
  children_truncated: false,
  observed_task_rows: 8,
  tasks_truncated: false,
  started_at: '2026-10-20T11:00:00Z',
  completed_at: null,
}

// ── AGT-05: a rerun pair of one run; ONE stage's output changed. ──
function compareCell(checksum: string, durationSeconds: number, tokens: number | null, cost: number | null) {
  return {
    status: 'completed',
    duration_seconds: durationSeconds,
    total_tokens: tokens,
    cost_usd: cost,
    fallback_used: false,
    confidence_score: 0.8,
    execution_path: 'llm',
    output_checksum_sha256: checksum,
  }
}

export const COMPARE_FIXTURE: PipelineCompare = {
  a: {
    pipeline_run_id: 'pipe-a', test_run_id: 'run-compare-1', workflow_type: 'offline', workflow_ref: 'offline@1',
    status: 'completed', public_status: 'completed', attempt: 1, rerun_of: null,
    created_at: '2026-10-20T09:00:00Z', started_at: '2026-10-20T09:00:01Z', completed_at: '2026-10-20T09:02:01Z',
    duration_seconds: 120, analysis_mode_resolved: 'llm', release_verdict: null, total_tokens: 1500, cost_usd: 0.012,
    stages_truncated: false,
  },
  b: {
    pipeline_run_id: 'pipe-b', test_run_id: 'run-compare-1', workflow_type: 'offline', workflow_ref: 'offline@1',
    status: 'passed', public_status: 'passed', attempt: 2, rerun_of: 'pipe-a',
    created_at: '2026-10-20T10:00:00Z', started_at: '2026-10-20T10:00:01Z', completed_at: '2026-10-20T10:01:31Z',
    duration_seconds: 90, analysis_mode_resolved: 'llm', release_verdict: null, total_tokens: 1300, cost_usd: 0.01,
    stages_truncated: false,
  },
  same_test_run: true,
  output: 'changed',
  changed_stages: 1,
  total: 2,
  stages: [
    {
      stage_name: 'ingestion', a: compareCell('1'.repeat(64), 5, null, null), b: compareCell('1'.repeat(64), 5, null, null),
      changed: [], output_checksum_equal: true, duration_delta_seconds: 0, tokens_delta: null, cost_delta_usd: null,
    },
    {
      stage_name: 'root_cause_analysis', a: compareCell('2'.repeat(64), 60, 1500, 0.012),
      b: compareCell('3'.repeat(64), 45, 1300, 0.01),
      changed: ['duration_seconds', 'total_tokens', 'cost_usd', 'output_checksum_sha256'],
      output_checksum_equal: false, duration_delta_seconds: -15, tokens_delta: -200, cost_delta_usd: -0.002,
    },
  ],
}

// ── AGT-07: a run's pre-scan (3 signature groups among 12 failing results,
//    refined by the last analysis's clusters), the project's history (4
//    investigated runs) and a measured estimate on a local model. ──
const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

export const PRESCAN_FIXTURE: DeepPreScan = {
  run_id: 'run-1',
  measured: true,
  failing_results: 12,
  groups_total: 3,
  groups: [
    {
      signature_id: 'sig-timeout', representative_error: 'TimeoutError: page.goto exceeded 30000 ms', members: 7,
      member_result_ids: [1, 2, 3, 4, 5, 6, 7].map(uuid), members_truncated: false, suites: ['checkout', 'cart'],
      cluster_id: 'cluster-1', cluster_label: 'Navigation timeouts',
    },
    {
      signature_id: 'sig-assert', representative_error: 'AssertionError: expected 200 to equal 500', members: 3,
      member_result_ids: [8, 9, 10].map(uuid), members_truncated: false, suites: ['payments'],
      cluster_id: 'cluster-2', cluster_label: 'Payment status mismatch',
    },
    {
      signature_id: 'sig-null', representative_error: "TypeError: Cannot read properties of null (reading 'id')",
      members: 2, member_result_ids: [11, 12].map(uuid), members_truncated: false, suites: ['profile'],
      cluster_id: null, cluster_label: null,
    },
  ],
  grouped_by: 'error_signature',
  refinement: 'clusters',
  refinement_note: "Grouped by error signature; the last deep analysis's clusters are shown beside each group.",
}

function historyRow(n: number, extra: Partial<DeepHistory['items'][number]> = {}): DeepHistory['items'][number] {
  return {
    run_id: `run-${n}`, run_seq: 40 - n, build_number: `build-${40 - n}`, suite_name: 'e2e',
    run_created_at: `2026-10-${String(20 - n).padStart(2, '0')}T08:00:00Z`, failures: 10 + n,
    pipeline_run_id: `pipe-deep-${n}`, status: 'completed', public_status: 'completed',
    started_at: `2026-10-${String(20 - n).padStart(2, '0')}T08:01:00Z`,
    completed_at: `2026-10-${String(20 - n).padStart(2, '0')}T08:05:00Z`, duration_seconds: 240,
    clusters: 3, findings: 4, avg_confidence: 0.82, cost_usd: 0, ...extra,
  }
}

export const HISTORY_FIXTURE: DeepHistory = {
  project_id: 'project-1',
  total: 4,
  page: 1,
  size: 10,
  items: [
    historyRow(1),
    historyRow(2, { clusters: 2, findings: 3, avg_confidence: 0.91 }),
    historyRow(3, { status: 'failed', public_status: 'failed', clusters: null, findings: 0, avg_confidence: null }),
    historyRow(4, { cost_usd: null }),
  ],
}

export const ESTIMATE_FIXTURE: DeepEstimate = {
  run_id: 'run-1',
  gate: {
    allowed: true, reason_code: null, message: null, analysis_mode: 'llm', deep_investigation_enabled: true,
  },
  configuration: {
    selected_failures: null, failures_analysed: 12, max_failures_analyzed: 50, project_max_failures_analyzed: 50,
    model_tier: 'slm', project_model_tier: 'slm', allowed_tiers: ['deterministic', 'slm'],
    evidence_sources: ['logs', 'history'], project_evidence_sources: ['logs', 'history'],
    tool_narrowing_allowed: true,
  },
  measured: true,
  reason: null,
  sample_pipelines: 12,
  minutes_p50: 4,
  tokens_p50: 18_000,
  cost: {
    priced: true, local_model: true, usd: 0, provider: 'ollama', model: 'mistral-nemo',
    note: 'local model — no charge', price_table_updated: '2026-08-09',
  },
  stages: [
    { stage_name: 'root_cause_analysis', samples: 12, duration_seconds_p50: 120, input_tokens_p50: 9_000, output_tokens_p50: 3_000, per_failure: true },
    { stage_name: 'triage', samples: 12, duration_seconds_p50: 45, input_tokens_p50: 3_000, output_tokens_p50: 1_000, per_failure: false },
    { stage_name: 'summary', samples: 12, duration_seconds_p50: 30, input_tokens_p50: 1_500, output_tokens_p50: 500, per_failure: false },
    { stage_name: 'failure_clustering', samples: 12, duration_seconds_p50: 20, input_tokens_p50: null, output_tokens_p50: null, per_failure: false },
  ],
}
