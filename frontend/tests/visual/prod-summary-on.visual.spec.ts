/**
 * AFTER baselines of /reports/summary with the catalogue ON (Wave 2.6,
 * VIZ-408, plan 5.2): the status donut and results-by-suite bars (from the
 * report itself, default `latest` aggregation), the pass-rate trend (its own
 * `/metrics/trends` request, executions), and failures by test (two tests
 * named "login times out" in two suites are two bars; a hostile name).
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

    const donut = sectionFrame(page, 'summary-donut', 'Status breakdown')
    const suites = sectionFrame(page, 'summary-suites', 'Results by suite')
    const trend = sectionFrame(page, 'summary-trend', 'Pass rate trend')
    const failures = sectionFrame(page, 'summary-top-failing', 'Failures by test')
    for (const [frame, name] of [
      [donut, 'donut'],
      [suites, 'suites'],
      [trend, 'trend'],
      [failures, 'failures by test'],
    ] as const) {
      await expectDrawn(frame, name)
      await waitForCharts(frame)
    }
    await expect(donut).toContainText('186')
    await expectNoErrorFrame(page)

    await visualRegion(page, 'summary-on-donut', theme, donut)
    await visualRegion(page, 'summary-on-suites', theme, suites)
    await visualRegion(page, 'summary-on-trend', theme, trend)
    await visualRegion(page, 'summary-on-top-failing', theme, failures)
    assertHermetic(api, errors)
  })
}
