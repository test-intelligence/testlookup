/**
 * AFTER baselines of /overview with the catalogue ON (Wave 2.6, VIZ-408,
 * plan 5.2): the pass-rate trend with release markers (it replaces the
 * Execution-trend card, OD-4), the status donut, and the lazy row's top
 * failing tests and failure categories. In the same load, `overview-verdict`
 * and `overview-kpis` are captured again under their EXISTING names: the
 * catalogue must leave the top of the page byte-identical to
 * `prod-overview`'s PNGs. Since Phase D (S6) the harness turns no flag on:
 * the page asks none.
 *
 * UX redesign P3: the catalogue's headline row (trend + donut) is the page's
 * primary content and its lazy row (top failing + categories) is the default
 * "Top failing" tab under it, so all four sections mount at this height on a
 * plain load and are captured as before (regions unchanged by name). The two
 * top regions are the KpiStrip and the one-line verdict banner now (see
 * `prod-overview.visual.spec.ts`).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { ancestorWithClass, assertHermetic, PINNED, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, openRollout, sectionFrame } from '../lib/rollout'
import { OVERVIEW_ON } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`overview regions, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', {
      theme,
      handlers: OVERVIEW_ON,
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    })

    const kpis = ancestorWithClass(page.getByText('Total executions', { exact: true }), 'grid')
    await expect(kpis).toHaveAttribute('data-kpi-strip', '')
    await expect(kpis.locator('svg path')).not.toHaveCount(0)
    const verdict = page.getByRole('region', { name: 'Release readiness' }).locator('[data-status-banner]')
    await expect(verdict.locator('[data-banner-pill]')).toHaveText('CONDITIONAL')

    const trend = sectionFrame(page, 'overview-trend', 'Pass rate trend')
    const donut = sectionFrame(page, 'overview-donut', 'Status breakdown')
    const topFailing = sectionFrame(page, 'overview-top-failing', 'Top failing tests')
    const categories = sectionFrame(page, 'overview-categories', 'Failure categories')
    for (const [frame, name] of [
      [trend, 'trend'],
      [donut, 'donut'],
      [topFailing, 'top failing'],
      [categories, 'categories'],
    ] as const) {
      await expectDrawn(frame, name)
      await waitForCharts(frame)
    }
    // The fixture's releases inside the 30 days are drawn as markers.
    await expect(trend).toContainText('2026.09')
    await expectNoErrorFrame(page)

    await visualRegion(page, 'overview-kpis', theme, kpis)
    await visualRegion(page, 'overview-verdict', theme, verdict)
    await visualRegion(page, 'overview-on-trend', theme, trend)
    await visualRegion(page, 'overview-on-donut', theme, donut)
    await visualRegion(page, 'overview-on-top-failing', theme, topFailing)
    await visualRegion(page, 'overview-on-categories', theme, categories)
    assertHermetic(api, errors)
  })
}
