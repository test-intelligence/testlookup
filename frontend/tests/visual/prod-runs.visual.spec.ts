/**
 * BEFORE/AFTER baselines of /runs (Wave 2.5, VIZ-104): the pipeline
 * verdict with its health meter (G3), the five KPI slots and their glyphs,
 * and the 14-cell build-velocity strip (S5: red and green builds and one
 * empty cell, since the window holds 13 builds).
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
import { NOW, PROJECT_ID, RUNS_PAGE, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`runs regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/runs', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RUNS_PAGE,
      ready: (p) => landmark(p, 'Run KPIs'),
    })

    const verdict = landmark(page, 'Pipeline verdict')
    const kpis = landmark(page, 'Run KPIs')
    const velocity = cardByHeading(page, /^Build velocity/)
    const strip = velocity.getByRole('img', { name: /^Build velocity over the last 14 builds/ })
    await expect(strip).toHaveAttribute('aria-label', /\d+ passed, \d+ failed, 1 no-build cells/)

    await visualRegion(page, 'runs-verdict', theme, verdict)
    await visualRegion(page, 'runs-kpis', theme, kpis)
    await visualRegion(page, 'runs-build-velocity', theme, velocity)
    assertHermetic(api, errors)
  })
}
