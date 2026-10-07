/**
 * Baselines of /runs (Wave 2.5, VIZ-104; UX redesign P3): the one-line
 * verdict banner above the table, the pipeline-health gauge with its four
 * dimensions (G3; "How this verdict is computed", a Disclosure below the
 * table since P3), and the 14-cell build-velocity strip (S5: red and green
 * builds and one empty cell, since the window holds 13 builds; "Build
 * history", a Disclosure since P3).
 *
 * P3 dropped two regions: `runs-verdict` (the verdict card: its line is the
 * banner, its gauge `runs-health`) and `runs-kpis` (the KPI strip repeated the
 * banner; its pass-rate line is in Build history). `runs-banner` and
 * `runs-health` are new: their baselines come from CI.
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
      ready: (p) => p.locator('[data-status-banner]'),
    })

    const banner = page.locator('[data-status-banner]')
    await expect(banner).toContainText('Failing builds 6 of 13')

    // The two Disclosures below the table, opened for their regions.
    await page.getByRole('button', { name: /^How this verdict is computed/ }).click()
    const health = page.locator('[data-disclosure]').filter({ has: page.getByRole('meter', { name: 'Pipeline health' }) })
    await page.getByRole('button', { name: /^Build history/ }).click()
    const velocity = cardByHeading(page, /^Build velocity/)
    const strip = velocity.getByRole('img', { name: /^Build velocity over the last 14 builds/ })
    await expect(strip).toHaveAttribute('aria-label', /\d+ passed, \d+ failed, 1 no-build cells/)

    await visualRegion(page, 'runs-banner', theme, banner)
    await visualRegion(page, 'runs-health', theme, health)
    await visualRegion(page, 'runs-build-velocity', theme, velocity)
    assertHermetic(api, errors)
  })
}
