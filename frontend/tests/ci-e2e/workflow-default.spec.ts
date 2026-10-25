/**
 * W14 AGT-08, hermetic (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.3 test 9):
 *
 *  - `/agents/workflows` as QA lead: a published fork → **Use for new offline runs** → ONE
 *    `PUT …/workflows/defaults/offline` with `{ workflow_id }` (unpinned), then the switch is ON and the
 *    model-stages cost is said beside it; OFF → ONE `DELETE`;
 *  - as QA engineer: no switch, nothing written;
 *  - `/agents?run=`: **Trigger pipeline** stays one click; the **Choose workflow** chevron opens the in-page
 *    picker (its reads only then), the fork is listed, Queue posts
 *    `{ test_run_id, workflow_id: 'wf.my-fork', workflow_version: 1 }`;
 *  - the Run page's ⋯ **Trigger pipeline** opens the same strip under the header and queues nothing by itself.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agent-ops.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY } from '../visual/production/fixtures-run'
import { FORK_ID, userWithRole, workflowDefaultWorld } from '../visual/production/fixtures-agent-ops'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const editorReady = (p: Page) => p.getByRole('heading', { level: 2, name: 'My fork' })
const theSwitch = (p: Page) => p.getByRole('switch', { name: 'Use for new offline runs' })
const added = (before: string[], after: string[]) => [...new Set(after)].filter((line) => !before.includes(line)).sort()

test('a QA lead makes a published fork the offline default, then clears it', async ({ page }) => {
  const world = workflowDefaultWorld({ role: 'QA_LEAD' })
  const { api, errors } = await openRollout(page, '/agents/workflows', { handlers: world.handlers, ready: editorReady })
  await expect(theSwitch(page)).toHaveAttribute('aria-checked', 'false')
  expect(world.writes).toEqual([])

  await theSwitch(page).click()
  await expect(theSwitch(page)).toHaveAttribute('aria-checked', 'true')
  await expect(page.locator('[data-model-stages]')).toHaveText(
    'Every new run calls a model in 3 stages: root_cause_analysis, summary, triage',
  )
  expect(world.writes).toEqual([
    { line: `PUT /api/v1/projects/${PROJECT_ID}/workflows/defaults/offline`, body: { workflow_id: FORK_ID } },
  ])

  await theSwitch(page).click()
  await expect(theSwitch(page)).toHaveAttribute('aria-checked', 'false')
  expect(world.writes.map((w) => w.line)).toEqual([
    `PUT /api/v1/projects/${PROJECT_ID}/workflows/defaults/offline`,
    `DELETE /api/v1/projects/${PROJECT_ID}/workflows/defaults/offline`,
  ])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA engineer sees no switch and writes nothing', async ({ page }) => {
  const world = workflowDefaultWorld({ role: 'QA_ENGINEER' })
  const { api, errors } = await openRollout(page, '/agents/workflows', {
    handlers: world.handlers, ready: editorReady, user: userWithRole('QA_ENGINEER'),
  })
  await networkQuiet(page, api)
  await expect(page.getByRole('switch')).toHaveCount(0)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('/agents: Trigger pipeline stays one click; Choose workflow opens the picker and queues the fork', async ({ page }) => {
  const world = workflowDefaultWorld()
  const ready = (p: Page) => p.getByRole('region', { name: 'AI report' }).getByText(AI_REPORT_SUMMARY)
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  const atLoad = observed(api)
  // The page's own selects (the shell has its own): ONE run picker until the picker opens.
  const pageSelects = page.locator('[data-page-header]').locator('xpath=..').locator('select')
  await expect(pageSelects).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Trigger pipeline' })).toHaveAttribute(
    'title', "Queue the project's default pipeline for the run picked here",
  )

  await page.getByRole('button', { name: 'Choose workflow' }).click()
  const strip = page.getByRole('region', { name: 'Trigger a pipeline' })
  await expect(strip).toBeVisible()
  // The second select exists only while the picker is open.
  await expect(pageSelects).toHaveCount(2)
  const workflow = strip.getByRole('combobox', { name: 'Workflow' })
  await expect(workflow.locator('option')).toHaveText([
    'Project default — built-in offline',
    'Built-in offline',
    // Built-in deep is not offered: this project's mode is Rules (the deep gate).
    `My fork · ${FORK_ID}@1`,
  ])
  await networkQuiet(page, api)
  expect(added(atLoad, observed(api))).toEqual([
    `GET /api/v1/projects/${PROJECT_ID}/workflows`,
    `GET /api/v1/projects/${PROJECT_ID}/workflows/defaults`,
  ])
  expect(world.writes).toEqual([])

  await workflow.selectOption({ label: `My fork · ${FORK_ID}@1` })
  await strip.getByRole('button', { name: 'Queue pipeline' }).click()
  await expect(page.getByText('Pipeline queued — it will appear in the list shortly.')).toBeVisible()
  await expect(strip).toHaveCount(0)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/agents/pipelines/trigger', body: { test_run_id: RUN_ID, workflow_id: FORK_ID, workflow_version: 1 } },
  ])
  assertHermetic(api, errors)
})

// §4.1: the toolbar gains ONE 24 px chevron; at 1280 and 1440 it stays one line with ≥ 40 px to spare
// (`fold-budget.spec.ts` budgets `/agents`' height: no load-time line may be added).
for (const width of [1280, 1440]) {
  test(`at ${width} px the /agents toolbar with the split button stays one line`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const world = workflowDefaultWorld()
    const ready = (p: Page) => p.getByRole('region', { name: 'AI report' }).getByText(AI_REPORT_SUMMARY)
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: world.handlers, ready })
    const geometry = await page.locator('[data-trigger-split]').evaluate((split) => {
      const row = split.parentElement as HTMLElement
      const kids = [...row.children] as HTMLElement[]
      const badge = kids[kids.length - 1].getBoundingClientRect()
      const chevron = (split.lastElementChild as HTMLElement).getBoundingClientRect()
      const boxes = kids.map((k) => k.getBoundingClientRect())
      return {
        // One line: every item overlaps every other vertically (items-center: the heights differ).
        oneLine: Math.max(...boxes.map((b) => b.top)) < Math.min(...boxes.map((b) => b.bottom)),
        tops: boxes.map((b) => Math.round(b.top)),
        slack: Math.round(badge.left - split.getBoundingClientRect().right),
        rowWidth: Math.round(row.getBoundingClientRect().width),
        chevron: Math.round(chevron.width),
      }
    })
    console.log(`TOOLBAR /agents@${width} ${JSON.stringify(geometry)}`)
    expect(geometry.oneLine, 'one line').toBe(true)
    expect(geometry.slack, 'px between the split button and the mode badge').toBeGreaterThanOrEqual(40)
    expect(geometry.chevron).toBe(24)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}

test('the Run page ⋯ Trigger pipeline opens the same strip under the header, queuing nothing by itself', async ({ page }) => {
  const world = workflowDefaultWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, {
    handlers: world.handlers, ready: (p) => p.locator('[data-primary] table'),
  })
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Trigger pipeline' }).click()
  const strip = page.getByRole('region', { name: 'Trigger a pipeline' })
  await expect(strip).toBeVisible()
  await expect(strip.getByRole('combobox', { name: 'Workflow' }).locator('option')).toHaveCount(3)
  // Under the header, above the page's tab content.
  const header = await page.locator('[data-page-header]').boundingBox()
  const box = await strip.boundingBox()
  expect((box?.y ?? 0)).toBeGreaterThan((header?.y ?? 0) + (header?.height ?? 0) - 1)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await strip.getByRole('button', { name: 'Cancel' }).click()
  await expect(strip).toHaveCount(0)
  assertHermetic(api, errors)
})
