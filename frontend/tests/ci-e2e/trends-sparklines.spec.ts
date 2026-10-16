/**
 * W08 TRND-06 (contract §2.3), hermetic, on `/trends`:
 *
 *   - By suite › "Suite pass rates": every suite row that is among the 7
 *     busiest series of "Pass rate by suite" draws its sparkline (the kit's
 *     `Sparkline`, a drawn line); a suite of the card that is not among them
 *     reads `—`, its tooltip saying why. Both read ONE request: the card and
 *     "Pass rate by suite" share the `top_n=7` chart-data read
 *     (`rollout-trends.spec.ts` pins exactly one);
 *   - the score disclosure's number is the server summary's
 *     `confidence.score` (`/analytics/trend-summary`, asked on load), and its
 *     run-gap callout names the server's gap.
 *
 * The default coverage fixture has no suite outside the series, so this spec
 * answers `/analytics/coverage` with one more (`Archive`). Harness:
 * `tests/lib/rollout.ts` (fail closed). Fixtures: `trendSummaryBody` (`fixtures.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { CHART_DATA_PATH, PROJECT_ID, TREND_SUMMARY_PATH, trendSummaryBody, TRENDS_ON } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 2000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Trend metrics')
const card = (page: Page) => page.locator('[data-trends-suite-rates]')
/** One suite row of the card: the nearest grid row around the suite's own link. */
const row = (page: Page, suite: string) =>
  card(page)
    .getByRole('link', { name: suite, exact: true })
    .locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " grid ")][1]')
const pageTab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name, exact: true })

const COVERAGE_PATH = '/api/v1/analytics/coverage'
/** The fixture's coverage suites, all of them among the series' 7 busiest. */
const IN_SERIES = ['Auth', 'Checkout', 'Payments', 'Search', 'Notifications']
const OUTSIDE = 'Archive'

/** `/analytics/coverage` with one suite the series does not draw. */
function withSuiteOutsideTheSeries(): ApiHandlers {
  const fixture = TRENDS_ON.find(([m]) => m === COVERAGE_PATH)?.[1]
  if (!fixture) throw new Error('TRENDS_ON answers /analytics/coverage')
  return [
    [
      COVERAGE_PATH,
      (request: ApiRequest) => {
        const body = fixture(request) as { suites: Record<string, unknown>[] }
        const archive = { suite_name: OUTSIDE, unique_tests: 3, passed: 9, failed: 1, skipped: 0, pass_rate: 90, last_run_at: null, last_run_id: null }
        return { ...body, suites: [...body.suites, archive] }
      },
    ],
  ]
}

test('By suite: a sparkline per suite of the series, "—" with its reason for one outside it, from ONE chart-data read', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...withSuiteOutsideTheSeries(), ...TRENDS_ON], ready })
  await pageTab(page, 'By suite').click()
  await expect(card(page)).toBeVisible()
  // Each row's sparkline is a lazy chunk that draws once the shared series lands.
  for (const suite of IN_SERIES) {
    const line = row(page, suite).locator('[data-sparkline] path[data-part="line"]')
    await expect(line, `${suite}: its line is drawn`).toHaveCount(1, { timeout: 20_000 })
    await expect(line).toHaveAttribute('d', /\S/)
  }
  const missing = row(page, OUTSIDE).locator('[data-sparkline-missing]')
  await expect(missing).toHaveText('—')
  await expect(missing).toHaveAttribute('title', 'Not among the 7 busiest suites drawn in "Pass rate by suite"')
  await expect(row(page, OUTSIDE).locator('[data-testid="sparkline"]')).toHaveCount(0)
  // The compare icon of each row opens the latest two runs of its suite.
  await expect(row(page, 'Auth').locator('[data-suite-compare]')).toHaveAttribute('href', '/runs/compare?mode=latest&suite=Auth')
  await networkQuiet(page, api)
  const series = queryOf(api, CHART_DATA_PATH, (q) => q.get('top_n') === '7')
  expect(series, 'the card and "Pass rate by suite" share one read').toHaveLength(1)
  expect(series[0].getAll('group_by')).toEqual(['day', 'suite'])
  assertHermetic(api, errors)
})

test('the score disclosure and the run-gap callout are the server summary\'s', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/trends', { handlers: TRENDS_ON, ready })
  const summary = trendSummaryBody(14)
  await expect(page.getByText(new RegExp(`confidence ${summary.confidence.score} / 100`))).toBeVisible()
  await networkQuiet(page, api)
  const reads = queryOf(api, TREND_SUMMARY_PATH)
  expect(reads, 'one summary read on load').toHaveLength(1)
  expect(reads[0].get('project_id')).toBe(PROJECT_ID)
  expect(reads[0].get('days')).toBe('14')
  // The fixture's 7-day gap (11 to 5 days ago) is the server's, and it is not ongoing.
  expect(summary.gap).toMatchObject({ silent_days: 7, ongoing: false })
  await expect(page.getByText('Run gap in this window')).toBeVisible()
  expect(requestsTo(api, TREND_SUMMARY_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})
