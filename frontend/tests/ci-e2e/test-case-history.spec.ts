/**
 * W28 TCM-04 "compare and restore versions" and TCM-05 "a recorded execution remembers the version it ran"
 * (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §2.4, §2.5, §4.4), hermetic, lifecycle v2 on, 1440 × 900:
 *
 *  - an AI-generated Active case with three versions (v1 by the AI generator, v2 by `Priya Shah`, v3 by an API key) →
 *    History: newest first; each card's `by <name>`, its `AI` / `API` chip, its summary and What changed (v3: Priority
 *    and Tags before → after; v2: `Step 2 action`, `Step 3 added`, the expected result stacked; v1, the oldest, its
 *    changed fields only); Compare with current on v1 opens the block IN its card (v1 → current, step by step); Restore
 *    this version → the question IN the card (`Restore v1 as a new version? The status stays Active.`) → Restore →
 *    exactly `POST …/cases/{id}/restore` `{ version: 1, expected_version: 3 }` → `Restored v1 as v4`, v4 `restored` at
 *    the top, the pane on v4 with v1's steps, the status unchanged; Cancel sends nothing; no Restore on the current
 *    version;
 *  - Plans › a plan: the `Ran against` column between Status and Actions (Status stays the third cell): `v1 · case now
 *    v2` for the item executed at v1 of a case now at v2, `—` for one never run, `not recorded` for one run before
 *    versions were recorded; `v1` opens, under its row (one cell spanning the five columns), the steps v1 had — read
 *    from the case history only then — not the case's current steps; the case's History v1 lists the execution.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testCaseEditingWorld()`
 * (`fixtures-test-case-editing.ts`, over `testLibraryWorld()`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { USER } from '../visual/production/fixtures'
import { TM_PLAN_NAME, tmCaseId, tmCaseTitle } from '../visual/production/fixtures-in-page-b'
import {
  ACTIVE_CASE,
  API_EDITOR,
  CASE_STEPS,
  HISTORY_CASE,
  OTHER_EDITOR,
  RAN_STEPS,
  NOW_STEPS,
  testCaseEditingWorld,
  type TestCaseEditingOptions,
} from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const CASES = '/api/v1/test-management/cases'
const V2 = { test_case_lifecycle_v2: true }
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')
const card = (p: Page, n: number) => pane(p).locator(`[data-history-version="${n}"]`)

async function open(page: Page, path: string, options: TestCaseEditingOptions = {}) {
  const world = testCaseEditingWorld({ lifecycleV2: true, ...options })
  const opened_ = await openRollout(page, path, { handlers: world.handlers, ready: tmReady, user: USER, flags: V2 })
  return { world, ...opened_ }
}

test('TCM-04 History: who, the source chips, what changed per version; Compare with current; Restore v1 → ONE restore POST → v4 at the top', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, '/test-management')
  await caseRow(page, HISTORY_CASE).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(HISTORY_CASE) })).toBeVisible()
  await networkQuiet(page, api)
  const HISTORY = `${CASES}/${tmCaseId(HISTORY_CASE)}/history`
  expect(requestsTo(api, HISTORY), 'the history is read only with its tab').toEqual([])
  await pane(page).getByRole('button', { name: 'History', exact: true }).click()

  const cards = pane(page).locator('[data-history-version]')
  await expect(cards).toHaveCount(3)
  expect(await cards.evaluateAll((els) => els.map((el) => el.getAttribute('data-history-version'))), 'newest first').toEqual(['3', '2', '1'])

  // v3: the API key's edit.
  await expect(card(page, 3)).toContainText('v3 — updated')
  await expect(card(page, 3)).toContainText(`by ${API_EDITOR.name}`)
  await expect(card(page, 3).locator('[data-history-source]')).toHaveText('API')
  await expect(card(page, 3)).toContainText('Tagged by the nightly sync')
  await expect(card(page, 3).getByTestId('history-changes-v3').locator('li')).toHaveText(['Priority: critical → high', 'Tags: cart → cart, regression'])
  await expect(card(page, 3).getByRole('button', { name: 'Restore this version' }), 'no Restore on the current version').toHaveCount(0)
  await expect(card(page, 3).getByRole('button', { name: 'Compare with current' }), 'nor a compare').toHaveCount(0)

  // v2: Priya's steps.
  await expect(card(page, 2)).toContainText(`by ${OTHER_EDITOR.name}`)
  await expect(card(page, 2).locator('[data-history-source]'), 'a person has no chip').toHaveCount(0)
  const v2 = card(page, 2).getByTestId('history-changes-v2').locator('li')
  await expect(v2).toHaveText([
    'Step 2 action: Refresh the page → Refresh the page twice',
    'Step 3 added: Sign out and back in',
    'Expected result:The cart is unchanged after a refreshThe cart is unchanged after a refresh and a re-login',
  ])
  await expect(v2.nth(2).locator('[data-diff-before]'), 'the long text stacked: before').toHaveText('The cart is unchanged after a refresh')

  // v1: the AI generator's first version, its changed fields only.
  await expect(card(page, 1).locator('[data-history-source]')).toHaveText('AI')
  await expect(card(page, 1)).toContainText('Changed: Title, Steps, Expected result')
  await expect(card(page, 1).locator('[data-history-changes]'), 'no predecessor: no What changed').toHaveCount(0)

  // Compare v1 with the current case, in its card.
  await card(page, 1).getByRole('button', { name: 'Compare with current' }).click()
  const compare = card(page, 1).locator('[data-history-compare]')
  await expect(compare).toContainText('v1 → current (v3)')
  await expect(compare.getByTestId('history-compare-v1').locator('li')).toHaveText([
    'Step 2 action: Refresh the page → Refresh the page twice',
    'Step 3 added: Sign out and back in',
    'Expected result:The cart is unchanged after a refreshThe cart is unchanged after a refresh and a re-login',
    'Priority: critical → high',
    'Tags: cart → cart, regression',
  ])
  await expectNoOverlay(page)

  // Restore v1: asked in its card; Cancel sends nothing; Restore sends one POST.
  await card(page, 1).getByRole('button', { name: 'Restore this version' }).click()
  let ask = card(page, 1).locator('[data-inline-confirm]')
  await expect(ask).toContainText('Restore v1 as a new version? The status stays Active.')
  await ask.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(ask).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await card(page, 1).getByRole('button', { name: 'Restore this version' }).click()
  ask = card(page, 1).locator('[data-inline-confirm]')
  await ask.locator('[data-inline-confirm-action]').click()
  await expect(page.getByText('Restored v1 as v4', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${CASES}/${tmCaseId(HISTORY_CASE)}/restore`, body: { version: 1, expected_version: 3 } }])
  await expect(cards.first(), 'v4 at the top').toHaveAttribute('data-history-version', '4')
  await expect(card(page, 4)).toContainText('v4 — restored')
  await expect(card(page, 4)).toContainText('Restored from v1')
  await expect(card(page, 4)).toContainText('by QA Lead')
  expect(world.cases[HISTORY_CASE].steps, 'v1\'s steps are back').toEqual(CASE_STEPS)
  expect(world.cases[HISTORY_CASE].status, 'the status stays').toBe('active')
  await pane(page).getByRole('button', { name: 'Details', exact: true }).click()
  await expect(pane(page).getByText('Refresh the page', { exact: true }), 'the pane shows v4\'s steps').toBeVisible()
  await expect(pane(page).getByText('Sign out and back in', { exact: true })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('TCM-05 Plans: the Ran against column; v1 · case now v2 opens the steps v1 had (not today\'s); — and not recorded for the others', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, '/test-management?tab=plans', { planItems: true })
  await page.getByRole('heading', { name: TM_PLAN_NAME }).click()
  const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: 'Ran against' }) })
  await expect(table.getByRole('columnheader')).toHaveText(['#', 'Test Case', 'Status', 'Ran against', 'Actions'])
  const row = (i: number) => table.locator('tbody > tr:not([data-in-page-row])').filter({ hasText: tmCaseId(i).slice(0, 8) })
  await expect(row(ACTIVE_CASE).getByRole('cell').nth(2), 'Status stays the third cell').toHaveText('passed')
  await expect(row(ACTIVE_CASE).getByRole('cell').nth(3)).toHaveText('v1 · case now v2')
  await expect(row(2).getByRole('cell').nth(3), 'never run').toHaveText('—')
  await expect(row(6).getByRole('cell').nth(3), 'run before versions were recorded').toHaveText('not recorded')
  await networkQuiet(page, api)
  const HISTORY = `${CASES}/${tmCaseId(ACTIVE_CASE)}/history`
  expect(requestsTo(api, HISTORY), 'no history read before the version is opened').toEqual([])

  await row(ACTIVE_CASE).getByRole('button', { name: 'v1', exact: true }).click()
  const below = row(ACTIVE_CASE).locator('xpath=following-sibling::tr[1]')
  await expect(below).toHaveAttribute('data-in-page-row', '')
  await expect(below.locator(':scope > td')).toHaveAttribute('colspan', '5')
  const steps = below.locator('[data-plan-item-steps="1"]')
  await expect(steps).toContainText('Steps it ran against (v1)')
  await expect(steps.locator('[data-plan-item-step]')).toHaveText(RAN_STEPS.map((s, i) => `#${i + 1} ${s.action}Expected: ${s.expected_result}`))
  await expect(steps, 'not the case\'s current steps').not.toContainText(NOW_STEPS[1].action)
  await networkQuiet(page, api)
  expect(requestsTo(api, HISTORY).length, 'the history is read once the version opens').toBeGreaterThan(0)
  await expectNoOverlay(page)

  // The case's History v1 lists the execution.
  await page.goto(`/test-management?case=${tmCaseId(ACTIVE_CASE)}`)
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(ACTIVE_CASE) })).toBeVisible()
  await pane(page).getByRole('button', { name: 'History', exact: true }).click()
  await expect(card(page, 1).locator('[data-history-executions]')).toContainText(`Ran in ${TM_PLAN_NAME} · passed`)
  await expect(card(page, 2).locator('[data-history-executions]'), 'v2 never ran').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
