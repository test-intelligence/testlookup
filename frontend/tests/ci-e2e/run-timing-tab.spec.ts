/**
 * EXEC-04 on `/runs/:runId?tab=timing` and the test case's History, hermetic
 * (the API is mocked to the EXEC-03 contract, `docs/.../EXEC-03-contract.md`):
 *
 *  - a timed run: "Wall clock 9.0s · Test time 17.4s · Parallelism 1.9×", the
 *    waterfall on two worker rows whose bars overlap in time, the failed test's
 *    bar in the failed pattern, the 20 slowest with the baseline; clicking the
 *    failed bar opens that test beside the tab;
 *  - an old run with no timestamps: "Wall clock: not reported", the reason
 *    "Report carried no timestamps." and no empty chart, the durations still
 *    ranked;
 *  - the timing is read once, only when the tab opens, and the regressions
 *    card is fed from it (no second read);
 *  - the test case's History: the duration per run with the baseline band and
 *    a 4x run ringed.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). The run page's
 * fixtures are `tests/visual/production/fixtures-run.ts` (`RUN_PAGE`), the
 * test case's `fixtures-pages.ts` (`TEST_CASE`); the timing answers are here.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { type ApiHandlers } from '../lib/production-pages'
import { isoAgo, RUN_ID } from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import { TEST_CASE, TEST_CASE_API, TEST_CASE_ID, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const TIMING_PATH = `/api/v1/runs/${RUN_ID}/timing`
const REGRESSIONS_PATH = `/api/v1/runs/${RUN_ID}/duration-regressions`

// ── The answers ─────────────────────────────────────────────────────────────

const T0 = Date.parse(isoAgo(0, 1.5))
const at = (ms: number) => new Date(T0 + ms).toISOString()

/** Two workers, six tests each, back to back: 9.0 s on the clock, 17.4 s summed. Worker 0's third test failed. */
const LANES: [string, number, number, number[]][] = [
  ['0', 0, 60, [1500, 1200, 1800, 1400, 1300, 1500]],
  ['1', 100, 40, [2000, 1100, 1600, 1700, 1300, 1000]],
]
const testId = (worker: string, i: number) =>
  worker === '0' && i === 2 ? TEST_CASE_ID : `66666666-6666-4666-8666-${worker.padStart(6, '0')}${String(i).padStart(6, '0')}`

function timelineItems() {
  const items: Record<string, unknown>[] = []
  for (const [worker, start, gap, durations] of LANES) {
    let offset = start
    durations.forEach((d, i) => {
      const failed = worker === '0' && i === 2
      items.push({
        test_id: testId(worker, i),
        test_name: failed ? TEST_CASE_NAME : `auth test ${worker}.${i}`,
        suite_name: 'Auth',
        status: failed ? 'FAILED' : 'PASSED',
        worker_id: worker,
        started_at: at(offset),
        finished_at: at(offset + d),
        offset_ms: offset,
        duration_ms: d,
      })
      offset += d + gap
    })
  }
  return items.sort((a, b) => (a.offset_ms as number) - (b.offset_ms as number))
}

const slow = (id: string, name: string, duration: number, extra: Record<string, unknown> = {}) => ({
  test_id: id, test_name: name, test_fingerprint: `fp-${id}`, suite_name: 'Auth', status: 'PASSED', duration_ms: duration,
  started_at: null, finished_at: null, setup_ms: null, call_ms: null, teardown_ms: null, worker_id: null,
  timing_source: 'report', baseline: null, baseline_ratio: null, ...extra,
})

const SLOWEST = [
  slow(testId('1', 0), 'auth test 1.0', 2000, { worker_id: '1' }),
  slow(TEST_CASE_ID, TEST_CASE_NAME, 1800, {
    status: 'FAILED', worker_id: '0', setup_ms: 120, call_ms: 1600, teardown_ms: 80,
    baseline: { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 1800, last_observed_at: at(4620) },
    baseline_ratio: 2,
  }),
  slow(testId('1', 3), 'auth test 1.3', 1700, { worker_id: '1' }),
]

const FINDINGS = {
  run_id: RUN_ID, schema_version: 1, measured: true, reason: null, stored: true, evaluated_at: at(9500),
  spikes: [{
    test_fingerprint: `fp-${TEST_CASE_ID}`, test_name: TEST_CASE_NAME, suite_name: 'Auth', observed_ms: 1800,
    baseline_mean_ms: 900, baseline_stddev_ms: 40, baseline_p95_ms: 980, sample_count: 14, sigma: 22.5, delta_ms: 900,
    ratio: 2, previous_ms: 910, was_slow_before: false, new: true,
  }],
  spike_count: 1, suites: [], suite_regression_count: 0,
  test_check: { measured: true, reason: null }, suite_check: { measured: true, reason: null }, notified: {},
}

const TIMED = {
  run_id: RUN_ID, project_id: '11111111-1111-4111-8111-111111111111', status: 'FAILED', build_number: '240', branch: 'main', created_at: at(0),
  run: { wall_clock_ms: 9000, summed_ms: 17400, parallelism: 1.93, timing_source: 'report', exec_started_at: at(0), exec_finished_at: at(9000), test_count: 12, timed_count: 12, duration_count: 12 },
  phases: { measured: true, reason: null, tests_with_phases: 12, setup_ms: 1440, call_ms: 14000, teardown_ms: 960, setup_share: 0.0883, teardown_share: 0.0589 },
  suites: {
    measured: true, reason: null, total: 1, truncated: false,
    items: [{ suite_name: 'Auth', suite_key: 'auth', source: 'report', started_at: at(0), finished_at: at(9000), wall_ms: 9000, summed_ms: 17400, setup_ms: null, teardown_ms: null, test_count: 12, timed_count: 12, passed: 11, failed: 1, broken: 0, skipped: 0, unknown: 0, parallelism: 1.93 }],
  },
  slowest: { limit: 20, total: 12, measured: true, reason: null, items: SLOWEST },
  workers: {
    measured: true, reason: null, total: 2, truncated: false,
    lanes: [
      { worker_id: '0', test_count: 6, busy_ms: 8700, started_at: at(0), finished_at: at(9000), span_ms: 9000 },
      { worker_id: '1', test_count: 6, busy_ms: 8700, started_at: at(100), finished_at: at(9000), span_ms: 8900 },
    ],
  },
  timeline: { measured: true, reason: null, limit: 2000, total: 12, truncated: false, origin: at(0), end: at(9000), items: timelineItems() },
  duration_findings: FINDINGS,
}

/** An old JUnit run: durations only, no clock. */
const NO_TIMESTAMPS = {
  ...TIMED,
  run: { wall_clock_ms: null, summed_ms: 17400, parallelism: null, timing_source: 'derived_sum', exec_started_at: null, exec_finished_at: null, test_count: 12, timed_count: 0, duration_count: 12 },
  phases: { measured: false, reason: 'report carried no setup/teardown split', tests_with_phases: 0, setup_ms: null, call_ms: null, teardown_ms: null, setup_share: null, teardown_share: null },
  suites: { measured: false, reason: 'suite timing was not recorded for this run yet', total: 0, truncated: false, items: [] },
  slowest: { limit: 20, total: 12, measured: true, reason: null, items: SLOWEST.map((t) => ({ ...t, worker_id: null, setup_ms: null, call_ms: null, teardown_ms: null })) },
  workers: { measured: false, reason: 'report carried no timestamps', total: 0, truncated: false, lanes: [] },
  timeline: { measured: false, reason: 'report carried no timestamps', limit: 2000, total: 0, truncated: false, origin: null, end: null, items: [] },
  duration_findings: null,
}

const withTiming = (body: unknown): ApiHandlers => [[TIMING_PATH, () => body], ...TEST_CASE, ...RUN_PAGE]

// ── The specs ───────────────────────────────────────────────────────────────

const timeline = (page: Page) => page.locator('[data-chart="execution-timeline"]')
const kpi = (page: Page, id: string) => page.locator(`[data-timing-kpi="${id}"] [data-timing-kpi-value]`)

test('a timed run: the figures, two overlapping worker rows, the failed bar, the slowest; a bar opens its test', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=timing`, { handlers: withTiming(TIMED), ready: timeline })

  await expect(kpi(page, 'wall')).toHaveText('9.0s')
  await expect(kpi(page, 'summed')).toHaveText('17.4s')
  await expect(kpi(page, 'parallelism')).toHaveText('1.9×')
  await expect(page.locator('[data-primary]')).toHaveAttribute('aria-label', 'Execution time')

  // Two worker rows; every test drawn; the rows overlap in time.
  const chart = timeline(page)
  await expect(chart.locator('[data-timeline-lane]')).toHaveCount(2)
  await expect(chart.locator('[data-timeline-lane][data-worker-id="0"]')).toContainText('Worker 0')
  await expect(chart.locator('[data-timeline-lane][data-worker-id="1"]')).toContainText('Worker 1')
  await expect(chart.locator('[data-timeline-bar]')).toHaveCount(12)
  const first0 = await chart.locator(`[data-timeline-bar][data-test-id="${testId('0', 0)}"]`).boundingBox()
  const first1 = await chart.locator(`[data-timeline-bar][data-test-id="${testId('1', 0)}"]`).boundingBox()
  expect(first0 && first1).toBeTruthy()
  expect(first1!.x).toBeLessThan(first0!.x + first0!.width)
  expect(first1!.y).toBeGreaterThan(first0!.y)

  // The failed test's bar: the failed status, filled with the failed pattern.
  const failed = chart.locator('[data-timeline-bar][data-status="failed"]')
  await expect(failed).toHaveCount(1)
  await expect(failed).toHaveAttribute('data-test-id', TEST_CASE_ID)
  await expect(failed).toHaveAttribute('fill', /chart-pattern-failed/)

  // The slowest tests, with the baseline.
  const slowest = page.getByRole('region', { name: 'Slowest tests' })
  await expect(slowest.getByRole('heading', { name: '20 slowest tests' })).toBeVisible()
  await expect(slowest.locator('[data-timing-slow-test]')).toHaveCount(3)
  await expect(slowest.locator(`[data-timing-slow-test="${TEST_CASE_ID}"]`)).toContainText('2× baseline')

  // The regressions card is fed from the timing answer.
  await expect(page.getByRole('region', { name: 'Duration regressions' })).toContainText(TEST_CASE_NAME)

  // Hovering a bar names it; clicking it opens the test beside the tab.
  await failed.hover()
  await expect(page.locator('[data-timeline-tip]')).toContainText(TEST_CASE_NAME)
  await failed.click()
  await expect(page.getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', TEST_CASE_PATH)
  await expect(page.locator('[data-timeline-mark="selected"]')).toHaveCount(1)

  await networkQuiet(page, api)
  expect(requestsTo(api, TIMING_PATH)).toHaveLength(1)
  expect(requestsTo(api, REGRESSIONS_PATH)).toEqual([])
  assertHermetic(api, errors)
})

test('an old run without timestamps: "not reported", the reason, no empty chart, the durations still ranked', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=timing`, {
    handlers: withTiming(NO_TIMESTAMPS),
    ready: (p) => p.locator('[data-timing-unmeasured="timeline"]'),
  })
  await expect(kpi(page, 'wall')).toHaveText('not reported')
  await expect(kpi(page, 'parallelism')).toHaveText('not reported')
  await expect(kpi(page, 'summed')).toHaveText('17.4s')
  await expect(page.locator('[data-timing-unmeasured="timeline"]')).toContainText('Report carried no timestamps.')
  await expect(page.locator('[data-timing-unmeasured="workers"]')).toContainText('Report carried no timestamps.')
  await expect(timeline(page)).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Slowest tests' }).locator('[data-timing-slow-test]')).toHaveCount(3)
  await expect(page.locator('[data-run-timing-tab]')).not.toContainText(/\b0ms\b/)
  // No findings in the timing answer: the card reads the run's regressions itself.
  await expect(page.getByRole('region', { name: 'Duration regressions' })).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, REGRESSIONS_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('the timing is read only when the tab opens', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, {
    handlers: withTiming(TIMED),
    ready: (p) => p.locator('[data-primary] table'),
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, TIMING_PATH)).toEqual([])
  await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Timing' }).click()
  await expect(page).toHaveURL(/[?&]tab=timing/)
  await expect(timeline(page).locator('[data-timeline-bar]')).toHaveCount(12)
  await networkQuiet(page, api)
  expect(requestsTo(api, TIMING_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})

// ── The test case's History: duration across runs ──────────────────────────

const HISTORY_POINTS = Array.from({ length: 10 }, (_, i) => ({
  run_id: i === 0 ? RUN_ID : `44444444-4444-4444-8444-${String(100000000000 + i)}`,
  run_label: `Run #${60 - i}`,
  build_number: String(240 - i),
  run_seq: 60 - i,
  status: i === 0 ? 'FAILED' : 'PASSED',
  // Newest first; run #60 (this run) took 4x its baseline, run #57 reported no duration.
  duration_ms: i === 0 ? 3600 : i === 3 ? null : 880 + (i % 3) * 20,
  created_at: isoAgo(i, 2),
  started_at: null, finished_at: null, setup_ms: null, call_ms: null, teardown_ms: null, worker_id: null, timing_source: null,
}))

const HISTORY = {
  run_id: RUN_ID, test_id: TEST_CASE_ID, test_fingerprint: `fp-${TEST_CASE_ID}`, test_name: TEST_CASE_NAME,
  history: HISTORY_POINTS,
  flakiness: { is_flaky: false, failure_rate: 0.1, failure_rate_pct: 10, impact_score: 1, classification: 'HEALTHY', window_days: 30, total_runs: 10, passed: 9, failed: 1 },
  metadata: {
    owner: null, assigned_to_user_id: null, suite: 'Auth', severity: null, feature: null, first_seen_run_id: null,
    first_seen_run_label: 'Run #51', first_seen_at: isoAgo(9), last_seen_run_id: RUN_ID, last_seen_run_label: 'Run #60',
    last_seen_at: isoAgo(0, 2), created_at: isoAgo(9), updated_at: isoAgo(0, 2),
  },
  duration_stats: { window_days: 30, n: 9, p50_ms: 900, p95_ms: 2400, max_ms: 3600, measured: true, reason: null },
  baseline: { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 3600, last_observed_at: isoAgo(0, 2) },
}

test('the test case History shows the duration per run with the baseline band, and the 4x run ringed', async ({ page }) => {
  const { api, errors } = await openRollout(page, TEST_CASE_PATH, {
    handlers: [[`${TEST_CASE_API}/history`, () => HISTORY], ...TEST_CASE],
    ready: (p) => p.locator('[data-duration-history-summary]'),
  })
  // Collapsed by default (fold budget): the stats line first, the chart on demand.
  const summary = page.locator('[data-duration-history-summary]')
  await expect(summary).toHaveText(/p50 900ms · p95 2\.4s · 1 slow run/)
  await expect(page.locator('[data-duration-history-disclosure]')).not.toHaveAttribute('open', '')
  await summary.click()
  const chart = page.locator('[data-chart="test-duration-history"]')
  await expect(chart).toBeVisible()
  await expect(chart.locator('[data-duration-point]')).toHaveCount(9)
  await expect(chart.locator('[data-duration-baseline-band]')).toHaveCount(1)
  await expect(chart.locator('[data-duration-ref="p50"]')).toHaveCount(1)
  await expect(chart.locator('[data-duration-ref="p95"]')).toHaveCount(1)
  const outlier = chart.locator('[data-duration-point="outlier"]')
  await expect(outlier).toHaveCount(1)
  await expect(chart.locator('[data-duration-outlier-label]')).toHaveText('4× baseline')
  const section = page.getByRole('region', { name: 'Duration across runs' })
  // The newest run's tooltip states it against the baseline; the run without a duration says so.
  await chart.locator('[data-duration-hit]').nth(9).hover()
  await expect(page.locator('[data-duration-history-tip]')).toContainText('4× baseline')
  await chart.locator('[data-duration-hit]').nth(6).hover()
  await expect(page.locator('[data-duration-history-tip]')).toContainText('Run #57')
  await expect(page.locator('[data-duration-history-tip]')).toContainText('not reported')
  await expect(section).toContainText('Baseline: mean 900ms ± 40ms over 14 runs, p95 980ms.')
  await expect(section).toContainText('1 run took at least 2× the baseline mean (ringed).')
  // The ringed point sits far above the band.
  const ring = await outlier.locator('circle').first().boundingBox()
  const band = await chart.locator('[data-duration-baseline-band]').boundingBox()
  expect(ring!.y + ring!.height).toBeLessThan(band!.y)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
