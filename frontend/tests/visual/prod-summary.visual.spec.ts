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
 * four regions are captured once the catalogue has drawn (the charts are
 * `prod-summary-on`'s). The charts above the top-failing table push it past
 * 2400 px, so the viewport is `PINNED_TALL` (the shell scrolls inside
 * `#main-content`, so the height changes no pixel of a region).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  ancestorWithClass,
  assertHermetic,
  cardAround,
  cardByHeading,
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
    const kpis = ancestorWithClass(page.getByText('Total tests', { exact: true }), 'grid')
    await expect(kpis).toContainText('186')
    await expect(kpis).toContainText('80.1%')
    await expect(kpis).toContainText('per unique test')

    const counts = cardAround(page.getByText('Evaluated', { exact: true }))
    await expect(counts).toContainText('180')
    await expect(counts).toContainText('Runs in window:')

    const suiteTable = cardByHeading(page, /^Per-suite breakdown/)
    await expect(suiteTable.locator('tbody tr')).toHaveCount(6)
    await expect(suiteTable.getByRole('columnheader', { name: 'Step %' })).toBeVisible()

    const topFailing = cardByHeading(page, /^Top failing tests/)
    await expect(topFailing.locator('tbody tr')).toHaveCount(12)
    // A hostile test name is text, never markup.
    await expect(topFailing.getByText(HOSTILE_NAME, { exact: true })).toBeVisible()
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()

    // The catalogue sections around the tables have drawn: nothing moves after this.
    for (const [id, title] of [
      ['summary-donut', 'Status breakdown'],
      ['summary-suites', 'Results by suite'],
      ['summary-trend', 'Pass rate trend'],
      ['summary-top-failing', 'Failures by test'],
    ] as const) {
      const frame = sectionFrame(page, id, title)
      await expectDrawn(frame, id)
      await waitForCharts(frame)
    }

    await visualRegion(page, 'summary-kpis', theme, kpis)
    await visualRegion(page, 'summary-counts', theme, counts)
    await visualRegion(page, 'summary-suite-table', theme, suiteTable)
    await visualRegion(page, 'summary-top-failing-table', theme, topFailing)
    assertHermetic(api, errors)
  })
}
