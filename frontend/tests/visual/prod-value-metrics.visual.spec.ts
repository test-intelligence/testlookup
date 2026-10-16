/**
 * BEFORE/AFTER baseline of /value-metrics (Wave 2.5, VIZ-104): the
 * "Hours saved per month" stacked bars (A4), three model legs drawn in
 * status colours today. W08 TRND-08 (PNGs from CI): NEW `value-by-team`, the
 * By team view's table (`?view=team`: five teams, Unassigned last, a hostile
 * team name as text, the footer's two totals).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  frameByHeading,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { NOW, PROJECT_ID, USER, VALUE_METRICS } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`value metrics regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/value-metrics', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: VALUE_METRICS,
      ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    })

    const monthly = frameByHeading(page, 'Hours saved per month')
    await waitForCharts(monthly)

    await visualRegion(page, 'value-monthly-hours', theme, monthly)
    assertHermetic(api, errors)
  })

  test(`value by team region — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/value-metrics?view=team', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: VALUE_METRICS,
      ready: (p) => p.getByRole('region', { name: 'Value by team' }),
    })
    const table = page.getByRole('region', { name: 'Value by team' })
    await expect(table).toHaveAttribute('data-value-by-team', 'drawn')
    await expect(table.locator('[data-team-row]')).toHaveCount(5)
    await visualRegion(page, 'value-by-team', theme, table)
    assertHermetic(api, errors)
  })
}
