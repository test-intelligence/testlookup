/**
 * W14 G-E baselines (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §6.5, five NEW regions), signal, lab and
 * paper (`THEMES_WITH_PAPER`):
 *
 *  - `/runs`: `runs-bulk-job-panel` (a "Re-trigger all matching" job held mid-way: the request line, the progress
 *    line, the started-of-queued gauge, Cancel / Open Pipeline runs / Close) and `runs-retrigger-confirm` (the
 *    in-page question naming the list's count);
 *  - `/deep-investigate`: `deep-run-configuration` (two signature groups selected, the tighten-only controls),
 *    `deep-estimate-card` (measured, a local model: `$0.00`, `~4 min`, the slowest stages, the budget line) and
 *    `deep-history-table` (the Investigated runs mode).
 *
 * The one CHANGED region of the wave on these pages, `deep-kpis` (the Clusters tile's caption "pre-scan · avg conf" →
 * "last analysis · avg conf"), is shot by the UNEDITED `prod-deep-investigation.visual.spec.ts`. Each region is the
 * in-page container itself, with focus blurred. Hermetic worlds: `production/fixtures-agent-ops-b.ts`. PNGs are
 * rendered by CI only (Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import { RUN_ID, runs } from './production/fixtures'
import { BULK_JOB_IDS, bulkJobWorld, deepConfigWorld, retriggerWorld } from './production/fixtures-agent-ops-b'

test.use(PINNED_TALL)

const runsReady = (p: Page) => p.locator('[data-status-banner]')
const verdict = (p: Page) => p.getByRole('region', { name: 'Investigation verdict' })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

for (const theme of THEMES_WITH_PAPER) {
  test(`the bulk-job panel mid-way, and the Re-trigger question — ${theme}`, async ({ page }) => {
    const tracked = retriggerWorld()
    const { api, errors } = await openRollout(page, `/runs?bulk_job=${BULK_JOB_IDS[0]}`, { theme, handlers: tracked.handlers, ready: runsReady })
    const panel = page.getByRole('region', { name: 'Bulk AI analysis' })
    await expect(panel).toContainText('2 completed · 1 passed · 1 failed · 2 running · 1 not started')
    await expect(panel.getByRole('meter', { name: 'Pipelines started of those queued' })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'runs-bulk-job-panel', theme, panel)
    assertHermetic(api, errors)
  })

  test(`the "Re-trigger all matching" question above the table — ${theme}`, async ({ page }) => {
    const world = bulkJobWorld()
    const { api, errors } = await openRollout(page, '/runs', { theme, handlers: world.handlers, ready: runsReady })
    await page.getByRole('button', { name: 'More actions' }).click()
    await page.getByRole('menuitem', { name: `Re-trigger all matching (${runs().length})` }).click()
    const confirm = page.getByTestId('runs-retrigger-confirm')
    await expect(confirm).toContainText(`Re-trigger the AI pipeline for ${runs().length} runs matching this filter?`)
    await blur(page)
    await visualRegion(page, 'runs-retrigger-confirm', theme, confirm)
    expect(world.state.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`the run configuration, the measured estimate and the investigated runs — ${theme}`, async ({ page }) => {
    const world = deepConfigWorld()
    const { api, errors } = await openRollout(page, `/deep-investigate?run=${RUN_ID}`, { theme, handlers: world.handlers, ready: verdict })
    const config = page.getByTestId('deep-run-configuration')
    await config.getByRole('checkbox', { name: /^Select 7 failures/ }).check()
    await config.getByRole('checkbox', { name: /^Select 5 failures/ }).check()
    await expect(config).toContainText('2 groups · 12 failures selected')
    await blur(page)
    await visualRegion(page, 'deep-run-configuration', theme, config)

    const estimate = page.locator('[data-deep-estimate]')
    await expect(estimate.locator('[data-estimate-minutes]')).toHaveText('~4 min')
    await expect(estimate).toContainText('local model — no charge')
    await visualRegion(page, 'deep-estimate-card', theme, estimate)

    await page.getByRole('button', { name: 'Investigated runs · 4' }).click()
    const history = page.getByTestId('deep-history-table')
    await expect(history.getByRole('heading', { level: 3, name: /^Investigated runs/ })).toBeVisible()
    await expect(history.locator('tbody tr')).toHaveCount(4)
    await blur(page)
    await visualRegion(page, 'deep-history-table', theme, history)
    expect(world.state.writes).toEqual([])
    assertHermetic(api, errors)
  })
}
