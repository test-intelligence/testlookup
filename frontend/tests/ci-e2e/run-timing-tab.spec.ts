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
 * test case's `fixtures-pages.ts` (`TEST_CASE`); the timing answers
 * `fixtures-timing.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { isoAgo, RUN_ID } from '../visual/production/fixtures'
import { TEST_CASE, TEST_CASE_API, TEST_CASE_ID, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import { NO_TIMESTAMPS, REGRESSIONS_PATH, testId, TIMED, TIMING_PATH, withTiming } from '../visual/production/fixtures-timing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

// The answers (a timed run, an old run with no clock) are `fixtures-timing.ts`, shared with the visual baselines.

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
  await expect(slowest.getByRole('heading', { name: 'Slowest tests', exact: true })).toBeVisible()
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
