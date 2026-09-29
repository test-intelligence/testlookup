/**
 * BEFORE/AFTER baseline of /value-metrics (Wave 2.5, VIZ-104): the
 * "Hours saved per month" stacked bars (A4), three model legs drawn in
 * status colours today.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
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

    const monthly = cardByHeading(page, 'Hours saved per month')
    await waitForCharts(monthly)

    await visualRegion(page, 'value-monthly-hours', theme, monthly)
    assertHermetic(api, errors)
  })
}
