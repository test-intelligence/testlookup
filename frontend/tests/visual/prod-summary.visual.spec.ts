/**
 * BEFORE baselines of /reports/summary (Wave 2.6 C0, VIZ-408): the page had
 * no baseline at all, and it is the one whose layout gains the most when the
 * catalogue charts land (a donut, stacked suite bars, a trend and a ranked
 * top-failing chart, all around these four regions). Captured with every
 * feature flag off, the page as it is today, in its default `latest`
 * aggregation over 30 days.
 *
 * Phase D S2: the catalogue mounts on every load, so the page is opened with
 * the catalogue's answers (`SUMMARY_REPORT_ON`) and every flag off, and the
 * regions are captured once the catalogue has drawn (the charts are
 * `prod-summary-on`'s). The charts above the top-failing table push it past
 * 2400 px, so the viewport is `PINNED_TALL` (the shell scrolls inside
 * `#main-content`, so the height changes no pixel of a region).
 *
 * UX redesign P3: the six KPI tiles and the counts strip are ONE row of five
 * (`summary-kpis`, its size changed); `summary-counts` is dropped (the strip
 * is gone: its counts are on the KPI row, its footer facts are the header's
 * subtitle).
 *
 * P3 cleanup: the status donut is gone (the KPI row has its counts) and the
 * trend is a collapsed "Trend" disclosure, so the only catalogue section on
 * the load is the results-by-suite bars, now full width: the two tables sit
 * higher, and their regions keep their names. Baselines from CI.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
  landmark,
  openProductionPage,
  PINNED_TALL,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { expectDrawn, sectionFrame } from '../lib/rollout'
import { HOSTILE_NAME, NOW, PROJECT_ID, SUMMARY_REPORT_ON, USER } from './production/fixtures'

test.use(PINNED_TALL)

for (const theme of THEMES) {
  test(`summary report regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/reports/summary', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: SUMMARY_REPORT_ON,
      ready: (p) => p.getByText('Total tests', { exact: true }),
    })

    // The default aggregation, and the fixture's `latest` totals (186 tests,
    // 149 passed): a page that fell back to another mode or an empty state
    // fails here, not as a screenshot of the wrong thing.
    await expect(page.getByRole('radio', { name: 'Latest run per suite' })).toHaveAttribute('aria-checked', 'true')
    const kpis = landmark(page, 'Summary KPIs')
    await expect(kpis).toContainText('186')
    await expect(kpis).toContainText('80.1%')
    await expect(kpis).toContainText('per unique test')
    // The counts strip's numbers, on the row: 180 evaluated, 149 passed.
    await expect(kpis).toContainText('180 evaluated')
    await expect(kpis).toContainText('149 passed')
    await expect(page.locator('[data-page-header]')).toContainText('Runs in window:')

    const suiteTable = cardByHeading(page, /^Per-suite breakdown/)
    await expect(suiteTable.locator('tbody tr')).toHaveCount(6)
    await expect(suiteTable.getByRole('columnheader', { name: 'Step %' })).toBeVisible()

    const topFailing = cardByHeading(page, /^Top failing tests/)
    await expect(topFailing.locator('tbody tr')).toHaveCount(12)
    // A hostile test name is text, never markup.
    await expect(topFailing.getByText(HOSTILE_NAME, { exact: true })).toBeVisible()
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()

    // The catalogue section above the tables has drawn: nothing moves after this.
    // (No donut since P3; the trend is collapsed, so it is not on the page.)
    const suites = sectionFrame(page, 'summary-suites', 'Results by suite')
    await expectDrawn(suites, 'summary-suites')
    await waitForCharts(suites)
    await expect(page.locator('[data-catalogue-section="summary-donut"], [data-catalogue-section="summary-trend"]')).toHaveCount(0)

    await visualRegion(page, 'summary-kpis', theme, kpis)
    await visualRegion(page, 'summary-suite-table', theme, suiteTable)
    await visualRegion(page, 'summary-top-failing-table', theme, topFailing)
    assertHermetic(api, errors)
  })
}
