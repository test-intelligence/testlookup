/**
 * BEFORE/AFTER baselines of /coverage (Wave 2.5, VIZ-104): the coverage
 * verdict with its gradient health fill (G4) and the 5-level run-cadence
 * intensity strip (S2), 30 days with a 7-day gap.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { COVERAGE, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`coverage regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/coverage', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: COVERAGE,
      ready: (p) => landmark(p, 'Run cadence'),
    })

    const cadence = landmark(page, 'Run cadence')
    await expect(cadence.getByRole('img').first()).toBeVisible()

    await visualRegion(page, 'coverage-verdict', theme, landmark(page, 'Coverage verdict'))
    await visualRegion(page, 'coverage-cadence', theme, cadence)
    assertHermetic(api, errors)
  })
}
