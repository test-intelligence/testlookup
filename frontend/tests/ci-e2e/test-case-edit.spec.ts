/**
 * W28 TCM-01 "edit a test case in the page" (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §2.1, §4.4),
 * hermetic, 1440 × 900 (the geometry test at 1280 and 1440):
 *
 *  - as a QA ENGINEER (lifecycle v2 on, review off): a case's Details › Edit opens `Edit test case` IN the Details
 *    tab (the toolbar hides, Title focused, the owner list read only now); the title, a step's expected result, a new
 *    step, a tag and the owner changed → Save changes → exactly ONE `PATCH /api/v1/test-management/cases/{id}` with
 *    `{ expected_version: 1, title, steps, tags, assignee_id }` (the untouched masked parameter is NOT sent), the toast
 *    `Test case saved (v2)`, the pane on v2 with its status unchanged, both case rolls re-read; History lists v2
 *    `by QA Engineer` with the title's before → after;
 *  - a conflict (the first PATCH answers 409 `{ current_version: 3, … }`; `Priya Shah` saved v2 and v3 meanwhile):
 *    the form re-reads the case and its history, the notice `Changed by Priya Shah since you opened it (v1 → v3).
 *    Changed: Title` IN the form, Keep editing → Save changes → a second PATCH with `expected_version: 3` and the
 *    reader's field only; never a silent retry;
 *  - as a TESTER (legacy lifecycle): Edit, New test case and AI Generate present, no row trash (retiring a case is a
 *    QA lead's, the AMENDMENT); as a VIEWER: no Edit, no New test case, no AI Generate, no trash, no comment box
 *    (`Viewers can read comments.`), no Run AI Review; a deprecated case's Edit is disabled (`Reinstate it to edit
 *    it`);
 *  - the form in the page at 1280 and 1440, split and expanded: no overlay, `#main-content` never scrolls sideways,
 *    the form inside the pane, its slack printed (`INPAGE /test-management case-edit …`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testCaseEditingWorld()`
 * (`fixtures-test-case-editing.ts`, over `testLibraryWorld()`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, USER } from '../visual/production/fixtures'
import { tmCaseId, tmCaseTitle } from '../visual/production/fixtures-in-page-b'
import {
  DEPRECATED_CASE,
  EDIT_CASE,
  NEW_OWNER,
  QA_ENGINEER,
  TESTER,
  testCaseEditingWorld,
  THEIR_TITLE,
  VIEWER,
  type FixtureUser,
  type TestCaseEditingOptions,
} from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const CASES = '/api/v1/test-management/cases'
const MEMBERS = `/api/v1/projects/${PROJECT_ID}/members`
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')
const toolbar = (p: Page) => pane(p).getByRole('group', { name: 'Case actions' })
const editButton = (p: Page) => toolbar(p).getByRole('button', { name: 'Edit', exact: true })
const editForm = (p: Page) => opened(p, 'Edit test case')
const paneTab = (p: Page, name: string) => pane(p).getByRole('button', { name, exact: true })
/** The form's Title (its label carries a hidden ` *`, so it is found by its accessible name). */
const titleField = (form: Locator) => form.getByRole('textbox', { name: 'Title', exact: true })
/** The two case rolls: the table's page (`size=25`) and the library health roll (`size=200`). */
const rollReads = (api: Parameters<typeof requestsTo>[0]) => {
  const lines = requestsTo(api, CASES)
  return { table: lines.filter((l) => l.includes('size=25')).length, health: lines.filter((l) => l.includes('size=200')).length }
}

async function open(page: Page, options: TestCaseEditingOptions & { user?: FixtureUser } = {}) {
  const world = testCaseEditingWorld(options)
  const opened_ = await openRollout(page, '/test-management', {
    handlers: world.handlers,
    ready: tmReady,
    user: options.user ?? USER,
    flags: options.lifecycleV2 ? { test_case_lifecycle_v2: true } : {},
  })
  await expect(caseRow(page, EDIT_CASE)).toBeVisible()
  return { world, ...opened_ }
}

/** Open a case's pane (its Details tab). */
async function openCase(page: Page, i: number) {
  await caseRow(page, i).click()
  await expect(pane(page)).toBeVisible()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(i) })).toBeVisible()
}

const STEP_1 = { step_number: 1, action: 'Add an item to the cart', expected_result: 'The cart badge shows 1' }

test('TCM-01 as a QA engineer: Edit in the Details tab → title, a step\'s expected result, a new step, a tag, the owner → ONE PATCH of only those; v2 by QA Engineer in History', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER, lifecycleV2: true })
  await openCase(page, EDIT_CASE)
  await networkQuiet(page, api)
  const membersAtLoad = requestsTo(api, MEMBERS).length

  await editButton(page).click()
  const form = editForm(page)
  await expect(form).toBeVisible()
  await expect(pane(page).locator(form), 'the form is in the case pane').toHaveCount(1)
  await expect(toolbar(page), 'the toolbar hides while the form is open').toHaveCount(0)
  await expect(titleField(form), 'Title is focused').toBeFocused()
  await expect(titleField(form)).toHaveValue(tmCaseTitle(EDIT_CASE))
  // The masked parameter is never prefilled; the plain one is.
  await expect(form.getByLabel('Parameter 1 value')).toHaveValue('')
  await expect(form.getByLabel('Parameter 1 value')).toHaveAttribute('type', 'password')
  await expect(form.getByLabel('Parameter 1 value')).toHaveAttribute('placeholder', 'Unchanged (masked)')
  await expect(form.getByLabel('Parameter 2 value')).toHaveValue('EU')
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  expect(requestsTo(api, MEMBERS).length, 'the owner list is read when the form opens').toBeGreaterThan(membersAtLoad)

  const before = rollReads(api)
  await titleField(form).fill('Checkout case 2: the cart survives a refresh and a re-login')
  await form.getByLabel('Step 2 expected result').fill('The cart still holds both items')
  await form.getByRole('button', { name: 'Add step' }).click()
  await form.getByLabel('Step 3 action').fill('Sign out and back in')
  await form.getByLabel('Step 3 expected result').fill('The cart still holds the item')
  await form.getByLabel('Tags', { exact: true }).fill('cart, regression')
  await form.getByLabel('Owner', { exact: true }).selectOption({ label: NEW_OWNER.name })
  await form.getByRole('button', { name: 'Save changes' }).click()

  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case saved (v2)', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: `PATCH ${CASES}/${tmCaseId(EDIT_CASE)}`,
      body: {
        expected_version: 1,
        title: 'Checkout case 2: the cart survives a refresh and a re-login',
        steps: [
          STEP_1,
          { step_number: 2, action: 'Refresh the page', expected_result: 'The cart still holds both items' },
          { step_number: 3, action: 'Sign out and back in', expected_result: 'The cart still holds the item' },
        ],
        tags: ['cart', 'regression'],
        assignee_id: NEW_OWNER.id,
      },
    },
  ])
  // The pane shows the saved version; editing never changed the status.
  await expect(pane(page).getByRole('heading', { name: 'Checkout case 2: the cart survives a refresh and a re-login' })).toBeVisible()
  await expect(toolbar(page), 'the toolbar is back').toBeVisible()
  await expect(pane(page).getByText('Sign out and back in', { exact: true })).toBeVisible()
  expect(world.cases[EDIT_CASE].status, 'the status is unchanged').toBe('draft')
  const after = rollReads(api)
  expect(after.table, 'the table roll re-reads').toBeGreaterThan(before.table)
  expect(after.health, 'the health roll re-reads').toBeGreaterThan(before.health)

  // History: v2 by QA Engineer, the title's before → after.
  await paneTab(page, 'History').click()
  const v2 = pane(page).locator('[data-history-version="2"]')
  await expect(v2).toBeVisible()
  await expect(v2).toContainText('v2 — updated')
  await expect(v2).toContainText('by QA Engineer')
  const title = v2.getByTestId('history-changes-v2').locator('li').filter({ hasText: 'Title:' })
  await expect(title.locator('[data-diff-before]')).toHaveText(tmCaseTitle(EDIT_CASE))
  await expect(title.locator('[data-diff-after]')).toHaveText('Checkout case 2: the cart survives a refresh and a re-login')
  const changes = v2.getByTestId('history-changes-v2').locator('li')
  await expect(changes.filter({ hasText: 'Owner:' })).toHaveText(`Owner: Unassigned → ${NEW_OWNER.name}`)
  await expect(changes.filter({ hasText: 'Step 3 added' })).toHaveText('Step 3 added: Sign out and back in')
  await expect(changes.filter({ hasText: 'Tags:' })).toHaveText('Tags: cart → cart, regression')
  await expect(pane(page).locator('[data-history-version]').first(), 'newest first').toHaveAttribute('data-history-version', '2')
  await networkQuiet(page, api)
  expect(world.writes, 'one write in all').toHaveLength(1)
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('TCM-01 a conflict: the 409 is shown IN the form with who and what changed; Keep editing → Save sends the fresh version and the reader\'s field only', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER, lifecycleV2: true, conflictOnFirstPatch: true })
  await openCase(page, EDIT_CASE)
  await editButton(page).click()
  const form = editForm(page)
  await form.getByLabel('Description', { exact: true }).fill('The cart keeps its items across a refresh and a new tab.')
  await networkQuiet(page, api)
  const reads = { one: requestsTo(api, `${CASES}/${tmCaseId(EDIT_CASE)}`).length, history: requestsTo(api, `${CASES}/${tmCaseId(EDIT_CASE)}/history`).length }
  await form.getByRole('button', { name: 'Save changes' }).click()

  const notice = form.locator('[data-edit-conflict]')
  await expect(notice).toBeVisible()
  await expect(notice).toHaveAttribute('role', 'alert')
  await expect(notice).toContainText('Changed by Priya Shah since you opened it (v1 → v3).')
  await expect(notice).toContainText('Changed: Title')
  await expect(notice, 'the reader did not change the title').not.toContainText('You also changed')
  await expect(form, 'the form stays open').toBeVisible()
  await expect(form.getByLabel('Description', { exact: true }), 'what the reader typed is kept').toHaveValue('The cart keeps its items across a refresh and a new tab.')
  await networkQuiet(page, api)
  expect(requestsTo(api, `${CASES}/${tmCaseId(EDIT_CASE)}`).length, 'the case is re-read').toBeGreaterThan(reads.one)
  expect(requestsTo(api, `${CASES}/${tmCaseId(EDIT_CASE)}/history`).length, 'and its history').toBeGreaterThan(reads.history)
  expect(world.writes.map((w) => w.line), 'a 409 is never retried by itself').toEqual([`PATCH ${CASES}/${tmCaseId(EDIT_CASE)}`])
  await expectNoOverlay(page)

  await notice.getByRole('button', { name: 'Keep editing' }).click()
  await expect(titleField(form), 'an untouched field takes theirs').toHaveValue(THEIR_TITLE)
  await expect(form.getByLabel('Description', { exact: true }), 'the reader\'s field keeps theirs').toHaveValue('The cart keeps its items across a refresh and a new tab.')
  await form.getByRole('button', { name: 'Save changes' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case saved (v4)', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PATCH ${CASES}/${tmCaseId(EDIT_CASE)}`, body: { expected_version: 1, description: 'The cart keeps its items across a refresh and a new tab.' } },
    { line: `PATCH ${CASES}/${tmCaseId(EDIT_CASE)}`, body: { expected_version: 3, description: 'The cart keeps its items across a refresh and a new tab.' } },
  ])
  expect(world.cases[EDIT_CASE].title, 'their title survives').toBe(THEIR_TITLE)
  await expect(pane(page).getByRole('heading', { name: THEIR_TITLE })).toBeVisible()
  assertHermetic(api, errors)
})

/** A row's trash (Delete in the legacy world, Deprecate in v2). */
const trash = (p: Page, i: number) => caseRow(p, i).getByRole('button', { name: /^(Delete|Deprecate) / })

test('TCM-01 a tester (legacy lifecycle): New test case, AI Generate and Edit; no row trash (retiring is a QA lead\'s)', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: TESTER })
  await expect(page.getByRole('button', { name: 'New test case from catalog toolbar' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'AI Generate', exact: true }).first()).toBeVisible()
  await expect(trash(page, EDIT_CASE), 'no trash below QA lead (the AMENDMENT)').toHaveCount(0)
  await openCase(page, EDIT_CASE)
  await expect(editButton(page)).toBeEnabled()
  await expect(toolbar(page).getByRole('button'), 'legacy: the toolbar holds only Edit').toHaveCount(1)
  await editButton(page).click()
  await expect(editForm(page)).toBeVisible()
  await editForm(page).getByRole('button', { name: 'Cancel' }).click()
  await expect(editForm(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('TCM-01 a viewer reads every tab and changes nothing: no Edit, New test case, AI Generate, trash, comment box or Run AI Review', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: VIEWER, lifecycleV2: true })
  await expect(page.getByRole('button', { name: 'New test case from catalog toolbar' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'AI Generate', exact: true })).toHaveCount(0)
  await expect(trash(page, EDIT_CASE)).toHaveCount(0)
  await openCase(page, EDIT_CASE)
  await expect(pane(page).getByRole('button', { name: 'Edit', exact: true }), 'no Edit').toHaveCount(0)
  await expect(toolbar(page), 'no toolbar at all').toHaveCount(0)
  await paneTab(page, 'Comments').click()
  await expect(pane(page).getByText('Step 2 needs a slower network to fail.')).toBeVisible()
  await expect(pane(page).getByText('Viewers can read comments.', { exact: true })).toBeVisible()
  await expect(pane(page).getByPlaceholder('Add a comment…'), 'no comment box').toHaveCount(0)
  await expect(pane(page).getByRole('button', { name: 'Post Comment' })).toHaveCount(0)
  await paneTab(page, 'AI Review').click()
  await expect(pane(page).getByText('AI review needs the Tester role or above.', { exact: true })).toBeVisible()
  await expect(pane(page).getByRole('button', { name: 'Run AI Review' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('TCM-01 a deprecated case: Edit is there, disabled, and says how to edit it', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER, lifecycleV2: true })
  await openCase(page, DEPRECATED_CASE)
  await expect(editButton(page)).toBeDisabled()
  await expect(editButton(page)).toHaveAttribute('title', 'Reinstate it to edit it')
  await expect(editForm(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

/** The open form's geometry: in the pane, nothing sideways, its slack printed. */
async function expectFormInPage(page: Page, what: string, width: number, form: Locator) {
  await expectNoOverlay(page)
  await expectMainFits(page, `${what} @${width}`)
  expect(await ownOverflow(form), `${what} @${width}: the form scrolls sideways`).toBeLessThanOrEqual(1)
  const [formBox, paneBox] = [await boxOf(form), await boxOf(pane(page))]
  expect(formBox.left, `${what} @${width}: inside the pane`).toBeGreaterThanOrEqual(paneBox.left - 1)
  expect(formBox.right, `${what} @${width}: inside the pane`).toBeLessThanOrEqual(paneBox.right + 1)
  inPageLine(what, width, { pane: paneBox.width, form: formBox.width, slack: await minContentSlack(form) })
}

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 } })

    test(`@${width}: the edit form sits in the case pane, split and expanded; nothing scrolls sideways`, async ({ page }) => {
      watchNativeDialogs(page)
      const { world, api, errors } = await open(page, { user: QA_ENGINEER, lifecycleV2: true })
      await openCase(page, EDIT_CASE)
      await editButton(page).click()
      const form = editForm(page)
      await expect(form).toBeVisible()
      await networkQuiet(page, api)
      await expectFormInPage(page, '/test-management case-edit (split)', width, form)
      await pane(page).getByRole('button', { name: 'Expand the case to full width' }).click()
      await expect(pane(page)).toHaveAttribute('data-expanded', 'true')
      await expect(form, 'expanding keeps the form open').toBeVisible()
      await expectFormInPage(page, '/test-management case-edit (expanded)', width, form)
      await form.getByRole('button', { name: 'Cancel' }).click()
      await expect(form).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes, 'Cancel sends nothing').toEqual([])
      assertHermetic(api, errors)
    })
  })
}
