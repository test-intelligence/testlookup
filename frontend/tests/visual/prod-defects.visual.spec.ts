/**
 * BEFORE/AFTER baselines of /defects (Wave 2.5, VIZ-104): the queue
 * verdict with its health meter (G2) and the five KPI slots, four of which
 * draw hard-coded glyphs today; "Oldest open P0" is a real 4-day bar.
 * UX redesign P3 (page template), same region names, new contents:
 * `defects-verdict` is the one-line StatusBanner (the "Defect queue verdict"
 * landmark); `defects-kpis` is the KpiStrip of five compact tiles (the oldest
 * P0's bar beside its value). NEW region `defects-health`: the queue meter
 * (G2) and its three dimensions left the verdict for the "How queue health is
 * computed" Disclosure below the table, captured open.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { DEFECTS, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`defects regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/defects', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: DEFECTS,
      ready: (p) => landmark(p, 'Defect KPIs'),
    })

    const kpis = landmark(page, 'Defect KPIs')
    await expect(kpis).toContainText('Oldest open P0')

    await visualRegion(page, 'defects-verdict', theme, landmark(page, 'Defect queue verdict'))
    await visualRegion(page, 'defects-kpis', theme, kpis)

    const health = page.locator('[data-disclosure]').filter({ has: page.getByRole('button', { name: /^How queue health is computed/ }) })
    await health.getByRole('button', { name: /^How queue health is computed/ }).click()
    await expect(health.getByRole('meter', { name: 'Queue health' })).toBeVisible()
    await visualRegion(page, 'defects-health', theme, health)
    assertHermetic(api, errors)
  })
}
