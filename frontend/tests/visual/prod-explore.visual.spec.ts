/**
 * AFTER baseline of `/explore` (W08 TRND-13): the Explorer split into small
 * multiples by BRANCH, one of the five dimensions `chart-data` filters with
 * `facet_dimension` + `facet_value` — the fixture's six branches (the
 * `(none)` bucket is left out, and said), failure rate per week, one line per
 * environment, on the shared 0–100 % scale.
 *
 * Lands WITH its PNGs (`visual-baselines.yml`, Linux only). Harness and
 * fail-closed rules: `tests/lib/production-pages.ts`; fixtures:
 * `fixtures-trends-coverage.ts` (`EXPLORE`).
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectNoErrorFrame, openRollout } from '../lib/rollout'
import { EXPLORE, EXPLORE_BRANCHES } from './production/fixtures-trends-coverage'

test.use(PINNED)

for (const theme of THEMES) {
  test(`explore regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/explore?facet=branch', {
      theme,
      handlers: EXPLORE,
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Explorer' }),
    })
    const grid = page.locator('[data-explore-grid]')
    await expect(grid.locator('[data-explore-panel]')).toHaveCount(EXPLORE_BRANCHES.length)
    await expect(grid.locator('[data-chart-state="ready"]')).toHaveCount(EXPLORE_BRANCHES.length, { timeout: 20_000 })
    await waitForCharts(grid, EXPLORE_BRANCHES.length)
    await expectNoErrorFrame(page)
    await visualRegion(page, 'explore-branch-panels', theme, grid)
    assertHermetic(api, errors)
  })
}
