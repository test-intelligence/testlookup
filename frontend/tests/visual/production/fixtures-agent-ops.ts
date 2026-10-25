/**
 * W14 G-D hermetic worlds (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.1-§2.3, §6.2):
 *
 *  - `auditWorld({ replayable })`   `/agents?run=` whose pipeline's replay document has gaps (or none);
 *  - `compareWorld()`               a rerun pair of one run, ONE stage's output changed;
 *  - `investigationGraphWorld()`    an investigation pipeline (plan → five hypotheses → synthesis) beside an
 *                                   offline one whose stages all map onto the hand canvas;
 *  - `workflowDefaultWorld({ role })` `/agents/workflows` with a published fork, the defaults read / PUT / DELETE,
 *                                   and the picker's reads on `/agents?run=` and the Run page.
 *
 * Each world is fresh state per call and records the writes it answered (`writes`, with their bodies). Its
 * answers are PREPENDED to the shared ones (`RUN_PAGE` through `agentsHandlers`): first match wins, so a world
 * only overrides what it names. Not a spec file; `fixtures.ts` / `fixtures-run.ts` are G-A's and untouched.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, PROJECT_ID, RUN_ID, USER, workflowDefaultsBody } from './fixtures'
import { agentsHandlers } from './fixtures-pages'
import { RUN } from './fixtures-run'

/** One answered write: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

export interface AgentOpsWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

/** `fixtures-run.ts`'s pipeline of `RUN_ID` (its stages, timeline, runtime and the run's report are answered there). */
export const ORIGIN_PIPELINE_ID = '77777777-7777-4777-8777-777777777777'
/** The rerun of `ORIGIN_PIPELINE_ID` (attempt 2), newest: `/agents?run=` opens it. */
export const RERUN_PIPELINE_ID = 'd1400000-0000-4000-8000-000000000002'
export const INVESTIGATION_PIPELINE_ID = 'd1400000-0000-4000-8000-000000000003'
export const OFFLINE_PIPELINE_ID = 'd1400000-0000-4000-8000-000000000004'

const OFFLINE_STAGE_NAMES = ['ingestion', 'anomaly_detection', 'root_cause_analysis', 'summary', 'triage']
export const HYPOTHESES = ['hypothesis_infra', 'hypothesis_commit', 'hypothesis_environment', 'hypothesis_known_flaky', 'hypothesis_regression']
export const INVESTIGATION_STAGE_NAMES = ['investigator_plan', ...HYPOTHESES, 'investigator_synthesis']

function pipelineRow(id: string, minutesAgo: number, fields: Record<string, unknown> = {}) {
  const started = isoAgo(0, minutesAgo / 60)
  return {
    id, test_run_id: RUN_ID, workflow_type: 'offline', status: 'completed', public_status: 'completed',
    attempt: 1, max_attempts: 3, next_retry_at: null, cancel_requested: false, rerun_of: null,
    started_at: started, completed_at: isoAgo(0, (minutesAgo - 2) / 60), error: null, created_at: started,
    execution_metadata: {}, provenance_metadata: null, build_number: String(RUN.build_number), run_seq: RUN.run_seq,
    suite_name: 'Auth', review_summary: null, ...fields,
  }
}

function stageRow(stage_name: string, i: number, minutesAgo: number, fields: Record<string, unknown> = {}) {
  return {
    stage_name, status: 'completed', started_at: isoAgo(0, (minutesAgo - i * 0.2) / 60),
    completed_at: isoAgo(0, (minutesAgo - i * 0.2 - 0.1) / 60), result_data: null, error: null, skipped_reason: null,
    execution_path: 'rules', fallback_used: false, input_tokens: null, output_tokens: null, total_tokens: null,
    llm_calls_count: null, cost_usd: null, error_category: null, confidence_score: null, evidence_count: null,
    route_rationale: null, ...fields,
  }
}

const NO_GAPS = {
  missing_start_events: [], missing_terminal_events: [], missing_replay_checksums: [], missing_checkpoints: [],
  missing_final_state_checksum: false,
}
/** An old pipeline's gaps: two stages without a checkpoint, one without a start event, no final checksum. */
const OLD_GAPS = {
  missing_start_events: ['summary'], missing_terminal_events: [], missing_replay_checksums: [],
  missing_checkpoints: ['anomaly_detection', 'root_cause_analysis'], missing_final_state_checksum: true,
}

function timelineOf(id: string, stages: unknown[], fields: Record<string, unknown> = {}) {
  return {
    schema_version: 2, pipeline_run_id: id, workflow_type: 'offline', status: 'completed', public_status: 'completed',
    started_at: isoAgo(0, 1.4), completed_at: isoAgo(0, 1.3), duration_seconds: 360,
    summary: { total_stages: stages.length, completed_stages: stages.length, running_stages: 0, failed_stages: 0, skipped_stages: 0, pending_stages: 0, progress_percent: 100 },
    cost_summary: { total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, total_tokens: 0, total_llm_calls: 0, stages: [] },
    stages, events: [], alerts: [], replay_integrity: { replayable: true, audit_gaps: NO_GAPS }, ...fields,
  }
}

// ── Audit (AGT-05) ─────────────────────────────────────────────────────────

/** The audited pipeline: the built-in offline stages (all on the hand canvas), so opening Agent stages reads nothing new. */
const ORIGIN_STAGES = OFFLINE_STAGE_NAMES.map((name, i) => stageRow(name, i, 84))

function replayDocument(replayable: boolean) {
  const stages = OFFLINE_STAGE_NAMES
  return {
    schema_version: 1, pipeline_run_id: ORIGIN_PIPELINE_ID, test_run_id: RUN_ID, workflow_type: 'offline',
    status: 'completed', started_at: isoAgo(0, 1.4), completed_at: isoAgo(0, 1.3),
    analysis_mode_requested: 'auto', analysis_mode_resolved: 'rules', analysis_mode_resolution: { source: 'settings' },
    final_state_checksum_sha256: replayable ? 'f'.repeat(64) : null, runtime_versions: { workflow: '2' },
    workflow_plan: { workflow_id: 'offline', version: 1 }, workflow_verification: {},
    route_decisions: [{ at: isoAgo(0, 1.39), decision_point: 'route_analysis_mode', chosen: 'rules', reason: 'no LLM endpoint' }],
    stage_replay: stages.map((stage_name, i) => ({
      stage_name, status: 'completed', started_at: isoAgo(0, 1.4 - i * 0.02), completed_at: isoAgo(0, 1.39 - i * 0.02),
      input_checksum_sha256: `${i}`.repeat(64), output_checksum_sha256: `${i + 1}`.repeat(64), runtime_versions: {},
      checkpoint_available: replayable || !['anomaly_detection', 'root_cause_analysis'].includes(stage_name),
      restored_from_checkpoint: false, decision_count: stage_name === 'root_cause_analysis' ? 1 : 0,
    })),
    memory_references: [{
      memory_entry_id: 'e1400000-0000-4000-8000-0000000000aa', entity_type: 'failure_cluster', entity_id: 'cluster-auth-ttl',
      payload_sha256: 'a'.repeat(64), memory_reference_id: 'mem-auth-ttl',
    }],
    events: [
      { event_type: 'stage_completed', stage_name: 'ingestion', timestamp: isoAgo(0, 1.39), detail: { failed: 15 } },
      { event_type: 'stage_completed', stage_name: 'root_cause_analysis', timestamp: isoAgo(0, 1.35), detail: { analysed: 15, mode: 'rules' } },
    ],
    event_counts: { stage_completed: 5, stage_started: replayable ? 5 : 4 },
    replayable, audit_gaps: replayable ? NO_GAPS : OLD_GAPS,
  }
}

/** `/agents?run=RUN_ID`: the run's one pipeline, its timeline carrying the replay integrity, its replay document. */
export function auditWorld({ replayable = false }: { replayable?: boolean } = {}): AgentOpsWorld {
  const writes: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/agents/pipelines', () => [pipelineRow(ORIGIN_PIPELINE_ID, 84, { execution_metadata: { workflow_ref: 'offline@1' } })]],
    [`/api/v1/agents/pipelines/${ORIGIN_PIPELINE_ID}/stages`, () => ORIGIN_STAGES],
    [`/api/v1/agents/pipelines/${ORIGIN_PIPELINE_ID}/timeline`, () => timelineOf(ORIGIN_PIPELINE_ID, ORIGIN_STAGES, {
      workflow_ref: 'offline@1', workflow_source: 'builtin',
      replay_integrity: { replayable, audit_gaps: replayable ? NO_GAPS : OLD_GAPS },
    })],
    [`/api/v1/agents/pipelines/${ORIGIN_PIPELINE_ID}/replay`, () => replayDocument(replayable)],
    ...agentsHandlers(),
  ]
  return { handlers, writes }
}

// ── Compare (AGT-05) ───────────────────────────────────────────────────────

const RERUN_STAGES = OFFLINE_STAGE_NAMES.map((name, i) => stageRow(name, i, 20))

function compareCell(checksum: string, seconds: number, tokens: number | null, cost: number | null) {
  return {
    status: 'completed', started_at: null, completed_at: null, duration_seconds: seconds, total_tokens: tokens,
    cost_usd: cost, fallback_used: false, confidence_score: 0.82, execution_path: 'rules', output_checksum_sha256: checksum,
  }
}

function compareSide(id: string, attempt: number, rerunOf: string | null, minutesAgo: number, seconds: number) {
  return {
    pipeline_run_id: id, test_run_id: RUN_ID, workflow_type: 'offline', workflow_ref: 'offline@1', status: 'completed',
    public_status: 'completed', attempt, rerun_of: rerunOf, created_at: isoAgo(0, minutesAgo / 60),
    started_at: isoAgo(0, minutesAgo / 60), completed_at: isoAgo(0, (minutesAgo - 2) / 60), duration_seconds: seconds,
    analysis_mode_resolved: 'rules', release_verdict: null, total_tokens: null, cost_usd: null, stages_truncated: false,
  }
}

/** `GET /agents/pipelines/compare?a=<rerun>&b=<origin>`: the root-cause stage's output changed, the rest equal. */
function compareAnswer(a: string, b: string) {
  const same = (stage: string, seconds: number) => ({
    stage_name: stage, a: compareCell(`${stage.length % 10}`.repeat(64), seconds, null, null),
    b: compareCell(`${stage.length % 10}`.repeat(64), seconds, null, null),
    changed: [] as string[], output_checksum_equal: true, duration_delta_seconds: 0, tokens_delta: null, cost_delta_usd: null,
  })
  return {
    a: compareSide(a, 2, ORIGIN_PIPELINE_ID, 20, 96), b: compareSide(b, 1, null, 84, 120), same_test_run: true,
    output: 'changed', changed_stages: 1, total: 5,
    stages: [
      same('ingestion', 4),
      same('anomaly_detection', 9),
      {
        stage_name: 'root_cause_analysis', a: compareCell('2'.repeat(64), 41, null, null), b: compareCell('3'.repeat(64), 65, null, null),
        changed: ['duration_seconds', 'output_checksum_sha256'], output_checksum_equal: false,
        duration_delta_seconds: 24, tokens_delta: null, cost_delta_usd: null,
      },
      same('summary', 12),
      same('triage', 6),
    ],
  }
}

/** `/agents?run=RUN_ID` with a rerun pair: the rerun (newest, attempt 2, `rerun_of` the origin) opens first. */
export function compareWorld(): AgentOpsWorld & { compared: Array<{ a: string | null; b: string | null }> } {
  const writes: RecordedWrite[] = []
  const compared: Array<{ a: string | null; b: string | null }> = []
  const handlers: ApiHandlers = [
    ['/api/v1/agents/pipelines', () => [
      pipelineRow(RERUN_PIPELINE_ID, 20, { attempt: 2, rerun_of: ORIGIN_PIPELINE_ID, execution_metadata: { workflow_ref: 'offline@1' } }),
      pipelineRow(ORIGIN_PIPELINE_ID, 84, { execution_metadata: { workflow_ref: 'offline@1' } }),
    ]],
    [`/api/v1/agents/pipelines/${RERUN_PIPELINE_ID}/stages`, () => RERUN_STAGES],
    [`/api/v1/agents/pipelines/${RERUN_PIPELINE_ID}/timeline`, () => timelineOf(RERUN_PIPELINE_ID, RERUN_STAGES, {
      workflow_ref: 'offline@1', workflow_source: 'named',
    })],
    ['/api/v1/agents/pipelines/compare', ({ url }) => {
      const a = url.searchParams.get('a')
      const b = url.searchParams.get('b')
      compared.push({ a, b })
      return compareAnswer(a ?? '', b ?? '')
    }],
    ...agentsHandlers(),
  ]
  return { handlers, writes, compared }
}

// ── The generic graph (AGT-06) ─────────────────────────────────────────────

/** Statuses of the investigation's hypotheses: two still running, one failed, two done. */
const HYPOTHESIS_STATUS = ['completed', 'running', 'failed', 'completed', 'running']

const INVESTIGATION_STAGES = INVESTIGATION_STAGE_NAMES.map((name, i) => stageRow(name, i === 0 ? 0 : i <= 5 ? 1 : 2, 12, {
  status: i === 0 ? 'completed' : i <= 5 ? HYPOTHESIS_STATUS[i - 1] : 'pending',
  completed_at: i === 0 || (i <= 5 && ['completed', 'failed'].includes(HYPOTHESIS_STATUS[i - 1])) ? isoAgo(0, 0.18) : null,
}))
const OFFLINE_GRAPH_STAGES = OFFLINE_STAGE_NAMES.map((name, i) => stageRow(name, i, 60))

/** `/agentic-runtime` of the investigation: the root, the plan, five hypotheses on it, the synthesis on all five. */
function investigationRuntime() {
  const id = (stage: string) => `pipeline:${INVESTIGATION_PIPELINE_ID}:stage:${stage}:attempt:1`
  const root = `pipeline:${INVESTIGATION_PIPELINE_ID}`
  const task = (stage: string, status: string, dependencies: string[], parent: string, tokens: number, cost: number) => ({
    schema_version: 1, task_id: id(stage), parent_task_id: parent, source_pipeline_run_id: INVESTIGATION_PIPELINE_ID,
    failure_cluster_id: null, capability_id: `agent.${stage}.v1`, stage_name: stage, status, selected: true,
    required: stage === 'investigator_plan', selection_reason: 'planned', dependencies, attempt: 1, budget: {},
    usage: { input_tokens: tokens - Math.floor(tokens / 4), output_tokens: Math.floor(tokens / 4), total_tokens: tokens, llm_calls: tokens > 0 ? 1 : 0, cost_usd: cost, duration_ms: 4_000 },
    stop_reason: status === 'running' || status === 'pending' ? null : status === 'failed' ? 'capability_error' : 'completed',
    enrichment_stop_reason: null, evidence_ids: [], finding_ids: [], started_at: isoAgo(0, 0.2),
    completed_at: status === 'running' || status === 'pending' ? null : isoAgo(0, 0.18),
    error: status === 'failed' ? 'environment probe timed out' : null,
  })
  return {
    schema_version: 1, agentic_run_id: `agentic:${INVESTIGATION_PIPELINE_ID}`, source_pipeline_run_id: INVESTIGATION_PIPELINE_ID,
    test_run_id: RUN_ID, workflow_type: 'investigation', status: 'running', public_status: 'in_progress', root_task_id: root,
    plan_id: `plan:${INVESTIGATION_PIPELINE_ID}`, plan_sha256: null, plan_integrity_status: 'legacy_unhashed',
    planner_version: 'investigator-1', run_budget: {},
    tasks: [
      { ...task('pipeline', 'running', [], '', 0, 0), task_id: root, parent_task_id: null, stop_reason: null },
      task('investigator_plan', 'completed', [], root, 0, 0),
      ...HYPOTHESES.map((h, i) => task(h, HYPOTHESIS_STATUS[i], ['investigator_plan'], id('investigator_plan'), 1_200 + i * 150, 0.004 + i * 0.001)),
      task('investigator_synthesis', 'pending', HYPOTHESES, root, 0, 0),
    ],
    findings: [], evidence: [], child_agentic_run_ids: [], observed_child_runs: 0, children_truncated: false,
    selected_capability_ids: [], skipped_capability_ids: [], observed_task_rows: INVESTIGATION_STAGE_NAMES.length,
    tasks_truncated: false, terminal_outcome: { status: 'running', error: null, budget_exhausted: false, budget_stop_reasons: [] },
    started_at: isoAgo(0, 0.2), completed_at: null,
  }
}

/**
 * `/agents?run=RUN_ID` with an investigation pipeline (newest: it opens first) and an offline one whose five
 * stages all map onto the hand canvas. ONLY the investigation answers `/agentic-runtime`: a read of the offline
 * pipeline's runtime is unhandled and fails the spec closed.
 */
export function investigationGraphWorld(): AgentOpsWorld & { runtimeReads: string[] } {
  const writes: RecordedWrite[] = []
  const runtimeReads: string[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/agents/pipelines', () => [
      pipelineRow(INVESTIGATION_PIPELINE_ID, 12, {
        workflow_type: 'investigation', status: 'running', public_status: 'in_progress', completed_at: null,
        execution_metadata: { workflow_ref: 'investigation@1' },
      }),
      pipelineRow(OFFLINE_PIPELINE_ID, 60, { execution_metadata: { workflow_ref: 'offline@1' } }),
    ]],
    [`/api/v1/agents/pipelines/${INVESTIGATION_PIPELINE_ID}/stages`, () => INVESTIGATION_STAGES],
    [`/api/v1/agents/pipelines/${INVESTIGATION_PIPELINE_ID}/timeline`, () => timelineOf(INVESTIGATION_PIPELINE_ID, INVESTIGATION_STAGES, {
      workflow_type: 'investigation', status: 'running', public_status: 'in_progress', completed_at: null,
      workflow_ref: 'investigation@1', workflow_source: 'named',
    })],
    [`/api/v1/agents/pipelines/${INVESTIGATION_PIPELINE_ID}/agentic-runtime`, ({ path }) => {
      runtimeReads.push(path)
      return investigationRuntime()
    }],
    [`/api/v1/agents/pipelines/${OFFLINE_PIPELINE_ID}/stages`, () => OFFLINE_GRAPH_STAGES],
    [`/api/v1/agents/pipelines/${OFFLINE_PIPELINE_ID}/timeline`, () => timelineOf(OFFLINE_PIPELINE_ID, OFFLINE_GRAPH_STAGES, {
      workflow_ref: 'offline@1', workflow_source: 'builtin',
    })],
    ...agentsHandlers(),
  ]
  return { handlers, writes, runtimeReads }
}

// ── Project default workflows (AGT-08) ─────────────────────────────────────

export const FORK_ID = 'wf.my-fork'

function workflowItem(workflow_id: string, base: 'offline' | 'deep', fields: Record<string, unknown> = {}) {
  const builtIn = workflow_id === base
  const steps = OFFLINE_STAGE_NAMES.map((id) => ({ id, agent_id: `agent.${id}.v1`, tools: [], reviews: [] }))
  return {
    id: `${workflow_id}-1`, workflow_id, version: 1, project_id: builtIn ? null : PROJECT_ID,
    name: builtIn ? `${base[0].toUpperCase()}${base.slice(1)} workflow` : 'My fork', description: builtIn ? 'Built-in TestLookup workflow template.' : 'The offline pipeline, triage first.',
    base,
    definition: {
      workflow_id, version: 1, project_id: builtIn ? null : PROJECT_ID, base, steps,
      edges: steps.slice(1).map((step, i) => ({ from: steps[i].id, to: step.id })), loops: [],
      retry_policy: { max_attempts: 5, base_seconds: 30, cap_seconds: 600 }, review_policy: 'human_required', deadline_seconds: 1500,
    },
    definition_sha256: (builtIn ? 'b' : 'c').repeat(64), status: 'published', published_at: isoAgo(3),
    eval_verdict: builtIn ? null : 'pass', eval_coverage: builtIn ? null : 0.92, eval_gate_run_id: null, evaluated_at: null,
    eval_regression_accepted: false, eval_regression_reason: null, eval_regression_accepted_at: null,
    read_only: builtIn, built_in: builtIn, ...fields,
  }
}

export const ROLE_RANK: Record<string, number> = { VIEWER: 0, TESTER: 1, QA_ENGINEER: 2, QA_LEAD: 3, ADMIN: 4 }

/** The signed-in user with another role (the stored session and `/auth/me`). */
export const userWithRole = (role: string) => ({ ...USER, id: `00000000-0000-4000-8000-0000000000${role.length.toString(16).padStart(2, '0')}`, username: role.toLowerCase(), full_name: role, role })

/**
 * `/agents/workflows` (the fork selected first), `/agents?run=RUN_ID` and `/runs/RUN_ID`: the project's workflows
 * (the two built-ins and a published fork on the offline base), its defaults (none set until a PUT), the PUT /
 * DELETE of `…/workflows/defaults/{base}` (refused below QA lead, as the server does) and the trigger.
 */
export function workflowDefaultWorld({ role = 'QA_LEAD' }: { role?: string } = {}): AgentOpsWorld & { defaultsReads: number[] } {
  const writes: RecordedWrite[] = []
  const defaultsReads: number[] = []
  let offlineDefault: string | null = null
  const record = (request: ApiRequest) => writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
  const defaults = () => {
    const body = workflowDefaultsBody()
    if (!offlineDefault) return body
    return {
      ...body,
      defaults: body.defaults.map((d) => d.base !== 'offline' ? d : {
        ...d, source: 'project_default', workflow_id: offlineDefault as string, resolved_ref: `${offlineDefault}@1`,
        resolved_name: 'My fork', updated_by: USER.full_name, updated_at: isoAgo(0, 0.01),
      }),
    }
  }
  const lead = (ROLE_RANK[role] ?? 0) >= ROLE_RANK.QA_LEAD
  const handlers: ApiHandlers = [
    [`/api/v1/projects/${PROJECT_ID}/workflows`, () => ({
      workflows: [workflowItem(FORK_ID, 'offline'), workflowItem('offline', 'offline'), workflowItem('deep', 'deep')],
    })],
    [`/api/v1/projects/${PROJECT_ID}/workflows/defaults`, () => {
      defaultsReads.push(1)
      return defaults()
    }],
    [/^\/api\/v1\/projects\/[^/]+\/workflows\/defaults\/[^/]+$/, (request) => {
      record(request)
      if (!lead) return respond(403, { detail: 'Requires at least QA_LEAD role in this project' })
      offlineDefault = (bodyOf(request) as { workflow_id?: string } | null)?.workflow_id ?? null
      return defaults().defaults[0]
    }, 'PUT'],
    [/^\/api\/v1\/projects\/[^/]+\/workflows\/defaults\/[^/]+$/, (request) => {
      record(request)
      if (!lead) return respond(403, { detail: 'Requires at least QA_LEAD role in this project' })
      offlineDefault = null
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/agents/pipelines/trigger', (request) => {
      record(request)
      return respond(202, { message: 'Pipeline queued', task_id: 'task-picked', run_id: RUN_ID, workflow_ref: `${FORK_ID}@1` })
    }, 'POST'],
    ...agentsHandlers(),
  ]
  return { handlers, writes, defaultsReads }
}
