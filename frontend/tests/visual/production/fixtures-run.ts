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
 *   Analysis  run intelligence (+ decision reports, step flips, onboarding)
 *             and the deep investigation's clusters, findings and status
 *   Changes   the regression diff
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

/** Everything `/runs/:runId` asks for, on every tab. */
export const RUN_PAGE: ApiHandlers = [
  ...LAYOUT,
  [`/api/v1/runs/${RUN_ID}`, () => RUN],
  [`/api/v1/runs/${RUN_ID}/tests`, testList],
  [`/api/v1/runs/${RUN_ID}/attribution`, () => ({ items: [], total: 0 })],
  [`/api/v1/runs/${RUN_ID}/regression-diff`, () => REGRESSION_DIFF],
  ...RUNS,
  // Analysis + Evidence: run intelligence (its LAYOUT duplicates are harmless: first match wins).
  ...RUN_INTELLIGENCE,
  // Analysis: the deep investigation's clusters, findings and pipeline status.
  ...DEEP_INVESTIGATION.filter(([matcher]) => String(matcher).includes('deep-investigate') || String(matcher).includes('pipeline-status')),
  // Evidence: the run's AI pipeline and its report.
  ['/api/v1/agents/pipelines', () => PIPELINES],
  [`/api/v1/agents/pipelines/${PIPELINE_ID}/stages`, () => STAGES],
  [`/api/v1/agents/pipelines/${PIPELINE_ID}/timeline`, () => TIMELINE],
  [`/api/v1/agents/runs/${RUN_ID}/summary`, () => RUN_SUMMARY],
]
