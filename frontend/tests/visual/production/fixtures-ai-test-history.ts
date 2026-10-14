/**
 * W03 AIH-05: the test case's History with the AI analysis across runs
 * (`GET .../history` with `ai` per point and `ai_changes`, contract §7.3) and
 * that run's stored versions (`GET /runs/{run}/tests/{test}/ai-history`, R5),
 * shared by the hermetic spec (`tests/ci-e2e/test-case-ai-history.spec.ts`)
 * and the visual baseline (`prod-test-case-ai.visual.spec.ts`), so the two can
 * never draw different answers.
 *
 * Ten runs, newest first, three red (#60 = this run, #57, #53), as the base
 * test-case fixture (`fixtures-pages.ts`):
 *  - #53: INFRASTRUCTURE 45% by rules (one backfilled version);
 *  - #57: INFRASTRUCTURE 40% by rules, the root cause reworded (no version stored yet);
 *  - #60: PRODUCT_BUG 85% by the LLM after a human correction: v1 rules,
 *    v2 LLM, v3 human correction (anna). The AI changed its mind on #60.
 */
import { type ApiHandlers } from '../../lib/production-pages'
import { isoAgo, RUN_ID } from './fixtures'
import { TEST_CASE, TEST_CASE_API, TEST_CASE_FINGERPRINT, TEST_CASE_ID, TEST_CASE_NAME, TEST_CASE_ROOT_CAUSE } from './fixtures-pages'

/** The run ids of the ten points (index 0 = this run, newest). */
export const historyRunId = (i: number) => (i === 0 ? RUN_ID : `44444444-4444-4444-8444-${String(100000000000 + i)}`)
/** That point's own test result id (index 0 = the page's test). */
export const historyTestId = (i: number) => (i === 0 ? TEST_CASE_ID : `55555555-5555-4555-8555-${String(100000000000 + i)}`)

const RED = [0, 3, 7]
const INFRA_CAUSE = 'payment-gateway sandbox timed out after 30 s; the checkout total was never computed.'

const aiFor = (i: number) => {
  if (i === 0) {
    return {
      test_id: historyTestId(0),
      category: 'PRODUCT_BUG',
      confidence: 85,
      root_cause_excerpt: TEST_CASE_ROOT_CAUSE,
      root_cause_sha: 'b1c2d3e4f5a6b7c8',
      analysis_mode: 'llm',
      model: 'llama3',
      is_flaky: false,
      requires_human_review: false,
      version_count: 3,
    }
  }
  if (i === 3) {
    return {
      test_id: historyTestId(3),
      category: 'INFRASTRUCTURE',
      confidence: 40,
      root_cause_excerpt: `The ${INFRA_CAUSE}`,
      root_cause_sha: 'a2a2a2a2a2a2a2a2',
      analysis_mode: 'rules',
      model: null,
      is_flaky: false,
      requires_human_review: true,
      version_count: 0,
    }
  }
  return {
    test_id: historyTestId(7),
    category: 'INFRASTRUCTURE',
    confidence: 45,
    root_cause_excerpt: `A ${INFRA_CAUSE}`,
    root_cause_sha: 'a1a1a1a1a1a1a1a1',
    analysis_mode: 'rules',
    model: null,
    is_flaky: false,
    requires_human_review: true,
    version_count: 1,
  }
}

/** `GET .../history` with the W03 AI fields (newest first). */
export const AI_HISTORY = {
  run_id: RUN_ID,
  test_id: TEST_CASE_ID,
  test_fingerprint: TEST_CASE_FINGERPRINT,
  test_name: TEST_CASE_NAME,
  history: Array.from({ length: 10 }, (_, i) => ({
    run_id: historyRunId(i),
    run_label: `Run #${60 - i}`,
    build_number: String(240 - i),
    run_seq: 60 - i,
    status: RED.includes(i) ? 'FAILED' : 'PASSED',
    duration_ms: 3_900 + i * 40,
    created_at: isoAgo(i, 2),
    ai: RED.includes(i) ? aiFor(i) : null,
  })),
  // Oldest -> newest, emitted for the NEWER point.
  ai_changes: [
    {
      run_id: historyRunId(3), run_label: 'Run #57', from_category: 'INFRASTRUCTURE', to_category: 'INFRASTRUCTURE',
      from_confidence: 45, to_confidence: 40, kinds: ['root_cause'],
    },
    {
      run_id: historyRunId(0), run_label: 'Run #60', from_category: 'INFRASTRUCTURE', to_category: 'PRODUCT_BUG',
      from_confidence: 40, to_confidence: 85, kinds: ['category', 'root_cause'],
    },
  ],
  flakiness: {
    is_flaky: true, failure_rate: 0.3, failure_rate_pct: 30, impact_score: 4.2, classification: 'INVESTIGATE',
    window_days: 30, total_runs: 10, passed: 7, failed: 3,
  },
  metadata: {
    owner: 'payments-team', assigned_to_user_id: null, suite: 'Checkout', severity: 'critical', feature: 'Totals',
    first_seen_run_id: null, first_seen_run_label: 'Run #12', first_seen_at: isoAgo(28), last_seen_run_id: RUN_ID,
    last_seen_run_label: 'Run #60', last_seen_at: isoAgo(0, 2), created_at: isoAgo(28), updated_at: isoAgo(0, 2),
  },
}

/** R5 for the page's own test (#60). */
export const AI_VERSIONS_PATH = `${TEST_CASE_API}/ai-history`
/** R5 for #53's test (one backfilled version). */
export const AI_VERSIONS_PATH_53 = `/api/v1/runs/${historyRunId(7)}/tests/${historyTestId(7)}/ai-history`
/** R5 for #57's test: never asked (its history says no version is stored). */
export const AI_VERSIONS_PATH_57 = `/api/v1/runs/${historyRunId(3)}/tests/${historyTestId(3)}/ai-history`

const version = (n: number, extra: Record<string, unknown>) => ({
  version_id: `9999999${n}-9999-4999-8999-999999999999`,
  version: n,
  pipeline_run_id: null,
  actor: null,
  failure_category: 'PRODUCT_BUG',
  confidence_score: 85,
  analysis_mode: 'llm',
  llm_provider: 'ollama',
  llm_model: 'llama3',
  is_flaky: false,
  requires_human_review: false,
  root_cause_excerpt: TEST_CASE_ROOT_CAUSE,
  content_sha256: String(n).repeat(64),
  ...extra,
})

/** `GET .../ai-history` for #60: v1 rules, v2 LLM, v3 human correction (anna); newest first. */
export const TEST_AI_VERSIONS = {
  run_id: RUN_ID,
  test_id: TEST_CASE_ID,
  test_fingerprint: TEST_CASE_FINGERPRINT,
  test_name: TEST_CASE_NAME,
  current: version(3, {
    version_id: '88888888-0000-4000-8000-000000000001', trigger: 'current',
    created_at: isoAgo(0, 1),
  }),
  versions: [
    version(3, {
      trigger: 'human_correction', actor: { user_id: '12121212-1212-4121-8121-121212121212', name: 'anna' },
      analysis_mode: 'llm', created_at: isoAgo(0, 1),
    }),
    version(2, { trigger: 'pipeline', pipeline_run_id: 'aaaaaaa2-aaaa-4aaa-8aaa-aaaaaaaaaaaa', created_at: isoAgo(0, 1.5) }),
    version(1, {
      trigger: 'pipeline', pipeline_run_id: 'aaaaaaa1-aaaa-4aaa-8aaa-aaaaaaaaaaaa', failure_category: 'INFRASTRUCTURE',
      confidence_score: 40, analysis_mode: 'rules', llm_provider: null, llm_model: null,
      root_cause_excerpt: `The ${INFRA_CAUSE}`, created_at: isoAgo(0, 1.9),
    }),
  ],
  total: 3,
}

/** `GET .../ai-history` for #53: one backfilled rules version. */
export const TEST_AI_VERSIONS_53 = {
  run_id: historyRunId(7),
  test_id: historyTestId(7),
  test_fingerprint: TEST_CASE_FINGERPRINT,
  test_name: TEST_CASE_NAME,
  current: null,
  versions: [
    version(1, {
      trigger: 'backfill', failure_category: 'INFRASTRUCTURE', confidence_score: 45, analysis_mode: 'rules',
      llm_provider: null, llm_model: null, root_cause_excerpt: `A ${INFRA_CAUSE}`, created_at: isoAgo(7, 2),
    }),
  ],
  total: 1,
}

/** The test case page with the AI history answers (first match wins: this history replaces the base one). */
export const WITH_AI_TEST_HISTORY: ApiHandlers = [
  [`${TEST_CASE_API}/history`, () => AI_HISTORY],
  [AI_VERSIONS_PATH, () => TEST_AI_VERSIONS],
  [AI_VERSIONS_PATH_53, () => TEST_AI_VERSIONS_53],
  ...TEST_CASE,
]
