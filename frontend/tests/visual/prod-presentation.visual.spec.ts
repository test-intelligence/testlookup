/**
 * Presentation mode baselines (Wave 2.6, VIZ-106, plan 5.2), with every flag
 * off so they show the mode alone: the Overview KPI cards with their metric
 * values on the raised tokens, and the Trends pass-rate frame drawn at
 * presentation scale (16/11: 11 px axis text reads 16 px). The mode is stored
 * as ON before the app loads, exactly as a reload after the toggle finds it.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  ancestorWithClass,
  assertHermetic,
  frameByHeading,
  landmark,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import { OVERVIEW, TRENDS } from './production/fixtures'

test.use(PINNED)

async function presentationOn(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('testlookup-presentation', JSON.stringify({ state: { enabled: true }, version: 0 }))
  })
}

for (const theme of THEMES) {
  test(`overview KPIs in presentation mode — ${theme}`, async ({ page }) => {
    await presentationOn(page)
    const { api, errors } = await openRollout(page, '/overview', {
      theme,
      handlers: OVERVIEW,
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    })
    await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
    const kpis = ancestorWithClass(page.getByText('Total executions', { exact: true }), 'grid')
    await expect(kpis.locator('svg path')).not.toHaveCount(0)

    await visualRegion(page, 'overview-presentation-kpis', theme, kpis)
    assertHermetic(api, errors)
  })

  test(`trends pass rate in presentation mode — ${theme}`, async ({ page }) => {
    await presentationOn(page)
    const { api, errors } = await openRollout(page, '/trends', {
      theme,
      handlers: TRENDS,
      ready: (p) => landmark(p, 'Trend metrics'),
    })
    await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
    const passRate = frameByHeading(page, 'Pass rate trend')
    await waitForCharts(passRate)
    // Drawn at presentation scale.
    await expect(passRate.locator('[data-chart-presentation]').first()).toBeAttached()

    await visualRegion(page, 'trends-presentation-pass-rate', theme, passRate)
    assertHermetic(api, errors)
  })
}
