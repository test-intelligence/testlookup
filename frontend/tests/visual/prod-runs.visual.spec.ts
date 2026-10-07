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
 *
 * P4 (D2, the `/intelligence` list retired into the table): `runs-table`, the
 * runs table with its AI-verdict column (a No-Go and a Conditional awaiting
 * review, a reviewed Go, "—" for the rest; captured once every verdict has
 * arrived), new: its baseline comes from CI. The banner's title carries the
 * failing count since the P3 baseline review ("6 of 13 builds failed"; the
 * "Failing builds" fact that repeated it is gone).
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
    // The failing count is the title's, once (no "Failing builds" fact repeating it).
    await expect(banner).toContainText('6 of 13 builds failed')
    await expect(banner).not.toContainText('Failing builds')

    // The table, once every run's AI verdict has arrived (no skeleton left).
    const table = page.locator('[data-primary]')
    await expect(table.locator('[data-ai-verdict="loading"]')).toHaveCount(0)
    await expect(table.locator('[data-ai-verdict="NO_GO"]')).toHaveText(/No-Go\s*draft/)
    await visualRegion(page, 'runs-table', theme, table)

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
