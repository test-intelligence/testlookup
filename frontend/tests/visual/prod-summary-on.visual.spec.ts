/**
 * AFTER baselines of /reports/summary with the catalogue ON (Wave 2.6,
 * VIZ-408, plan 5.2): the status donut and results-by-suite bars (from the
 * report itself, default `latest` aggregation), the pass-rate trend (its own
 * `/metrics/trends` request, executions). UX redesign P3 deleted the fourth
 * region, `summary-on-top-failing` ("Failures by test": §5, a duplicate of the
 * Top failing tests table, whose rows are `prod-summary`'s
 * `summary-top-failing-table`).
 *
 * P3 cleanup: `summary-on-donut` is dropped (the donut is gone: its four
 * counts are the KPI row's, from the same totals); `summary-on-suites` is the
 * page's primary content alone, full width (new pixels); `summary-on-trend` is
 * captured after opening the collapsed "Trend" disclosure that holds it.
 * Baselines from CI.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, openRollout, sectionFrame } from '../lib/rollout'
import { SUMMARY_REPORT_ON } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`summary report regions, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      theme,
      handlers: SUMMARY_REPORT_ON,
      ready: (p) => p.getByText('Total tests', { exact: true }),
    })
    await expect(page.getByRole('radio', { name: 'Latest run per suite' })).toHaveAttribute('aria-checked', 'true')

    const suites = sectionFrame(page, 'summary-suites', 'Results by suite')
    const trend = sectionFrame(page, 'summary-trend', 'Pass rate trend')
    // The trend is in the collapsed "Trend" disclosure: open it.
    await page.getByRole('button', { name: /^Trend/ }).click()
    for (const [frame, name] of [
      [suites, 'suites'],
      [trend, 'trend'],
    ] as const) {
      await expectDrawn(frame, name)
      await waitForCharts(frame)
    }
    // The donut's 186 tests are the KPI row's now.
    await expect(page.getByRole('region', { name: 'Summary KPIs' })).toContainText('186')
    await expect(page.locator('[data-catalogue-section="summary-donut"]')).toHaveCount(0)
    await expectNoErrorFrame(page)

    await visualRegion(page, 'summary-on-suites', theme, suites)
    await visualRegion(page, 'summary-on-trend', theme, trend)
    await expect(page.locator('[data-catalogue-section="summary-top-failing"]')).toHaveCount(0)
    assertHermetic(api, errors)
  })
}
