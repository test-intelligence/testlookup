/**
 * The Run page (`/runs/:runId`, UX redesign P4): every request its four tabs
 * make, answered from the same deterministic fixtures as the production
 * visual baselines (`fixtures.ts`, pinned to `NOW`). Not a spec file.
 *
 * The run is `RUN_ID`: build 240 of the Auth suite, the newest of the 13
 * builds of `runs()` (180 passed, 12 failed, 3 broken, 2 skipped = 197).
 * Its tests are listed the way the API lists them (`runs_service.
 * list_run_test_cases`: ordered by status, then name), paged and filtered by
 * status, so the Tests tab's failures-first order and its status chips can
 * be checked against a server-shaped answer.
 *
 * Tabs and what they ask for:
 *   Tests     the run, its tests, its attribution verdicts, the suite's runs
 *   Analysis  run intelligence (+ decision reports, step flips, onboarding),
 *             the deep investigation's clusters, findings and status, and
 *             (W03) the AI history: versions, previous run, AI compare
 *   Changes   the regression diff and the duration regressions (EXEC-05)
 *   Evidence  run intelligence (decision report) and the run's AI pipeline:
 *             pipelines, the newest one's stages and timeline, the AI report
 */
import { type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { DEEP_INVESTIGATION, isoAgo, LAYOUT, PROJECT_ID, RUN_ID, RUN_INTELLIGENCE, RUNS, runs } from './fixtures'

/** The run on the page: `runs()[0]`. */
export const RUN = runs()[0]

type Status = 'BROKEN' | 'FAILED' | 'PASSED' | 'SKIPPED'

const AREAS = ['login', 'logout', 'session', 'token', 'password', 'mfa', 'sso', 'profile', 'lockout', 'audit']

/** The run's tests, in the API's order (status, then name). */
export function runTests() {
  const counts: [Status, number][] = [
    ['BROKEN', RUN.broken_tests],
    ['FAILED', RUN.failed_tests],
    ['PASSED', RUN.passed_tests],
    ['SKIPPED', RUN.skipped_tests],
  ]
  const tests: Record<string, unknown>[] = []
  for (const [status, count] of counts) {
    const names = Array.from({ length: count }, (_, i) => `test_${AREAS[i % AREAS.length]}_${String(i).padStart(3, '0')}_${status.toLowerCase()}`)
    names.sort()
    names.forEach((name, i) => {
      const failing = status === 'FAILED' || status === 'BROKEN'
      tests.push({
        id: `55555555-5555-4555-8555-${String(tests.length).padStart(12, '0')}`,
        test_name: name,
        class_name: `auth.${AREAS[i % AREAS.length]}.Tests`,
        suite_name: 'Auth',
        status,
        duration_ms: 120 + ((tests.length * 37) % 900),
        failure_category: failing ? (status === 'BROKEN' ? 'INFRASTRUCTURE' : 'PRODUCT_BUG') : null,
        failure_kind: null,
        step_count: null,
        retry_count: 0,
        is_flaky_run: false,
      })
    })
  }
  return tests
}

function testList(request: ApiRequest) {
  const params = request.url.searchParams
  const page = Number(params.get('page') ?? 1)
  const size = Number(params.get('size') ?? 50)
  const status = params.get('status')
  const suite = params.get('suite')
  const all = runTests().filter(
    (t) => (!status || t.status === status.toUpperCase()) && (!suite || String(t.suite_name).toLowerCase().includes(suite.toLowerCase())),
  )
  const items = all.slice((page - 1) * size, page * size)
  return { items, total: all.length, page, size, pages: Math.max(1, Math.ceil(all.length / size)) }
}

/** The last green build of Auth before this one: build 238 (`runs()[2]`). */
const BASELINE = runs()[2]

const REGRESSION_DIFF = {
  baseline_available: true,
  baseline_run_id: BASELINE.id,
  baseline_build_number: String(BASELINE.build_number),
  pass_rate: RUN.pass_rate,
  baseline_pass_rate: BASELINE.pass_rate,
  pass_rate_delta: Math.round((RUN.pass_rate - BASELINE.pass_rate) * 10) / 10,
  new_failing_tests: runTests()
    .filter((t) => t.status === 'FAILED' || t.status === 'BROKEN')
    .map((t) => ({ test_name: t.test_name, suite_name: t.suite_name })),
  new_failing_count: RUN.failed_tests + RUN.broken_tests,
  resolved_count: 0,
  commit_range: [
    { sha: 'a1b2c3d', message: 'Shorten the session token TTL', author: 'dev-one', timestamp: isoAgo(0, 5) },
    { sha: 'e4f5a6b', message: 'Move MFA to the new provider', author: 'dev-two', timestamp: isoAgo(0, 7) },
  ],
}

/**
 * Changes: the run's duration regressions (EXEC-05) -- one Auth test 4x its
 * baseline, new on this run, and the suite 1.8x slower than its previous 20
 * runs.
 */
export const DURATION_REGRESSIONS = {
  run_id: RUN_ID,
  schema_version: 1,
  measured: true,
  reason: null,
  stored: true,
  evaluated_at: isoAgo(0, 1.2),
  spikes: [
    {
      test_fingerprint: 'fp-auth-token-refresh',
      test_name: 'auth::token::test_refresh_rotates',
      suite_name: 'Auth',
      observed_ms: 400,
      baseline_mean_ms: 100,
      baseline_stddev_ms: 73.2,
      baseline_p95_ms: 220.4,
      sample_count: 34,
      sigma: 4.1,
      delta_ms: 300,
      ratio: 4,
      previous_ms: 104,
      was_slow_before: false,
      new: true,
    },
  ],
  spike_count: 1,
  suites: [
    {
      suite_name: 'Auth',
      suite_key: 'auth',
      metric: 'wall',
      ratio: 1.8,
      recent_median_ms: 9000,
      baseline_median_ms: 5000,
      recent_runs: 5,
      baseline_runs: 20,
      observed_ms: 9400,
      was_slow_before: false,
      new: true,
    },
  ],
  suite_regression_count: 1,
  test_check: { measured: true, reason: null, considered: 180, judged: 172, history: 34, required_history: 10, sigma: 3 },
  suite_check: { measured: true, reason: null, evaluated: 1, history: 25, required_history: 15, recent_runs: 5, baseline_runs: 20, ratio: 1.5 },
  notified: { tests: ['fp-auth-token-refresh'], suites: ['auth'] },
}

const PIPELINE_ID = '77777777-7777-4777-8777-777777777777'

const PIPELINES = [
  {
    id: PIPELINE_ID,
    test_run_id: RUN_ID,
    workflow_type: 'offline',
    status: 'completed',
    public_status: 'completed',
    attempt: 1,
    max_attempts: 3,
    started_at: isoAgo(0, 1.4),
    completed_at: isoAgo(0, 1.3),
    error: null,
    created_at: isoAgo(0, 1.4),
    execution_metadata: null,
    provenance_metadata: null,
    build_number: String(RUN.build_number),
    run_seq: RUN.run_seq,
    suite_name: 'Auth',
    review_summary: null,
  },
]

const STAGES = ['ingestion', 'anomaly', 'rca', 'summary'].map((stage_name, i) => ({
  stage_name,
  status: 'completed',
  started_at: isoAgo(0, 1.4 - i * 0.02),
  completed_at: isoAgo(0, 1.39 - i * 0.02),
  result_data: null,
  error: null,
  skipped_reason: null,
  execution_path: 'rules',
  fallback_used: false,
  input_tokens: null,
  output_tokens: null,
  total_tokens: null,
  llm_calls_count: null,
  cost_usd: null,
  error_category: null,
  confidence_score: null,
  evidence_count: null,
  route_rationale: null,
}))

const TIMELINE = {
  schema_version: 1,
  pipeline_run_id: PIPELINE_ID,
  workflow_type: 'offline',
  status: 'completed',
  public_status: 'completed',
  started_at: isoAgo(0, 1.4),
  completed_at: isoAgo(0, 1.3),
  duration_seconds: 360,
  summary: { total_stages: 4, completed_stages: 4, running_stages: 0, failed_stages: 0, skipped_stages: 0, pending_stages: 0, progress_percent: 100 },
  cost_summary: { total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, total_tokens: 0, total_llm_calls: 0, stages: [] },
  stages: STAGES,
  events: [],
}

/**
 * W14 AGT-06: the pipeline's task graph (`GET /agents/pipelines/{id}/agentic-runtime`, `AgenticRunV1`): the
 * root task plus its four stages as tasks, chained in their order (ingestion → anomaly → rca → summary). The
 * stage names `anomaly` / `rca` are not the hand canvas's, so the Graph tab draws the generic DAG from this.
 */
const runtimeTaskId = (stage: string) => `${PIPELINE_ID}:${stage}`

const AGENTIC_RUNTIME = {
  schema_version: 1,
  agentic_run_id: `agentic:${PIPELINE_ID}`,
  source_pipeline_run_id: PIPELINE_ID,
  test_run_id: RUN_ID,
  workflow_type: 'offline',
  status: 'completed',
  public_status: 'completed',
  root_task_id: runtimeTaskId('pipeline'),
  plan_id: `plan:${PIPELINE_ID}`,
  plan_sha256: null,
  plan_integrity_status: 'legacy_unhashed',
  planner_version: 'workflow-1',
  run_budget: {},
  tasks: [
    { stage_name: 'pipeline', dependencies: [] as string[] },
    ...STAGES.map((stage, i) => ({ stage_name: stage.stage_name, dependencies: i === 0 ? [] : [STAGES[i - 1].stage_name] })),
  ].map(({ stage_name, dependencies }, i) => ({
    schema_version: 1,
    task_id: runtimeTaskId(stage_name),
    parent_task_id: i === 0 ? null : runtimeTaskId('pipeline'),
    source_pipeline_run_id: PIPELINE_ID,
    failure_cluster_id: null,
    capability_id: i === 0 ? 'agent.workflow.v1' : `agent.${stage_name}.v1`,
    stage_name,
    status: 'completed',
    selected: true,
    required: i <= 1,
    selection_reason: i === 0 ? 'root' : 'planned',
    dependencies,
    attempt: 1,
    budget: {},
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, llm_calls: 0, cost_usd: 0, duration_ms: 600 },
    stop_reason: 'completed',
    enrichment_stop_reason: null,
    evidence_ids: [],
    finding_ids: [],
    started_at: i === 0 ? isoAgo(0, 1.4) : STAGES[i - 1].started_at,
    completed_at: i === 0 ? isoAgo(0, 1.3) : STAGES[i - 1].completed_at,
    error: null,
  })),
  findings: [],
  evidence: [],
  child_agentic_run_ids: [],
  observed_child_runs: 0,
  children_truncated: false,
  selected_capability_ids: STAGES.map((stage) => `agent.${stage.stage_name}.v1`),
  skipped_capability_ids: [],
  observed_task_rows: STAGES.length,
  tasks_truncated: false,
  terminal_outcome: { status: 'completed', error: null, budget_exhausted: false, budget_stop_reasons: [] },
  started_at: isoAgo(0, 1.4),
  completed_at: isoAgo(0, 1.3),
}

/** The AI report of the run (`GET /agents/runs/{id}/summary`). */
export const AI_REPORT_SUMMARY = '15 failures in the Auth suite, all after the session token TTL change.'

const RUN_SUMMARY = {
  test_run_id: RUN_ID,
  project_id: PROJECT_ID,
  build_number: String(RUN.build_number),
  executive_summary: AI_REPORT_SUMMARY,
  markdown_report: '## Root cause\n**Likely cause:** session tokens expire before the MFA step completes\n- 12 failed tests share the token expiry error\n## Next steps\n- Restore the previous TTL and re-run the Auth suite',
  executive_panel: null,
  anomaly_count: 2,
  is_regression: true,
  analysis_count: 15,
  generated_at: isoAgo(0, 1.3),
  requires_human_review: false,
  review: null,
}

/**
 * The run's stored release decision (`GET /release-readiness/{run}`), not
 * overridden: the Analysis banner reads it for the override state and the QA
 * lead's Hold / Approve with conditions / Override actions (GATE-01). The same
 * verdict as the run intelligence's `release_decision` (a conditional go at 58).
 */
export const RUN_RELEASE_DECISION = {
  run_id: RUN_ID,
  recommendation: 'CONDITIONAL_GO',
  risk_score: 58,
  composite_risk: 58,
  dimension_scores: [],
  blocking_issues: [],
  conditions_for_go: ['Re-run the Payments suite after the gateway fix'],
  reasoning: 'Infrastructure failures dominate; product risk is contained to one suite.',
  score_model_version: 1,
  input_snapshot: null,
  cluster_insights: [],
  baseline_diff: null,
  open_defects_by_component: [],
  human_override: null,
  overridden_by: null,
  original_recommendation: null,
  original_risk_score: null,
  override_audit: [],
  pass_rate: RUN.pass_rate,
  build_number: String(RUN.build_number),
  policy_id: null,
  policy_version: null,
  policy_level: 'hardcoded',
  rule_evaluations: [],
  synthesized: false,
  created_at: isoAgo(0, 1.1),
  updated_at: isoAgo(0, 1.1),
}

const NO_PREVIOUS = 'No earlier completed run of "Auth" on branch "main" found.'
const AI_ENVELOPE = { requires_human_review: true, review: null, ai_disclaimer: null, ai_disclaimer_version: null }

/**
 * W03 AIH-04: the run's AI history, quiet — no stored version yet and no previous run, so the Analysis tab's top
 * (the "vs previous run" card and the history control) stays one line each. `GET /runs/{id}/previous` is read on
 * every tab (the ⋯ menu's "Compare to previous run"), the version list on Analysis and Evidence. A spec about the
 * AI history puts `fixtures-ai-history.ts` `AI_HISTORY` before `RUN_PAGE` (first match wins).
 */
export const AI_HISTORY_QUIET: ApiHandlers = [
  [/^\/api\/v1\/runs\/[^/]+\/ai-history$/, ({ path, url }) => ({
    run_id: path.split('/')[4], kind: url.searchParams.get('kind') ?? 'analysis_digest', total: 0, limit: 100,
    truncated: false, latest_version: null, live: { available: true }, versions: [], ...AI_ENVELOPE,
  })],
  [/^\/api\/v1\/runs\/[^/]+\/previous$/, ({ path }) => ({
    run_id: path.split('/')[4],
    rule: { scope: 'project+primary_suite+branch', suite_name: 'Auth', branch: 'main', candidate_status: 'not IN_PROGRESS (any outcome)', order: 'finished before this run, newest first' },
    current: {
      run_id: RUN.id, build_number: String(RUN.build_number), run_seq: RUN.run_seq, branch: RUN.branch,
      primary_suite_name: RUN.primary_suite_name, status: RUN.status, created_at: RUN.created_at, finished_at: RUN.end_time,
    },
    baseline: null, reason: 'no_previous_run', message: NO_PREVIOUS,
  })],
  [/^\/api\/v1\/runs\/[^/]+\/ai-compare$/, ({ path }) => ({
    run_id: path.split('/')[4], project_id: PROJECT_ID, baseline: null, reason: 'no_previous_run', message: NO_PREVIOUS,
    current_ref: null, baseline_ref: null, sides: null, diff: null, narrative: null, ...AI_ENVELOPE,
  })],
]

/** Everything `/runs/:runId` asks for, on every tab. */
export const RUN_PAGE: ApiHandlers = [
  ...LAYOUT,
  [`/api/v1/runs/${RUN_ID}`, () => RUN],
  [`/api/v1/runs/${RUN_ID}/tests`, testList],
  [`/api/v1/runs/${RUN_ID}/attribution`, () => ({ items: [], total: 0 })],
  [`/api/v1/runs/${RUN_ID}/regression-diff`, () => REGRESSION_DIFF],
  [`/api/v1/runs/${RUN_ID}/duration-regressions`, () => DURATION_REGRESSIONS],
  ...RUNS,
  // Analysis + Evidence: run intelligence (its LAYOUT duplicates are harmless: first match wins).
  ...RUN_INTELLIGENCE,
  // Analysis: the stored release decision the verdict banner's override reads (GATE-01).
  [/^\/api\/v1\/release-readiness\/[^/]+$/, () => RUN_RELEASE_DECISION],
  // Analysis: the deep investigation's clusters, findings and pipeline status.
  ...DEEP_INVESTIGATION.filter(([matcher]) => String(matcher).includes('deep-investigate') || String(matcher).includes('pipeline-status')),
  // Evidence: the run's AI pipeline and its report.
  ['/api/v1/agents/pipelines', () => PIPELINES],
  [`/api/v1/agents/pipelines/${PIPELINE_ID}/stages`, () => STAGES],
  [`/api/v1/agents/pipelines/${PIPELINE_ID}/timeline`, () => TIMELINE],
  // W14 AGT-06: read while the Graph tab of Agent stages shows the generic DAG (the stage names don't map).
  [`/api/v1/agents/pipelines/${PIPELINE_ID}/agentic-runtime`, () => AGENTIC_RUNTIME],
  [`/api/v1/agents/runs/${RUN_ID}/summary`, () => RUN_SUMMARY],
  // Analysis (+ Evidence, + the ⋯ menu's previous run): the AI history, quiet (W03).
  ...AI_HISTORY_QUIET,
]
