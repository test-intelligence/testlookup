/**
 * AFTER baselines of /trends with the catalogue ON (Wave 2.6, VIZ-408, plan
 * 5.2), the heatmap included (no flag asked since Phase D, S4): the existing
 * pass-rate frame with release markers and the overlay row (then with both
 * overlays switched on), the suite series (top 7 + Other, a hostile suite
 * name), the p50/p95 duration band (a p95 gap, an inverted day) and the suite
 * x day heatmap (worst first, canvas). Wave 3: the heatmap has its own
 * `/analytics/heatmap` read (the server's top suites by failures, the cut
 * stated, the Rows / Fit colour scale toolbar, the reserved action row), so
 * `trends-on-heatmap` is RE-BASELINED; the other regions keep their PNGs. The
 * page is taller than 2400 px with the catalogue: `PINNED_TALL`, 5000 px tall
 * since Compare (VIZ-605) joined the catalogue.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, settle, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, openRollout, sectionFrame } from '../lib/rollout'
import { TRENDS_ON } from './production/fixtures'

// Compare (VIZ-605) sits between the suite series and the durations, so the
// page no longer fits 4000 px: the heatmap ended at 4085.
test.use({ ...PINNED_TALL, viewport: { width: 1280, height: 5000 } })

for (const theme of THEMES) {
  test(`trends regions, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', {
      theme,
      handlers: TRENDS_ON,
      ready: (p) => landmark(p, 'Trend metrics'),
    })

    const passRate = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    const suites = sectionFrame(page, 'trends-multi-series', 'Pass rate by suite')
    const duration = sectionFrame(page, 'trends-duration', 'Test duration (p50 / p95)')
    const heatmap = sectionFrame(page, 'trends-heatmap', 'Suite pass rate by day')
    for (const [frame, name] of [
      [passRate, 'pass rate'],
      [suites, 'suites'],
      [duration, 'duration'],
      [heatmap, 'heatmap'],
    ] as const) {
      await expectDrawn(frame, name)
    }
    await waitForCharts(passRate)
    await waitForCharts(suites)
    await waitForCharts(duration)
    // The heatmap draws on a canvas once the lazy engine has loaded.
    await expect(heatmap.locator('canvas').first()).toBeVisible()
    await expect(heatmap.locator('[data-heatmap-rows]')).toContainText('Top 7 of 11 suites by failures.')
    await expectNoErrorFrame(page)

    await visualRegion(page, 'trends-on-pass-rate', theme, passRate)
    await visualRegion(page, 'trends-on-multi-series', theme, suites)
    await visualRegion(page, 'trends-on-duration', theme, duration)
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
