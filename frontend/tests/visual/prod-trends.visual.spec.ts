/**
 * BEFORE/AFTER baselines of /trends (Wave 2.5, VIZ-104), the page with the
 * most hand-drawn visuals: the five KPI glyphs, the run-cadence strip (S1),
 * the CSS-grid daily breakdown (P1), the SVG pass-rate polyline (P2) and the
 * per-suite micro-bars. The 14-day window holds a 7-day gap, a mostly-failing
 * day, broken tests on every third day, and mixed pass/fail days.
 *
 * Phase D S3: the page asks no flag; its catalogue mounts on every load, so
 * it is opened with the catalogue's answers (`TRENDS_ON`) and every flag off.
 * The pass-rate frame is always the analysis frame now; its region is
 * `prod-trends-on`'s `trends-on-pass-rate` (the flag-off `trends-pass-rate`
 * shot is deleted).
 *
 * UX redesign P3 (the page template): the verdict card is gone — its score
 * and dimensions are a collapsed disclosure below the hero — so the
 * `trends-verdict` region is DROPPED (its PNGs go with it). The KPI strip is
 * the compact `KpiStrip` (same landmark, new pixels). The cadence strip and
 * the daily breakdown are the Volume tab's (the default), full width; the
 * suite pass rates card is the By suite tab's, captured after that tab is
 * opened.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
  frameByHeading,
  landmark,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { expectDrawn, sectionFrame } from '../lib/rollout'
import { NOW, PROJECT_ID, TRENDS_ON, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`trends regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/trends', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: TRENDS_ON,
      ready: (p) => landmark(p, 'Trend metrics'),
    })

    // The page resets the shared window to 14 days on mount.
    const cadence = cardByHeading(page, 'Run cadence — last 14 days')
    await expect(cadence).toContainText('7 days with runs · 7 empty')
    // Both plots are kit chart frames since Wave 2.5 (the frame is the card).
    const daily = frameByHeading(page, 'Daily breakdown')
    await waitForCharts(daily)
    // The analysis frame has drawn: nothing above or beside it moves after this.
    const passRate = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    await expectDrawn(passRate, 'pass rate')
    await waitForCharts(passRate)

    await visualRegion(page, 'trends-kpis', theme, landmark(page, 'Trend metrics'))
    await visualRegion(page, 'trends-cadence', theme, cadence)
    await visualRegion(page, 'trends-daily-breakdown', theme, daily)

    // P3: the suite pass rates card is the By suite tab's.
    await page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name: 'By suite', exact: true }).click()
    // UX redesign P2: the title says the window it shows (it said "· today" for every window).
    const suites = cardByHeading(page, 'Suite pass rates — last 14 days')
    await expect(suites.getByText('Payments', { exact: true })).toBeVisible()
    await visualRegion(page, 'trends-suite-rates', theme, suites)
    assertHermetic(api, errors)
  })
}
