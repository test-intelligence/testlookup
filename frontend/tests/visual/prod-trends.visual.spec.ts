/**
 * BEFORE/AFTER baselines of /trends (Wave 2.5, VIZ-104), the page with the
 * most hand-drawn visuals: the verdict's confidence meter (G1), the five
 * KPI glyphs, the run-cadence strip (S1), the CSS-grid daily breakdown
 * (P1), the SVG pass-rate polyline (P2) and the per-suite micro-bars. The
 * 14-day window holds a 7-day gap, a mostly-failing day, broken tests on
 * every third day, and mixed pass/fail days.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
  landmark,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
} from '../lib/production-pages'
import { NOW, PROJECT_ID, TRENDS, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`trends regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/trends', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: TRENDS,
      ready: (p) => landmark(p, 'Trend metrics'),
    })

    // The page resets the shared window to 14 days on mount.
    const cadence = cardByHeading(page, 'Run cadence — last 14 days')
    await expect(cadence).toContainText('7 days with runs · 7 empty')
    const daily = cardByHeading(page, 'Daily breakdown')
    const passRate = cardByHeading(page, 'Pass rate trend')
    await expect(passRate.locator('svg polyline').first()).toBeVisible()
    const suites = cardByHeading(page, 'Suite pass rates · today')
    await expect(suites.getByText('Payments', { exact: true })).toBeVisible()

    await visualRegion(page, 'trends-verdict', theme, landmark(page, 'Trend verdict'))
    await visualRegion(page, 'trends-kpis', theme, landmark(page, 'Trend metrics'))
    await visualRegion(page, 'trends-cadence', theme, cadence)
    await visualRegion(page, 'trends-daily-breakdown', theme, daily)
    await visualRegion(page, 'trends-pass-rate', theme, passRate)
    await visualRegion(page, 'trends-suite-rates', theme, suites)
    assertHermetic(api, errors)
  })
}
