/**
 * W16 G-E baselines (`docs/deferred-scope-2026-10-13/work/W16-contract.md` §6.5, six NEW regions), signal, lab and
 * paper (`THEMES_WITH_PAPER`), all on `/settings/ai-eval`:
 *
 *  - `ai-eval-tabs`: the six-tab bar on Overview with the Recent Evaluation Runs' `Legacy label agreement` /
 *    `Replay · Rules` chips (the page column: header, tabs and the Overview panels);
 *  - `ai-eval-agent-stack`: a FAIL agent-stack result (a row per gate) and the history with one manifest open;
 *  - `ai-eval-candidate-run`: a dataset row with a completed replay run and its Set-as-baseline confirm open;
 *  - `ai-eval-run-items`: the run's items table with one expected-vs-candidate diff open;
 *  - `ai-eval-gate-result`: the Gate tab with a candidate run selected and a FAIL result (`no_regression` failed);
 *  - `ai-eval-reviewer`: the G3 reviewer-quality cards and history (read-only).
 *
 * No existing region changes (no region shot `/settings/ai-eval` before W16). Each region is the container itself,
 * focus blurred. Hermetic world: `production/fixtures-ai-eval.ts` (G-A). PNGs are rendered by CI only (Linux);
 * nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import {
  AI_EVAL_PATHS,
  DATASET_IDS,
  aiEvalWorld,
  baselineRow,
  completedReplay,
  legacyRuns,
} from './production/fixtures-ai-eval'

test.use(PINNED)

const GOOD_RUN = 'b0b0a1a0-0001-4abc-8def-a1a0b0b0c0de'
const BAD_RUN = 'b0b0a1a0-0002-4abc-8def-a1a0b0b0bad0'

const tabBar = (p: Page) => p.getByRole('tablist', { name: 'AI evaluation' })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const runRow = (p: Page, id: string) => p.locator(`[data-run-row="${id}"]`)

/** A world with two legacy runs, a good and a bad Rules replay, and the good one as the replay baseline. */
function replayWorld() {
  const good = completedReplay(GOOD_RUN, 'classification', 'rules', 'good')
  const bad = completedReplay(BAD_RUN, 'classification', 'rules', 'bad')
  return aiEvalWorld({
    role: 'admin',
    runs: [...legacyRuns(), good.run, bad.run],
    items: { [GOOD_RUN]: good.items, [BAD_RUN]: bad.items },
    baselines: [
      baselineRow({ is_active: false }),
      baselineRow({
        id: 'bae11e9a-0002-4abc-8def-ba5e1e9a9e01', prompt_version: 'replay-rules', model_name: null,
        metric_kind: 'replay', eval_run_id: GOOD_RUN, baseline_accuracy: good.run.accuracy, baseline_f1: good.run.f1_score,
      }),
    ],
  })
}

async function open(page: Page, path: string, world: ReturnType<typeof aiEvalWorld>, theme: (typeof THEMES_WITH_PAPER)[number]) {
  return openRollout(page, path, { theme, handlers: world.handlers, user: world.user, ready: tabBar })
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the six tabs and the Recent runs' metric-kind chips — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval', world, theme)
    await expect(page.locator('[data-eval-run-row] [data-metric-kind="replay"]').first()).toHaveText('Replay · Rules')
    await expect(page.locator('[data-eval-run-row] [data-metric-kind="label_agreement"]').first()).toHaveText('Legacy label agreement')
    await blur(page)
    await visualRegion(page, 'ai-eval-tabs', theme, tabBar(page).locator('xpath=..'))
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`a FAIL agent-stack result and the history with a manifest open — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval?tab=stack', world, theme)
    const stack = page.getByTestId('ai-eval-stack-tab')
    await stack.getByRole('button', { name: 'Run gate' }).click()
    await expect(stack.getByTestId('agent-stack-result').locator('[data-gate-row]')).toHaveCount(4)
    await stack.locator('[data-history-row]').last().getByRole('button', { expanded: false }).click()
    await expect(stack.locator('[data-manifest]')).toContainText('Required gates')
    await blur(page)
    await visualRegion(page, 'ai-eval-agent-stack', theme, stack)
    expect(world.writes.map((w) => w.line)).toEqual([`POST ${AI_EVAL_PATHS.agentStackGate}`])
    assertHermetic(api, errors)
  })

  test(`a dataset row with a completed replay and its Set-as-baseline confirm — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval?tab=datasets', world, theme)
    const row = page.locator(`[data-dataset-row="${DATASET_IDS.classification}"]`)
    await row.getByRole('button', { name: 'Runs' }).click()
    await runRow(page, GOOD_RUN).getByRole('button', { name: 'Set as baseline' }).click()
    await expect(page.getByTestId('run-baseline-confirm')).toBeVisible()
    await blur(page)
    await visualRegion(page, 'ai-eval-candidate-run', theme, row)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`a replay's items with one diff open — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval?tab=datasets', world, theme)
    await page.locator(`[data-dataset-row="${DATASET_IDS.classification}"]`).getByRole('button', { name: 'Runs' }).click()
    await runRow(page, BAD_RUN).getByRole('button', { name: 'Items' }).click()
    const items = page.locator(`[data-run-items="${BAD_RUN}"]`)
    await items.locator('tbody tr[data-item-row="1"]').click()
    await expect(items.locator('[data-item-diff="1"]')).toBeVisible()
    await blur(page)
    await visualRegion(page, 'ai-eval-run-items', theme, items)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`the Gate tab: a candidate run selected, FAIL on no_regression — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval?tab=gate', world, theme)
    const gateTab = page.getByTestId('ai-eval-gate-tab')
    await gateTab.locator('#gate-candidate-classification').selectOption(BAD_RUN)
    await gateTab.getByRole('button', { name: 'Gate: Classification' }).click()
    const card = gateTab.getByTestId('gate-result')
    await expect(card.locator('[data-verdict]')).toHaveText('FAIL')
    await expect(card.locator('[data-rule="no_regression"]')).toHaveAttribute('data-passed', 'false')
    await blur(page)
    await visualRegion(page, 'ai-eval-gate-result', theme, gateTab)
    expect(world.writes.map((w) => w.line)).toEqual([`POST ${AI_EVAL_PATHS.preReleaseGate}`])
    assertHermetic(api, errors)
  })

  test(`the reviewer-quality cards and history — ${theme}`, async ({ page }) => {
    const world = replayWorld()
    const { api, errors } = await open(page, '/settings/ai-eval?tab=reviewer', world, theme)
    const reviewer = page.getByTestId('ai-eval-reviewer-tab')
    await expect(reviewer.locator('[data-reviewer-row]')).toHaveCount(2)
    await blur(page)
    await visualRegion(page, 'ai-eval-reviewer', theme, reviewer)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })
}
