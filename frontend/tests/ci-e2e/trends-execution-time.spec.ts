/**
 * EXEC-04b (absorbs TRND-09), hermetic: execution time where a reader finds
 * it, against the fail-closed fixtures (`tests/lib/production-pages.ts`,
 * `EXECUTION` in `tests/visual/production/fixtures.ts`, pinned to `NOW`).
 *
 *   - Trends › Execution time (`?tab=durations`, its id kept): ONE
 *     `/analytics/execution-metrics` read in the page's scope (project,
 *     window, suite), the totals with wall clock and summed time named apart,
 *     wall vs summed per day (a gap is "—" in the table, never 0), the
 *     per-suite table (the hostile suite name as text), the biggest
 *     regressions, the 20 slowest tests with the 40 s outlier clamped off the
 *     scale, and the duration distribution on the server's buckets.
 *   - The suite page: Charts › "Suite execution time" (one suite-history read,
 *     the last 30 runs) and the Runs tab's Wall-clock column ("not reported"
 *     for a run without timestamps, never its summed time).
 *   - The run header and the Runs table: wall clock and summed test time,
 *     each labelled.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, networkQuiet, openRollout, requestsTo, section, sectionFrame } from '../lib/rollout'
import {
  EXECUTION_METRICS_PATH,
  HOSTILE_NAME,
  PROJECT_ID,
  RUN_ID,
  RUNS_PAGE,
  SUITE_DETAIL_ON,
  SUITE_HISTORY_PATH,
  SUITE_ID,
  TRENDS_ON,
} from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Trend metrics')
const EXECUTION = 'trends-execution'
const PER_DAY = 'Wall clock vs test time per day'

test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const executionReads = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, EXECUTION_METRICS_PATH)

test('Trends › Execution time: one read in the page scope, every block drawn, names as text', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/trends?tab=durations', { handlers: TRENDS_ON, ready })
  const tab = page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name: 'Execution time', exact: true })
  await expect(tab).toHaveAttribute('aria-selected', 'true')
  await expectDrawn(sectionFrame(page, EXECUTION, PER_DAY), 'wall vs summed per day')
  await networkQuiet(page, api)
  expect(executionReads(api)).toEqual([`GET ${EXECUTION_METRICS_PATH}?project_id=${P}&days=14`])

  const root = section(page, EXECUTION)
  // The totals: the two durations named apart, and the parallelism between them.
  const totals = root.locator('[data-execution-totals]')
  await expect(totals.getByRole('heading', { name: 'Execution time', exact: true })).toBeVisible()
  await expect(totals.locator('[data-execution-stat="wall"]')).toContainText('Wall clock')
  await expect(totals.locator('[data-execution-stat="summed"]')).toContainText('Test time (summed)')
  await expect(totals.locator('[data-execution-stat="parallelism"]')).toContainText('1.9×')
  await expect(totals.getByRole('link', { name: 'How to read these figures' })).toHaveAttribute('href', '/docs/charts#execution-time')
  // EXEC-06: the per-suite table as CSV (its download is the service's own, covered by vitest).
  await expect(totals.getByRole('button', { name: 'Export CSV' })).toBeVisible()

  // Per day: a day whose runs reported no wall clock is a gap, said under the plot and "—" in the table.
  const perDay = sectionFrame(page, EXECUTION, PER_DAY)
  await expect(perDay.locator('[data-chart-wall-gaps]')).toContainText('summed test time but no wall clock')
  await perDay.getByRole('button', { name: /table/i }).first().click()
  const table = perDay.getByRole('table')
  await expect(table.getByRole('columnheader', { name: 'Wall clock' })).toBeVisible()
  await expect(table.getByRole('cell', { name: '—' }).first()).toBeVisible()
  await expect(table).not.toContainText(/\b0ms\b/)

  // By suite: the change against the previous window; the hostile name is text, never markup.
  const suites = root.locator('[data-execution-suites]')
  await expect(suites.locator('[data-execution-suite="payments"]')).toContainText('+79.3%')
  await expect(suites).toContainText(HOSTILE_NAME)
  await expect(suites.locator('[data-execution-suite]').last()).toContainText('not reported')
  await expect(root.locator('img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()

  // Regressions: the suite, and the tests block saying why it has nothing to judge.
  const regressions = root.locator('[data-execution-regressions]')
  await expect(regressions.locator('[data-regression-suite="payments"]')).toContainText('1.8× slower wall clock')
  await expect(regressions.locator('[data-regression-not-measured]')).toContainText('duration baseline of at least 10 samples')

  // TRND-09: the 20 slowest — the 40 s outlier runs off a clamped scale, the rest are not slivers.
  const slowest = sectionFrame(page, EXECUTION, '20 slowest tests (p95)')
  await expectDrawn(slowest, 'slowest tests')
  const bars = slowest.getByTestId('ranked-bar')
  await expect(bars).toHaveCount(20)
  await expect(bars.first()).toHaveAttribute('data-off-scale', '')
  await expect(slowest.getByText('40.0s', { exact: true })).toBeVisible()
  await expect(slowest.locator('[data-chart-off-scale]')).toContainText('Bars stop at')
  const widths = await bars.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width))
  const full = widths[0]
  expect(Math.min(...widths.slice(1)) / full, 'the fastest of the twenty is no sliver').toBeGreaterThan(0.4)

  // TRND-09: the distribution, on the server's fixed buckets, the overflow bucket hatched.
  const histogram = sectionFrame(page, EXECUTION, 'Duration distribution')
  await expectDrawn(histogram, 'duration distribution')
  await expect(histogram.locator('[data-chart="duration-histogram"] figcaption')).toContainText('the last bucket counts everything from 5m 0s up')
  await expect(histogram.locator('[data-overflow="true"]')).toHaveCount(1)
  await expect(histogram).toContainText('p50 240ms · p95 3.1s over')

  // The p50 / p95 band is still on the tab, under the section.
  await expectDrawn(sectionFrame(page, 'trends-duration', 'Test duration (p50 / p95)'), 'duration band')
  await expectNoErrorFrame(page)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Trends › Execution time follows the page scope: the window and the suite filter are new reads', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/trends?tab=durations', { handlers: TRENDS_ON, ready })
  await expectDrawn(sectionFrame(page, EXECUTION, PER_DAY), 'per day')
  await page.getByRole('radiogroup', { name: 'Time window' }).getByRole('radio', { name: '30d' }).click()
  await expect.poll(() => executionReads(api).at(-1)).toBe(`GET ${EXECUTION_METRICS_PATH}?project_id=${P}&days=30`)
  await page.getByLabel('Test suite').selectOption('Payments')
  await expect.poll(() => executionReads(api).at(-1)).toBe(`GET ${EXECUTION_METRICS_PATH}?project_id=${P}&days=30&suite_name=Payments`)
  // A suite filter counts the suite's own executions, and says so.
  await expect(section(page, EXECUTION).locator('[data-execution-grain]')).toContainText("selected suites' own executions")
  await expectDrawn(sectionFrame(page, EXECUTION, PER_DAY), 'per day, one suite')
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the suite page: Charts › Suite execution time, and the Runs tab’s Wall-clock column', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/suites/${SUITE_ID}?tab=charts`, {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    days: 30,
  })
  // The section is the composite's last: bring it near, then it asks once. The
  // lazy placeholder is swapped for the section as soon as it nears the viewport,
  // which can happen mid-scroll ("Element is not attached to the DOM", both CI
  // attempts on #263): retry the scroll on whichever of the two is attached.
  const trend = page.locator('[data-catalogue-section="suite-execution"] [data-chart-frame]')
  await expect(async () => {
    await page.locator('[data-lazy-section="suite-execution"], [data-catalogue-section="suite-execution"]').first().scrollIntoViewIfNeeded({ timeout: 2_000 })
  }).toPass({ timeout: 15_000 })
  await expectDrawn(trend, 'suite execution time')
  await expect(trend.getByRole('heading', { name: 'Suite execution time' })).toBeVisible()
  await expect(trend.locator('[data-chart="execution-time-trend"]')).toHaveAttribute('data-grain', 'run')
  await expect(trend.locator('[data-chart-wall-gaps]')).toContainText('1 run has summed test time but no wall clock')
  await expect(trend.locator('[data-stat="wall"]')).toContainText('over 7 runs')
  await networkQuiet(page, api)
  expect(requestsTo(api, SUITE_HISTORY_PATH)).toEqual([`GET ${SUITE_HISTORY_PATH}?project_id=${P}&days=30&suite_name=Auth&limit=30`])

  await page.getByRole('tablist', { name: 'Suite sections' }).getByRole('tab', { name: /^Runs/ }).click()
  const runs = page.locator('[data-suite-recent-runs]')
  await expect(runs.getByRole('columnheader', { name: 'Wall clock' })).toBeVisible()
  const cells = runs.locator('[data-wall-clock]')
  await expect(cells).toHaveCount(8)
  await expect(runs.locator('[data-wall-clock="not-reported"]')).toHaveCount(1)
  await expect(runs.locator('[data-wall-clock="not-reported"]')).toHaveText('not reported')
  await expect(runs.locator('[data-wall-clock="measured"]')).toHaveCount(7)
  await networkQuiet(page, api)
  expect(requestsTo(api, SUITE_HISTORY_PATH).at(-1)).toBe(`GET ${SUITE_HISTORY_PATH}?project_id=${P}&days=30&suite_name=Auth&limit=500`)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the run header and the Runs table: wall clock and summed test time, each labelled', async ({ page }) => {
  const opened = await openRollout(page, `/runs/${RUN_ID}`, {
    handlers: RUN_PAGE,
    ready: (p) => p.getByRole('heading', { level: 1, name: /^Run #/ }),
  })
  const header = page.locator('[data-run-timing]')
  await expect(header).toContainText('Wall clock')
  await expect(header).toContainText('Test time (summed)')
  await expect(header.locator('[data-run-timing-figure="parallelism"]')).toHaveText('1.9×')
  expect(opened.errors).toEqual([])

  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready: (p) => p.getByRole('heading', { level: 1 }) })
  await networkQuiet(page, api)
  const timing = page.locator('tbody tr td[title*="Wall clock"]')
  // Two runs in three reported a wall clock; the others show their summed time, labelled.
  await expect(timing.filter({ hasText: / wall · / }).first()).toBeVisible()
  await expect(timing.filter({ hasText: / summed · / }).first()).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
