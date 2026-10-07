/**
 * AFTER baselines of /trends with the catalogue ON (Wave 2.6, VIZ-408, plan
 * 5.2), the heatmap included (no flag asked since Phase D, S4): the existing
 * pass-rate frame with release markers and the overlay row (then with both
 * overlays switched on), the suite series (top 7 + Other, a hostile suite
 * name), the p50/p95 duration band (a p95 gap, an inverted day) and the suite
 * x day heatmap (worst first, canvas). Wave 3: the heatmap has its own
 * `/analytics/heatmap` read (the server's top suites by failures, the cut
 * stated, the Rows / Fit colour scale toolbar, the reserved action row), so
 * `trends-on-heatmap` is RE-BASELINED; the other regions keep their PNGs. C1
 * adds `trends-on-compare`: Compare draws (the busiest three suites, its
 * Suites / Releases pickers).
 *
 * UX redesign P3 (the page template): the pass-rate frame is the hero, full
 * width under the KPI strip; the catalogue's sections are the page's tabs —
 * By suite (the suite series and Compare, beside the suite pass rates card),
 * Durations, Heatmap — each captured after its tab is opened. Every region
 * keeps its name; the sizes changed with the layout.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, settle, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, openRollout, sectionFrame } from '../lib/rollout'
import { TRENDS_ON } from './production/fixtures'

// The page was taller than 4000 px with the whole catalogue in one stack; a
// tab is shorter, and the same tall viewport keeps every region unscrolled.
test.use({ ...PINNED_TALL, viewport: { width: 1280, height: 5000 } })

const openTab = (page: Page, name: string) =>
  page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name, exact: true }).click()

for (const theme of THEMES) {
  test(`trends regions, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', {
      theme,
      handlers: TRENDS_ON,
      ready: (p) => landmark(p, 'Trend metrics'),
    })

    const passRate = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    await expectDrawn(passRate, 'pass rate')
    await waitForCharts(passRate)
    await visualRegion(page, 'trends-on-pass-rate', theme, passRate)

    // By suite: the suite series and Compare (C1: the busiest three suites, its own pickers).
    await openTab(page, 'By suite')
    const suites = sectionFrame(page, 'trends-multi-series', 'Pass rate by suite')
    const compare = sectionFrame(page, 'trends-compare', 'Compare')
    await expectDrawn(suites, 'suites')
    await expectDrawn(compare, 'compare')
    await waitForCharts(suites)
    await waitForCharts(compare)
    await expectNoErrorFrame(page)
    await visualRegion(page, 'trends-on-multi-series', theme, suites)
    await visualRegion(page, 'trends-on-compare', theme, compare)

    await openTab(page, 'Durations')
    const duration = sectionFrame(page, 'trends-duration', 'Test duration (p50 / p95)')
    await expectDrawn(duration, 'duration')
    await waitForCharts(duration)
    await expectNoErrorFrame(page)
    await visualRegion(page, 'trends-on-duration', theme, duration)

    await openTab(page, 'Heatmap')
    const heatmap = sectionFrame(page, 'trends-heatmap', 'Suite pass rate by day')
    await expectDrawn(heatmap, 'heatmap')
    // The heatmap draws on a canvas once the lazy engine has loaded.
    await expect(heatmap.locator('canvas').first()).toBeVisible()
    await expect(heatmap.locator('[data-heatmap-rows]')).toContainText('Top 7 of 11 suites by failures.')
    await expectNoErrorFrame(page)
    await visualRegion(page, 'trends-on-heatmap', theme, heatmap)

    // Trend analysis switched on by the reader: both overlays, then the pointer and focus leave.
    for (const key of ['movingAverage', 'trendLine']) {
      const toggle = passRate.locator(`[data-trend-toggle="${key}"]`)
      await expect(toggle, `${key} is available on this window`).not.toHaveAttribute('aria-disabled', 'true')
      await toggle.click()
      await expect(toggle).toHaveAttribute('aria-pressed', 'true')
    }
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await page.mouse.move(0, 0)
    await settle(page)
    await visualRegion(page, 'trends-on-trend-analysis', theme, passRate)
    assertHermetic(api, errors)
  })
}
