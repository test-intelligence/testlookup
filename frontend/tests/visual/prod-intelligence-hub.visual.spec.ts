/**
 * BEFORE/AFTER baselines of /intelligence (Wave 2.5, VIZ-104): the "Recent
 * runs analyzed" table with its 56 px pass-rate meters (G8, one run in each
 * tone band) and the "Intelligence spend" panel with its monthly-budget bar
 * (a hard-coded teal gradient today).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
} from '../lib/production-pages'
import { INTELLIGENCE_HUB, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`intelligence hub regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/intelligence', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: INTELLIGENCE_HUB,
      ready: (p) => p.getByRole('heading', { name: 'Recent runs analyzed' }),
    })

    const runsTable = cardByHeading(page, 'Recent runs analyzed')
    await expect(runsTable.locator('tbody tr')).toHaveCount(6)
    const spend = cardByHeading(page, 'Intelligence spend')
    await expect(spend).toContainText('$18.42 of $50')

    await visualRegion(page, 'hub-runs-meter', theme, runsTable)
    await visualRegion(page, 'hub-utilisation', theme, spend)
    assertHermetic(api, errors)
  })
}
