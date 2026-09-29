/**
 * BEFORE/AFTER baselines of /overview (Wave 2.5, VIZ-104): the KPI cards
 * with their three real sparklines, the verdict card with its pass-rate
 * mini meter (G7), and the execution-trend chart (A1), which drops
 * `broken` executions today (the fixture has them on every third day).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  ancestorWithClass,
  assertHermetic,
  cardAround,
  frameByHeading,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { NOW, OVERVIEW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`overview regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/overview', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: OVERVIEW,
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    })

    const kpis = ancestorWithClass(page.getByText('Total executions', { exact: true }), 'grid')
    // The three KPI cards with a day series draw a sparkline (not a caption).
    await expect(kpis.locator('svg path')).not.toHaveCount(0)
    await expect(kpis.getByText(/no trend line|no executions recorded/)).toHaveCount(0)

    const verdict = cardAround(page.getByText('RELEASE READINESS', { exact: true }))
    await expect(verdict.getByText('Conditional', { exact: false }).first()).toBeVisible()

    const trend = frameByHeading(page, 'Execution trend')
    await waitForCharts(trend)

    await visualRegion(page, 'overview-kpis', theme, kpis)
    await visualRegion(page, 'overview-verdict', theme, verdict)
    await visualRegion(page, 'overview-trend', theme, trend)
    assertHermetic(api, errors)
  })
}
