/**
 * W16 AGT-09 (`docs/deferred-scope-2026-10-13/work/W16-contract.md` §2.1 test 7, §4.1, §4.2), hermetic, on
 * `/settings/ai-eval` (clock pinned at 2026-09-18 12:00 UTC):
 *
 *  - an ADMIN runs the agent-stack gate: PASS / FAIL / Not enough evidence per gate, the run joins the history, and
 *    its manifest opens inline;
 *  - an ADMIN evaluates a Rules candidate on the 12-item classification set: the world moves the run queued →
 *    running → completed across polls, Items opens, an Incorrect row's diff shows expected vs candidate as text;
 *  - that run is set as the baseline, the Gate tab gates Classification with it (PASS), then a second, worse run
 *    FAILs with `no_regression`;
 *  - a QA lead sees every Run / Evaluate / Set control disabled with `Needs an administrator.`, makes no ADMIN read,
 *    and the world records no write;
 *  - widths (§4.1, planned for CI's DejaVu fonts with ≥ 40 px slack): every new table's scroller and the six-tab
 *    strip at 1280 and 1440, printed as `INPAGE` lines, with the system fonts and with every font forced to Verdana
 *    (the DejaVu Sans width proxy: no DejaVu on a Windows host).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-ai-eval.ts` (G-A).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectMainFits, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  AI_EVAL_PATHS,
  DATASET_IDS,
  aiEvalWorld,
  completedReplay,
  createdRunId,
} from '../visual/production/fixtures-ai-eval'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const NEEDS_ADMIN = 'Needs an administrator.'
const GOOD_RUN = 'b0b0a1a0-0001-4abc-8def-a1a0b0b0c0de'

const tabBar = (p: Page) => p.getByRole('tablist', { name: 'AI evaluation' })
const tab = (p: Page, name: string) => tabBar(p).getByRole('tab', { name, exact: false })
const datasetRow = (p: Page, id: string) => p.locator(`[data-dataset-row="${id}"]`)
const runRow = (p: Page, id: string) => p.locator(`[data-run-row="${id}"]`)
const pageReady = (p: Page) => tabBar(p)

async function open(page: Page, path: string, world: ReturnType<typeof aiEvalWorld>) {
  return openRollout(page, path, { handlers: world.handlers, user: world.user, ready: pageReady })
}

/** A scroller's overflow and slack, and the tab strip's: printed, and asserted to fit. */
async function measureTables(page: Page, width: number, what: string, scrollers: string[]) {
  await expectMainFits(page, `${what} @${width}`)
  const numbers: Record<string, unknown> = {}
  for (const name of scrollers) {
    const scroller = page.locator(`[data-table-scroller="${name}"]`).first()
    await expect(scroller, `${name} is on the page`).toBeVisible()
    const overflow = await ownOverflow(scroller)
    expect(overflow, `${what}: the ${name} table scrolls sideways @${width}`).toBeLessThanOrEqual(0)
    // A fixed-layout table never grows with its text, so the fonts show inside the cells: a cell that is not
    // `truncate` (a chip, a number) must not spill out of its column, and no header may wrap to a second line.
    const cells = await scroller.evaluate((el: HTMLElement) => {
      const spill = Array.from(el.querySelectorAll<HTMLElement>('th, td'))
        .filter((c) => !c.classList.contains('truncate') && !c.querySelector('[data-item-diff], [data-manifest]'))
        .filter((c) => Array.from(c.children).some((k) => (k as HTMLElement).getBoundingClientRect().right > c.getBoundingClientRect().right + 1))
        .map((c) => c.textContent?.trim() ?? '')
      const heads = Array.from(el.querySelectorAll<HTMLElement>('thead th')).map((h) => h.getBoundingClientRect().height)
      const wrapped = heads.filter((h) => h > Math.min(...heads) + 4).length
      return { client: el.clientWidth, scroll: el.scrollWidth, spill, wrapped }
    })
    expect(cells.spill, `${what}: ${name} cells spill out of their column @${width}`).toEqual([])
    expect(cells.wrapped, `${what}: ${name} headers wrap @${width}`).toBe(0)
    numbers[name] = { client: cells.client, scroll: cells.scroll, slack: await minContentSlack(scroller) }
  }
  inPageLine(`/settings/ai-eval ${what}`, width, numbers)
  return numbers
}

/** The six tabs on ONE line, and the strip's slack (its width minus the tabs' extent). */
async function measureTabStrip(page: Page, width: number, what: string) {
  const strip = await tabBar(page).evaluate((bar: HTMLElement) => {
    const tabs = Array.from(bar.querySelectorAll<HTMLElement>('[role="tab"]')).map((t) => t.getBoundingClientRect())
    const left = Math.min(...tabs.map((r) => r.left))
    const right = Math.max(...tabs.map((r) => r.right))
    return {
      bar: Math.round(bar.getBoundingClientRect().width),
      tabs: Math.round(right - left),
      lines: new Set(tabs.map((r) => Math.round(r.top))).size,
      height: Math.round(bar.getBoundingClientRect().height),
    }
  })
  expect(strip.lines, `${what}: the six tabs stay on one line @${width}`).toBe(1)
  expect(strip.bar - strip.tabs, `${what}: the tab strip keeps 40 px of slack @${width}`).toBeGreaterThanOrEqual(40)
  inPageLine(`/settings/ai-eval ${what} tab-strip`, width, { ...strip, slack: strip.bar - strip.tabs })
}

/** Every font forced to Verdana (as wide as CI's DejaVu Sans, which this host lacks). */
async function verdana(page: Page) {
  const style = await page.addStyleTag({ content: '*, *::before, *::after { font-family: Verdana, sans-serif !important; }' })
  await page.evaluate(() => document.fonts.ready)
  return style
}

const gateRow = (scope: Locator, task: string) => scope.locator(`[data-gate-row="${task}"]`)

test('an admin runs the agent-stack gate: a verdict per gate, a history row, its manifest inline', async ({ page }) => {
  const world = aiEvalWorld({ role: 'admin' })
  const { api, errors } = await open(page, '/settings/ai-eval?tab=stack', world)
  await expect(tab(page, 'Agent stack')).toHaveAttribute('aria-selected', 'true')
  const stack = page.getByTestId('ai-eval-stack-tab')
  await expect(stack.getByLabel('Change id')).toHaveValue('manual-2026-09-18')
  await expect(stack.locator('[data-history-row]')).toHaveCount(1)

  await stack.getByRole('button', { name: 'Run gate' }).click()
  const result = stack.getByTestId('agent-stack-result')
  await expect(gateRow(result, 'classification').locator('[data-verdict]')).toHaveText('PASS')
  await expect(gateRow(result, 'root_cause').locator('[data-verdict]')).toHaveText('PASS')
  await expect(gateRow(result, 'duplicate_detection').locator('[data-verdict]')).toHaveText('FAIL')
  await expect(gateRow(result, 'duplicate_detection')).toContainText('DefectPromotionAgent')
  await expect(gateRow(result, 'release_decision').locator('[data-verdict]')).toHaveText('Not enough evidence')
  await expect(result).toContainText('Blocking: Duplicate Detection, Release Decision')

  // The run joined the history (newest first); its checksum opens the manifest inline.
  const rows = stack.locator('[data-history-row]')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toContainText('manual-2026-09-18')
  await rows.first().getByRole('button', { expanded: false }).click()
  const manifest = stack.locator('[data-manifest]')
  await expect(manifest).toContainText('Required gates')
  await expect(manifest).toContainText('Duplicate Detection · DefectPromotionAgent')

  await networkQuiet(page, api)
  expect(world.writes.map((w) => w.line)).toEqual([`POST ${AI_EVAL_PATHS.agentStackGate}`])
  expect(world.writes[0].body).toEqual({ change_id: 'manual-2026-09-18' })
  assertHermetic(api, errors)
})

test('an admin evaluates a Rules candidate: queued → running → completed, then an Incorrect item’s diff', async ({ page }) => {
  test.setTimeout(60_000)
  const world = aiEvalWorld({ role: 'admin' })
  const { api, errors } = await open(page, '/settings/ai-eval?tab=datasets', world)
  const row = datasetRow(page, DATASET_IDS.classification)
  await row.getByRole('button', { name: 'Evaluate candidate' }).click()
  const form = row.getByRole('region', { name: 'Evaluate candidate' })
  await expect(form.getByRole('radio', { name: 'Rules', exact: true })).toBeChecked()
  await expect(form.getByRole('radio', { name: /^ML/ })).toBeDisabled()
  await expect(form).toContainText('No ML model is trained yet; the ML tier would answer with the rules engine.')
  await expect(form.getByRole('radio', { name: 'Local model · ollama · llama3.1:8b-instruct' })).toBeEnabled()
  await expect(form.getByRole('spinbutton')).toHaveValue('12')
  await form.getByRole('button', { name: 'Start' }).click()
  await expect(form).toHaveCount(0)

  const run = runRow(page, createdRunId(1))
  await expect(run).toHaveAttribute('data-run-status', 'queued')
  await expect(run).toHaveAttribute('data-run-status', 'running', { timeout: 15_000 })
  await expect(run.locator('[data-run-progress]')).toContainText('of 12 items')
  await expect(run).toHaveAttribute('data-run-status', 'completed', { timeout: 15_000 })
  await expect(run).toContainText('Replay · Rules')
  await expect(run).toContainText(/\d+ scored · \d+ unscored · \d+ errors?/)

  await run.getByRole('button', { name: 'Items' }).click()
  const itemsView = run.locator('[data-run-items]')
  await itemsView.getByRole('button', { name: /^Incorrect \(\d+\)$/ }).click()
  const incorrect = itemsView.locator('tbody tr[data-item-row]').first()
  await expect(incorrect.locator('[data-item-status]')).toHaveText('Incorrect')
  await incorrect.click()
  const diff = itemsView.locator('[data-item-diff]')
  await expect(diff.locator('pre')).toHaveCount(3)
  await expect(diff.locator('pre').nth(0)).toContainText('"failure_category"')
  await expect(diff.locator('pre').nth(1)).toContainText('"failure_category"')

  await networkQuiet(page, api)
  expect(world.writes.map((w) => w.line)).toEqual([`POST /api/v1/ai-eval/runs/evaluate/${DATASET_IDS.classification}`])
  expect(world.writes[0].body).toEqual({ candidate: { tier: 'rules' }, project_id: PROJECT_ID, limit: 12 })
  // Polling stopped at the terminal status: no runs read after the last answer said completed.
  const polls = requestsTo(api, AI_EVAL_PATHS.runs).length
  await page.waitForTimeout(6_000)
  expect(requestsTo(api, AI_EVAL_PATHS.runs).length, 'the runs list stops polling once the run is completed').toBe(polls)
  assertHermetic(api, errors)
})

test('a completed run set as the baseline gates PASS; a second, worse run gates FAIL on no_regression', async ({ page }) => {
  test.setTimeout(60_000)
  const good = completedReplay(GOOD_RUN, 'classification', 'rules', 'good')
  const world = aiEvalWorld({ role: 'admin', runs: [good.run], items: { [GOOD_RUN]: good.items }, candidatePlans: ['bad'] })
  const { api, errors } = await open(page, '/settings/ai-eval?tab=datasets', world)

  // Set the good run as the Classification baseline (an inline confirm, then the POST).
  const row = datasetRow(page, DATASET_IDS.classification)
  await row.getByRole('button', { name: 'Runs' }).click()
  await runRow(page, GOOD_RUN).getByRole('button', { name: 'Set as baseline' }).click()
  const confirm = page.getByTestId('run-baseline-confirm')
  await expect(confirm).toContainText('Make this run the Classification baseline for AnalysisAgent?')
  await confirm.getByRole('button', { name: 'Set as baseline' }).click()
  await expect(runRow(page, GOOD_RUN).getByRole('status')).toHaveText('Baseline set for AnalysisAgent.')

  // The Gate tab: Classification, with that run as the candidate → PASS against itself.
  await tab(page, 'Gate').click()
  const gateTab = page.getByTestId('ai-eval-gate-tab')
  await gateTab.locator('#gate-candidate-classification').selectOption(GOOD_RUN)
  await gateTab.getByRole('button', { name: 'Gate: Classification' }).click()
  const card = gateTab.getByTestId('gate-result')
  await expect(card.locator('[data-verdict]')).toHaveText('PASS')
  await expect(card).toContainText('Replay')

  // A second, worse candidate on the same dataset, then the gate with it → FAIL, no_regression failed.
  await tab(page, 'Datasets').click()
  await datasetRow(page, DATASET_IDS.classification).getByRole('button', { name: 'Evaluate candidate' }).click()
  await datasetRow(page, DATASET_IDS.classification).getByRole('button', { name: 'Start' }).click()
  const bad = runRow(page, createdRunId(1))
  await expect(bad).toHaveAttribute('data-run-status', 'completed', { timeout: 25_000 })
  await tab(page, 'Gate').click()
  await gateTab.locator('#gate-candidate-classification').selectOption(createdRunId(1))
  await gateTab.getByRole('button', { name: 'Gate: Classification' }).click()
  await expect(card.locator('[data-verdict]')).toHaveText('FAIL')
  await expect(card.locator('[data-rule="no_regression"]')).toHaveAttribute('data-passed', 'false')

  await networkQuiet(page, api)
  expect(world.writes.map((w) => w.line)).toEqual([
    `POST ${AI_EVAL_PATHS.baselines}`,
    `POST ${AI_EVAL_PATHS.preReleaseGate}`,
    `POST /api/v1/ai-eval/runs/evaluate/${DATASET_IDS.classification}`,
    `POST ${AI_EVAL_PATHS.preReleaseGate}`,
  ])
  expect(world.writes[0].body).toEqual({ task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: GOOD_RUN })
  expect(world.writes[1].body).toMatchObject({ task_type: 'classification', agent_name: 'AnalysisAgent', candidate_run_id: GOOD_RUN })
  expect(world.writes[3].body).toMatchObject({ candidate_run_id: createdRunId(1) })
  assertHermetic(api, errors)
})

test('a QA lead reads every tab with every write disabled, no ADMIN read, and no write', async ({ page }) => {
  const good = completedReplay(GOOD_RUN, 'classification', 'rules', 'good')
  const world = aiEvalWorld({ role: 'qa_lead', runs: [good.run], items: { [GOOD_RUN]: good.items } })
  const { api, errors } = await open(page, '/settings/ai-eval?tab=datasets', world)
  const expectNeedsAdmin = async (control: Locator) => {
    await expect(control).toBeDisabled()
    await expect(control).toHaveAttribute('title', NEEDS_ADMIN)
  }
  await expectNeedsAdmin(page.getByRole('button', { name: 'Generate from Feedback' }))
  await expectNeedsAdmin(page.getByRole('button', { name: 'Seed Golden Datasets' }))
  const row = datasetRow(page, DATASET_IDS.classification)
  await expectNeedsAdmin(row.getByRole('button', { name: 'Run label agreement' }))
  await expectNeedsAdmin(row.getByRole('button', { name: 'Evaluate candidate' }))
  await row.getByRole('button', { name: 'Runs' }).click()
  for (const name of ['Items', 'Gate', 'Set as baseline']) await expectNeedsAdmin(runRow(page, GOOD_RUN).getByRole('button', { name }))

  await tab(page, 'Gate').click()
  for (const label of ['Classification', 'Root Cause', 'Duplicate Detection', 'Release Decision']) {
    await expectNeedsAdmin(page.getByRole('button', { name: `Gate: ${label}` }))
  }
  await tab(page, 'Agent stack').click()
  await expectNeedsAdmin(page.getByRole('button', { name: 'Run gate' }))
  await expect(page.locator('[data-history-row]')).toHaveCount(1)
  await tab(page, 'Reviewer').click()
  await expect(page.getByTestId('ai-eval-reviewer-tab')).toContainText("This page never changes the reviewer's configuration.")

  await networkQuiet(page, api)
  expect(requestsTo(api, AI_EVAL_PATHS.candidateOptions)).toEqual([])
  expect(requestsTo(api, /^\/api\/v1\/ai-eval\/runs\/[^/]+\/items$/)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test.describe(`widths at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`every new table and the six-tab strip fit the settings column, system fonts and Verdana — ${width}`, async ({ page }) => {
      test.setTimeout(60_000)
      const good = completedReplay(GOOD_RUN, 'classification', 'rules', 'bad')
      const world = aiEvalWorld({ role: 'admin', runs: [good.run], items: { [GOOD_RUN]: good.items } })
      const { api, errors } = await open(page, '/settings/ai-eval', world)

      /** Measure with the system fonts, then with every font Verdana, then put the fonts back. */
      const bothFonts = async (measure: (fonts: string) => Promise<unknown>) => {
        await measure('system')
        const style = await verdana(page)
        await measure('verdana')
        await style.evaluate((el) => el.remove())
      }

      await bothFonts((fonts) => measureTabStrip(page, width, fonts))
      await tab(page, 'Agent stack').click()
      await page.getByRole('button', { name: 'Run gate' }).click()
      await expect(page.getByTestId('agent-stack-result')).toBeVisible()
      await bothFonts((fonts) => measureTables(page, width, `${fonts} stack`, ['agent-stack-gates', 'agent-stack-history']))
      await tab(page, 'Datasets').click()
      await datasetRow(page, DATASET_IDS.classification).getByRole('button', { name: 'Runs' }).click()
      await runRow(page, GOOD_RUN).getByRole('button', { name: 'Items' }).click()
      await page.locator('tbody tr[data-item-row]').nth(1).click()
      await expect(page.locator('[data-item-diff]')).toBeVisible()
      await bothFonts((fonts) => measureTables(page, width, `${fonts} items`, ['run-items']))
      await tab(page, 'Reviewer').click()
      await bothFonts((fonts) => measureTables(page, width, `${fonts} reviewer`, ['reviewer-history']))
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.line)).toEqual([`POST ${AI_EVAL_PATHS.agentStackGate}`])
      assertHermetic(api, errors)
    })
  })
}
