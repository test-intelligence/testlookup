/**
 * W22a ACT-04 on `/runs/:id?tab=changes`, hermetic (`docs/deferred-scope-2026-10-13/work/W22a-contract.md` §4.1,
 * §6.3), at 1440 and 1280: when the retention purge removed the run's stored baseline, the Changes card says
 * `Baseline build 4471 was purged on Sep 15, 2026.` —
 *
 *  - baseline found (a surviving older PASSED run was chosen live): the line sits right under `Baseline: Build #4470`;
 *  - no baseline: the line sits right under `No recent passing baseline run found for comparison.`;
 *  - `baseline_purged: null`: no line.
 *
 * Opening the tab asks nothing new (the purged fact rides the regression diff). Harness: `tests/lib/rollout.ts`
 * (fail closed, pinned clock: NOW = 2026-09-18, the purge three days before). World: `fixtures-retention.ts`
 * `retentionHoldsWorld({ base: RUN_PAGE, regressionDiff })`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import {
  retentionHoldsWorld,
  worldRegressionDiff,
  worldRegressionDiffNoBaseline,
} from '../visual/production/fixtures-retention'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const PURGED_LINE = 'Baseline build 4471 was purged on Sep 15, 2026.'
const card = (page: Page) => page.getByRole('region', { name: 'What changed since the last good run' })
const purged = (page: Page) => card(page).locator('[data-baseline-purged]')

for (const [width, height] of [[1440, 900], [1280, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`baseline found: the purged line under the baseline line — ${width}`, async ({ page }) => {
      const world = retentionHoldsWorld({ base: RUN_PAGE, regressionDiff: (runId) => worldRegressionDiff(runId) })
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
        handlers: world.handlers,
        ready: (p) => card(p).getByText('Baseline: Build #4470'),
      })
      await expect(purged(page)).toHaveText(PURGED_LINE)
      const baseline = card(page).getByText('Baseline: Build #4470')
      expect(await baseline.evaluate((el) => el.nextElementSibling?.hasAttribute('data-baseline-purged') ?? false)).toBe(true)
      await expectMainFits(page, `/runs/:id?tab=changes purged @${width}`)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`no baseline: the purged line under "No recent passing baseline…" — ${width}`, async ({ page }) => {
      const world = retentionHoldsWorld({ base: RUN_PAGE, regressionDiff: (runId) => worldRegressionDiffNoBaseline(runId) })
      const noBaseline = 'No recent passing baseline run found for comparison.'
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
        handlers: world.handlers,
        ready: (p) => card(p).getByText(noBaseline),
      })
      await expect(purged(page)).toHaveText(PURGED_LINE)
      const line = card(page).getByText(noBaseline)
      expect(await line.evaluate((el) => el.nextElementSibling?.hasAttribute('data-baseline-purged') ?? false)).toBe(true)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`nothing purged: no line — ${width}`, async ({ page }) => {
      const world = retentionHoldsWorld({ base: RUN_PAGE, regressionDiff: (runId) => worldRegressionDiff(runId, { baseline_purged: null }) })
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
        handlers: world.handlers,
        ready: (p) => card(p).getByText('Baseline: Build #4470'),
      })
      await networkQuiet(page, api)
      await expect(purged(page)).toHaveCount(0)
      assertHermetic(api, errors)
    })
  })
}
