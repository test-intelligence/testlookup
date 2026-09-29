/**
 * BEFORE/AFTER baselines of /defects (Wave 2.5, VIZ-104): the queue
 * verdict with its health meter (G2) and the five KPI slots, four of which
 * draw hard-coded glyphs today; "Oldest open P0" is a real 4-day bar.
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
    assertHermetic(api, errors)
  })
}
