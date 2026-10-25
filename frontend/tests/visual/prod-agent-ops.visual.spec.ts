/**
 * W14 G-D baselines (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §6.5 "Visual regions"), in signal,
 * lab and paper:
 *
 *  - `agents-audit-tab`               an old pipeline's Audit: the gaps panel and the stage table;
 *  - `agents-compare-tab`             a rerun pair, one changed stage;
 *  - `agents-generic-graph`           an investigation's fan-out: plan → five hypotheses → synthesis;
 *  - `agents-workflow-picker`         the open picker strip with a fork listed;
 *  - `workflow-editor-default-switch` the editor's facts row with the switch ON and the model-stages warning.
 *
 * Hermetic fixtures: `fixtures-agent-ops.ts`. Focus is blurred before each region (a focus ring is not part of
 * the picture). PNGs are rendered by CI only (`visual-baselines.yml`, Linux); locally this spec runs with
 * `--update-snapshots=none` only, to prove it reaches every region.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { AI_REPORT_SUMMARY } from './production/fixtures-run'
import {
  auditWorld,
  compareWorld,
  FORK_ID,
  investigationGraphWorld,
  workflowDefaultWorld,
} from './production/fixtures-agent-ops'

test.use(PINNED)

const report = (p: Page) => p.getByRole('region', { name: 'AI report' })
const ready = (p: Page) => report(p).getByText(AI_REPORT_SUMMARY)
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

async function openStages(page: Page) {
  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  await expect(page.getByRole('tablist', { name: 'Pipeline views' })).toBeVisible()
}

for (const theme of THEMES_WITH_PAPER) {
  test(`agents audit tab — ${theme}`, async ({ page }) => {
    const { handlers } = auditWorld({ replayable: false })
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}&ptab=audit`, { theme, handlers, ready })
    await openStages(page)
    const view = page.locator('[data-audit-view]')
    await expect(view.getByRole('region', { name: 'Audit gaps' })).toBeVisible()
    await expect(view.locator('[data-audit-stages] tbody tr')).toHaveCount(5)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-audit-tab', theme, view)
    assertHermetic(api, errors)
  })

  test(`agents compare tab — ${theme}`, async ({ page }) => {
    const { handlers } = compareWorld()
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}&ptab=compare`, { theme, handlers, ready })
    await openStages(page)
    const view = page.locator('[data-compare-view]')
    await expect(view.locator('[data-compare-output]')).toHaveText('Output changed in 1 stage')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-compare-tab', theme, view)
    assertHermetic(api, errors)
  })

  test(`agents generic graph — ${theme}`, async ({ page }) => {
    const { handlers } = investigationGraphWorld()
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { theme, handlers, ready })
    await openStages(page)
    const graph = page.locator('[data-generic-graph]')
    await expect(graph.locator('[data-stage-id]')).toHaveCount(7)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-generic-graph', theme, graph)
    assertHermetic(api, errors)
  })

  test(`agents workflow picker — ${theme}`, async ({ page }) => {
    const { handlers } = workflowDefaultWorld()
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { theme, handlers, ready })
    await page.getByRole('button', { name: 'Choose workflow' }).click()
    const strip = page.locator('[data-workflow-picker]')
    await expect(strip.getByRole('combobox', { name: 'Workflow' }).locator('option')).toHaveText([
      'Project default — built-in offline', 'Built-in offline', `My fork · ${FORK_ID}@1`,
    ])
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-workflow-picker', theme, strip)
    assertHermetic(api, errors)
  })

  test(`workflow editor default switch — ${theme}`, async ({ page }) => {
    const { handlers } = workflowDefaultWorld()
    const { api, errors } = await openRollout(page, '/agents/workflows', {
      theme, handlers, ready: (p) => p.getByRole('heading', { level: 2, name: 'My fork' }),
    })
    const toggle = page.getByRole('switch', { name: 'Use for new offline runs' })
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    await expect(page.locator('[data-model-stages]')).toBeVisible()
    const facts = page.locator('[data-default-switch]').locator('xpath=..')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'workflow-editor-default-switch', theme, facts)
    assertHermetic(api, errors)
  })
}
