/**
 * UX redesign P4 (`02-design-spec.md` §2 + §5 "Test case"), on
 * /runs/:runId/tests/:testId, hermetic, at 1440 x 900:
 *
 *  - the answer is in the first viewport: the error, the stack trace and the
 *    AI root cause are one block (`[data-primary]`) whose top is at most
 *    300 px below the top of `#main-content`, and the stack trace and the
 *    suggested root cause are on screen without scrolling;
 *  - above it only the header and the chips (suite, class, status, duration,
 *    tags), never the old metadata grid or the hard-coded `<Class>.java`;
 *  - below it the tabs History · Steps · Details (`?tab=`): History is the
 *    default and asks for the cross-run history at load, Steps asks for the
 *    step tree only when opened, Details shows the fingerprint, the ids and
 *    the parser.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after can be compared (`docs/viz-work/p4-agent-E.md`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). The fixtures
 * are this page's own: one failed test of the visual specs' run `RUN_ID`.
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { LAYOUT, PROJECT_ID, RUN_ID, isoAgo } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const TEST_ID = '77777777-7777-4777-8777-777777777777'
const TEST_NAME = 'checkout total includes tax'
const FULL_NAME = 'com.example.checkout.TotalSpec.checkoutTotalIncludesTax'
const ERROR = 'AssertionError: expected total 108.00 but was 100.00'
const TRACE = [
  ERROR,
  '    at org.junit.Assert.assertEquals(Assert.java:117)',
  '    at com.example.checkout.TotalSpec.checkoutTotalIncludesTax(TotalSpec.java:42)',
  '    at java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)',
].join('\n')
const ROOT_CAUSE = 'The tax line is skipped when the cart holds a discounted item: TaxCalculator returns 0 for a negative line.'
const FINGERPRINT = 'fp-checkout-total-includes-tax'
const PATH = `/runs/${RUN_ID}/tests/${TEST_ID}`
const BASE = `/api/v1/runs/${RUN_ID}/tests/${TEST_ID}`

const STEPS = [
  { name: 'open the cart', status: 'PASSED' },
  { name: 'apply the discount code', status: 'PASSED' },
  { name: 'read the total', status: 'FAILED' },
].map((s, i) => ({
  id: `step-${i + 1}`,
  parent_step_id: null,
  ordinal: i + 1,
  depth: 0,
  name: s.name,
  keyword: null,
  status: s.status,
  duration_ms: 400 + i * 300,
  start_ms: i * 700,
  assertion_message: s.status === 'FAILED' ? ERROR : null,
  assertion_trace: null,
  expected_value: s.status === 'FAILED' ? '108.00' : null,
  actual_value: s.status === 'FAILED' ? '100.00' : null,
  parameters: [],
  created_at: isoAgo(0, 2),
  steps: [],
  attachments: [],
}))

/** `GET /runs/{run}/tests/{test}/rich-detail`: the enriched contract (`types/test-case-detail.ts`). */
const DETAIL = {
  contract: 'test-case-detail',
  schema_version: 1,
  contract_version: '1',
  id: TEST_ID,
  test_name: TEST_NAME,
  full_name: FULL_NAME,
  class_name: 'TotalSpec',
  suite_name: 'Checkout',
  status: 'FAILED',
  duration_ms: 4_210,
  severity: 'critical',
  feature: 'Totals',
  owner: 'payments-team',
  created_at: isoAgo(0, 2),
  tags: ['smoke', 'checkout'],
  error_message: ERROR,
  has_attachments: false,
  canonical_test_case_id: '88888888-8888-4888-8888-888888888888',
  project_id: PROJECT_ID,
  identity: {
    test_case_id: TEST_ID,
    test_run_id: RUN_ID,
    history_id: 'hist-checkout-total-tax',
    full_name: FULL_NAME,
    fingerprint: FINGERPRINT,
  },
  classification: {
    suite: { name: 'Checkout', legacy_name: 'Checkout' },
    class_name: 'TotalSpec',
    severity: 'critical',
    feature: 'Totals',
    owner: 'payments-team',
    framework: 'junit',
    tags: ['smoke', 'checkout'],
    components: [],
    labels: [],
  },
  execution: {
    status: 'FAILED',
    duration_ms: 4_210,
    retry_count: 0,
    is_flaky_run: false,
    step_count: 3,
    steps_present: true,
    has_attachments: false,
    failure_category: 'PRODUCT_BUG',
    error_message: ERROR,
    stack_trace: TRACE,
    parameters: [],
  },
  provenance: {
    source_test_run_id: RUN_ID,
    parser_format: 'junit',
    parser_version: '2.4.1',
    format: 'junit',
    source_file: null,
    field_sources: { identity: 'source_report', suite: 'source_report' },
    warnings: [],
  },
  steps_present: true,
  steps: STEPS,
  attachments: [],
  definition: null,
  links: [],
  extensions: null,
}

/** `GET .../history`: ten runs, newest first, three of them red. */
const HISTORY = {
  run_id: RUN_ID,
  test_id: TEST_ID,
  test_fingerprint: FINGERPRINT,
  test_name: TEST_NAME,
  history: Array.from({ length: 10 }, (_, i) => ({
    run_id: i === 0 ? RUN_ID : `44444444-4444-4444-8444-${String(100000000000 + i)}`,
    run_label: `Run #${60 - i}`,
    build_number: String(240 - i),
    run_seq: 60 - i,
    status: [0, 3, 7].includes(i) ? 'FAILED' : 'PASSED',
    duration_ms: 3_900 + i * 40,
    created_at: isoAgo(i, 2),
  })),
  flakiness: {
    is_flaky: true,
    failure_rate: 0.3,
    failure_rate_pct: 30,
    impact_score: 4.2,
    classification: 'INVESTIGATE',
    window_days: 30,
    total_runs: 10,
    passed: 7,
    failed: 3,
  },
  metadata: {
    owner: 'payments-team',
    assigned_to_user_id: null,
    suite: 'Checkout',
    severity: 'critical',
    feature: 'Totals',
    first_seen_run_id: null,
    first_seen_run_label: 'Run #12',
    first_seen_at: isoAgo(28),
    last_seen_run_id: RUN_ID,
    last_seen_run_label: 'Run #60',
    last_seen_at: isoAgo(0, 2),
    created_at: isoAgo(28),
    updated_at: isoAgo(0, 2),
  },
}

/** `GET .../steps`: the same tree (the enriched detail already carries it). */
const STEP_TREE = {
  run_id: RUN_ID,
  test_id: TEST_ID,
  test_name: TEST_NAME,
  status: 'FAILED',
  step_count: 3,
  retry_count: 0,
  is_flaky_run: false,
  stack_trace: TRACE,
  steps: STEPS,
  attachments: [],
}

/** `GET .../step-flips`: steady steps over six runs. */
const STEP_FLIPS = {
  run_id: RUN_ID,
  test_id: TEST_ID,
  test_fingerprint: FINGERPRINT,
  report: { has_step_flip: false, runs_analyzed: 6, total_flips: 0, flips: [], flipping_steps: [], summary: '' },
}

/** `GET /analyze/{test}`: the stored AI analysis (`types/ai.ts` AnalysisResult). */
const ANALYSIS = {
  test_case_id: TEST_ID,
  analysis_id: 'analysis-1',
  root_cause_summary: ROOT_CAUSE,
  failure_category: 'PRODUCT_BUG',
  backend_error_found: false,
  pod_issue_found: false,
  is_flaky: false,
  confidence_score: 82,
  recommended_actions: ['Guard TaxCalculator against negative discount lines.'],
  role_actions: { qa: '', developer: 'Fix TaxCalculator.lineTax for negative lines.', sre: '', release_manager: '' },
  evidence_references: [{ source: 'stacktrace', reference_id: 'st-1', excerpt: 'TotalSpec.java:42' }],
  tools_used: ['fetch_allure_stacktrace'],
  confidence_why: { evidence_count: 1, data_sources: ['stacktrace'], is_llm_inference: true, investigation_depth: 'standard' },
  llm_provider: 'ollama',
  llm_model: 'llama3',
  requires_human_review: false,
}

const TEST_CASE: ApiHandlers = [
  ...LAYOUT,
  [`${BASE}/rich-detail`, () => DETAIL],
  [`${BASE}/history`, () => HISTORY],
  [`${BASE}/steps`, () => STEP_TREE],
  [`${BASE}/step-flips`, () => STEP_FLIPS],
  [`/api/v1/analyze/${TEST_ID}`, () => ANALYSIS],
]

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_NAME })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The scroller's height and the primary block's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<{ scrollHeight: number; primaryTop: number | null; primaries: number }> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
    }
  }, MAIN)
}

test('the error, the stack trace and the AI root cause are in the first viewport at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: TEST_CASE, ready })
  const primary = page.locator('[data-primary]')
  await expect(primary.getByText(ROOT_CAUSE)).toBeVisible()
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD ${PATH.replace(RUN_ID, ':runId').replace(TEST_ID, ':testId')} ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary block').toBe(1)
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // The answer, without scrolling: the error, its stack trace, the AI's root cause.
  await expect(primary.getByText(ERROR).first()).toBeInViewport()
  await expect(primary.getByText('TotalSpec.java:42', { exact: false }).first()).toBeInViewport()
  await expect(primary.getByText(ROOT_CAUSE)).toBeInViewport()
  // The chips above it, and none of the deleted duplicates (§5).
  const chips = page.locator('[data-test-case-chips]')
  for (const text of ['Checkout', 'TotalSpec', 'FAILED', '4.2s', 'smoke', 'checkout']) {
    await expect(chips.getByText(text, { exact: true }).first()).toBeVisible()
  }
  await expect(page.getByText('TotalSpec.java', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Run Date', { exact: true })).toHaveCount(0)
  // The tabs, below the block; History by default.
  const tabs = page.getByRole('tablist', { name: 'Test case sections' })
  await expect(tabs.getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
  expect(requestsTo(api, `${BASE}/history`)).toHaveLength(1)
  // Steps are asked for only when their tab opens.
  expect(requestsTo(api, `${BASE}/steps`)).toHaveLength(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the tabs: ?tab= opens one, and each shows its section', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${PATH}?tab=steps`, { handlers: TEST_CASE, ready })
  const tabs = page.getByRole('tablist', { name: 'Test case sections' })
  await expect(tabs.getByRole('tab', { name: /Steps/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('read the total')).toBeVisible()
  await expect.poll(() => requestsTo(api, `${BASE}/steps`).length).toBe(1)

  await tabs.getByRole('tab', { name: /Details/ }).click()
  await expect(page).toHaveURL(/[?&]tab=details/)
  await expect(page.getByText(FINGERPRINT)).toBeVisible()
  await expect(page.getByText('2.4.1')).toBeVisible()

  await tabs.getByRole('tab', { name: /History/ }).click()
  await expect(page).not.toHaveURL(/[?&]tab=/)
  await expect(page.getByText('Recent runs (oldest → newest)')).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
