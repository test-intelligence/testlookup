/**
 * W28 TCM-02 "change status the way the team works" (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §2.2,
 * §1.2 as the AMENDMENT keeps it: Deprecate / Archive / Reinstate stay QA_LEAD or above), hermetic, lifecycle v2 on,
 * 1440 × 900:
 *
 *  - review OFF as a TESTER: a Draft's Details toolbar (`group` "Case actions") offers `Make active` and `Request
 *    review` (no retire move) and says `Review is optional in this project.`; Make active → exactly
 *    `POST …/cases/{id}/transition` `{ action: 'activate', expected_version: 1 }`, `Moved to Active`, the pill Active;
 *    Back to draft → `{ action: 'revise', expected_version: 2 }`, `Moved to Draft`;
 *  - as a VIEWER: no toolbar; the Lifecycle tab lists every action disabled with `Viewers cannot change test cases`;
 *  - review ON as a TESTER: no Make active; the Lifecycle tab's Activate says `Review is required in this project:
 *    request a review first`;
 *  - review ON as the QA LEAD on their own Draft: Request review → `Approve my case…` → the reason form under the
 *    toolbar → `{ action: 'approve', reason, notes, expected_version: 2 }`, `Moved to Approved`;
 *  - the AMENDMENT: a QA LEAD's `Deprecate…` with a reason → `{ action: 'deprecate', reason, expected_version }`; a QA
 *    ENGINEER sees no Deprecate / Archive / Reinstate, on any case; a QA lead sees Archive / Reinstate on a
 *    Deprecated case;
 *  - an edited-after-approval Active case (review on): `Edited after approval (since v2; now v2).`, Request review
 *    FIRST with its hint, and `edited after approval` on its row;
 *  - `/projects` › Edit Project › tick `Require review before a test case becomes Active` → exactly
 *    `PUT /api/v1/projects/{id}` `{ require_case_review: true }`;
 *  - Plans › Link Suite links only the Active (approved / active) manual cases by default: two POSTs and
 *    `Linked 2 test cases from "Checkout" · 1 not Active left out`; with `Include cases that are not Active` three
 *    (the Draft too); the Deprecated case and the automation row never.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testCaseEditingWorld()`,
 * `projectsReviewWorld()` (`fixtures-test-case-editing.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { precedes } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { USER } from '../visual/production/fixtures'
import { LEGACY_PROJECT, TM_PLAN_ID, TM_PLAN_NAME, tmCaseId, tmCaseTitle } from '../visual/production/fixtures-in-page-b'
import {
  ACTIVE_CASE,
  DEPRECATED_CASE,
  EDIT_CASE,
  OWN_CASE,
  projectsReviewWorld,
  QA_ENGINEER,
  SUITE_CASES,
  TESTER,
  testCaseEditingWorld,
  VIEWER,
  type FixtureUser,
  type TestCaseEditingOptions,
} from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const CASES = '/api/v1/test-management/cases'
const V2 = { test_case_lifecycle_v2: true }
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')
const toolbar = (p: Page) => pane(p).getByRole('group', { name: 'Case actions' })
/** The toolbar's moves (every button but Edit), by their labels. */
const moves = (p: Page) => toolbar(p).getByRole('button').filter({ hasNotText: /^Edit$/ })
const move = (p: Page, label: string) => toolbar(p).getByRole('button', { name: label, exact: true })
const RETIRE_LABELS = ['Deprecate…', 'Archive…', 'Reinstate…']

async function open(page: Page, options: TestCaseEditingOptions & { user?: FixtureUser; path?: string } = {}) {
  const world = testCaseEditingWorld({ lifecycleV2: true, ...options })
  const opened_ = await openRollout(page, options.path ?? '/test-management', { handlers: world.handlers, ready: tmReady, user: options.user ?? USER, flags: V2 })
  return { world, ...opened_ }
}

async function openCase(page: Page, i: number) {
  await caseRow(page, i).click()
  await expect(pane(page)).toBeVisible()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(i) })).toBeVisible()
}

/** The pane subtitle's status pill reads the state (`StatusPill` prints it as written, `review requested`). */
const expectPill = (page: Page, status: string) =>
  expect(pane(page).getByText(status.replace(/_/g, ' '), { exact: true }).first()).toBeVisible()

test('review off as a tester: Make active then Back to draft, each one transition POST with the case version; no retire move', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: TESTER })
  await openCase(page, EDIT_CASE)
  await expect(moves(page)).toHaveText(['Make active', 'Request review'])
  await expect(pane(page).getByText('Review is optional in this project.', { exact: true })).toBeVisible()
  expect(await precedes(toolbar(page), pane(page).getByText('Test Steps', { exact: true })), 'the toolbar is above the details').toBe(true)
  await networkQuiet(page, api)

  await move(page, 'Make active').click()
  await expect(page.getByText('Moved to Active', { exact: true })).toBeVisible()
  await expectPill(page, 'active')
  await expect(moves(page), 'from Active: back to draft, or flag it').toHaveText(['Back to draft', 'Needs update…'])
  await move(page, 'Back to draft').click()
  await expect(page.getByText('Moved to Draft', { exact: true })).toBeVisible()
  await expectPill(page, 'draft')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(EDIT_CASE)}/transition`, body: { action: 'activate', expected_version: 1 } },
    { line: `POST ${CASES}/${tmCaseId(EDIT_CASE)}/transition`, body: { action: 'revise', expected_version: 2 } },
  ])
  for (const label of RETIRE_LABELS) await expect(move(page, label), `a tester has no ${label}`).toHaveCount(0)
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('a viewer: no toolbar; the Lifecycle tab lists every action disabled, each saying "Viewers cannot change test cases"', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: VIEWER })
  await openCase(page, EDIT_CASE)
  await expect(toolbar(page), 'no Status moves and no Edit').toHaveCount(0)
  await expect(pane(page).getByText('Review is optional in this project.'), 'no review line without moves').toHaveCount(0)
  await pane(page).getByRole('button', { name: 'Lifecycle', exact: true }).click()
  const lifecycle = pane(page).getByRole('region', { name: 'Test case lifecycle' })
  const actions = lifecycle.getByRole('button')
  await expect(actions).toHaveCount(13)
  for (let i = 0; i < 13; i++) {
    await expect(actions.nth(i)).toBeDisabled()
    await expect(actions.nth(i)).toHaveAttribute('title', 'Viewers cannot change test cases')
  }
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('review on as a tester: no Make active on a Draft; the Lifecycle tab says a review is required', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: TESTER, reviewRequired: true })
  await openCase(page, EDIT_CASE)
  await expect(moves(page)).toHaveText(['Request review'])
  await expect(pane(page).getByText('Review is required before Active in this project.', { exact: true })).toBeVisible()
  await pane(page).getByRole('button', { name: 'Lifecycle', exact: true }).click()
  const activate = pane(page).getByRole('region', { name: 'Test case lifecycle' }).getByRole('button', { name: 'Activate', exact: true })
  await expect(activate).toBeDisabled()
  await expect(activate).toHaveAttribute('title', 'Review is required in this project: request a review first')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('review on as the QA lead on their own Draft: Request review, then Approve my case… with a reason (the reason is the review note too)', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { reviewRequired: true })
  await openCase(page, OWN_CASE)
  await expect(moves(page)).toHaveText(['Request review', 'Deprecate…'])
  await expect(move(page, 'Make active'), 'review is required').toHaveCount(0)

  await move(page, 'Request review').click()
  await expect(page.getByText('Moved to Review requested', { exact: true })).toBeVisible()
  await expect(moves(page), 'their own case: withdraw it, or approve it with a reason').toHaveText(['Withdraw review', 'Approve my case…'])

  await move(page, 'Approve my case…').click()
  const form = opened(page, 'Approve my case')
  await expect(form).toBeVisible()
  expect(await precedes(toolbar(page), form), 'the reason form is right under the toolbar').toBe(true)
  await expect(form).toContainText('This change is recorded in the audit trail with your reason.')
  await expect(form.getByRole('button', { name: 'Approve my case', exact: true }), 'no reason: disabled').toBeDisabled()
  await expect(moves(page).first(), 'the moves wait while the form is open').toBeDisabled()
  await expectNoOverlay(page)
  await form.getByLabel('Reason').fill('Covered by the checkout walkthrough')
  await form.getByRole('button', { name: 'Approve my case', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Moved to Approved', { exact: true })).toBeVisible()
  await expectPill(page, 'approved')
  await expect(moves(page)).toHaveText(['Make active', 'Needs update…', 'Deprecate…'])
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(OWN_CASE)}/transition`, body: { action: 'request_review', expected_version: 1 } },
    {
      line: `POST ${CASES}/${tmCaseId(OWN_CASE)}/transition`,
      body: { action: 'approve', reason: 'Covered by the checkout walkthrough', notes: 'Covered by the checkout walkthrough', expected_version: 2 },
    },
  ])
  assertHermetic(api, errors)
})

test('the AMENDMENT: a QA lead deprecates with a reason; a QA engineer is offered no Deprecate, Archive or Reinstate', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page)
  await openCase(page, EDIT_CASE)
  await move(page, 'Deprecate…').click()
  const form = opened(page, 'Deprecate')
  await expect(form).toBeVisible()
  await form.getByLabel('Reason').fill('Replaced by the guest checkout case')
  await form.getByRole('button', { name: 'Deprecate', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Moved to Deprecated', { exact: true })).toBeVisible()
  await expectPill(page, 'deprecated')
  await expect(moves(page), 'a deprecated case: archive or reinstate it').toHaveText(['Archive…', 'Reinstate…'])
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(EDIT_CASE)}/transition`, body: { action: 'deprecate', reason: 'Replaced by the guest checkout case', expected_version: 1 } },
  ])
  assertHermetic(api, errors)
})

test('the AMENDMENT: a QA engineer sees no retire move on a Draft, an Active or a Deprecated case, and no row trash', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER })
  await expect(caseRow(page, EDIT_CASE).getByRole('button', { name: /^(Delete|Deprecate) / }), 'no row trash').toHaveCount(0)
  for (const [i, expected] of [[EDIT_CASE, ['Make active', 'Request review']], [ACTIVE_CASE, ['Back to draft', 'Needs update…']], [DEPRECATED_CASE, []]] as const) {
    await openCase(page, i)
    await expect(moves(page), `case ${i + 1}`).toHaveText([...expected])
    for (const label of RETIRE_LABELS) await expect(move(page, label), `case ${i + 1}: no ${label}`).toHaveCount(0)
  }
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('an Active case edited after its approval (review on): the line, Request review first with its hint, and the row\'s mark', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { reviewRequired: true, editedAfterApproval: true })
  await expect(caseRow(page, ACTIVE_CASE).locator('[data-edited-after-approval-mark]')).toHaveText('· edited after approval')
  await expect(caseRow(page, EDIT_CASE).locator('[data-edited-after-approval-mark]'), 'only that case').toHaveCount(0)
  await openCase(page, ACTIVE_CASE)
  const line = pane(page).locator('[data-edited-after-approval]')
  await expect(line).toContainText('Edited after approval (since v2; now v2).')
  await expect(line).toContainText('Request review moves it to Review requested until a reviewer approves it; plans that already hold it keep it.')
  await expect(moves(page), 'Request review first').toHaveText(['Request review', 'Needs update…', 'Deprecate…'])
  await expect(pane(page).locator('[data-edited-after-approval-mark]'), 'the pane subtitle marks it too').toHaveCount(1)
  await expectPill(page, 'active')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('/projects › Edit Project: the review checkbox, unticked by default, ticked → a PUT of exactly { require_case_review: true }', async ({ page }) => {
  watchNativeDialogs(page)
  const world = projectsReviewWorld()
  const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: (p) => p.getByRole('heading', { level: 1, name: 'Projects', exact: true }) })
  const card = page.locator('.card').filter({ has: page.getByText(LEGACY_PROJECT.name, { exact: true }) })
  await card.hover()
  await card.getByRole('button', { name: 'Edit' }).click()
  const form = opened(page, 'Edit Project')
  const box = form.getByLabel('Require review before a test case becomes Active', { exact: true })
  await expect(box).not.toBeChecked()
  await expect(form).toContainText('When on, a test case needs an approved review before it can be made Active. A QA lead or administrator can approve their own case with a reason.')
  expect(await precedes(form.getByLabel('Tags (comma-separated)'), box), 'after the Tags field').toBe(true)
  await box.check()
  await form.getByRole('button', { name: 'Save Changes' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Project updated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `PUT /api/v1/projects/${LEGACY_PROJECT.id}`, body: { require_case_review: true } }])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('Plans › Link Suite links the Active manual cases by default (and says how many were left out); the box adds the Draft; never the Deprecated one', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { path: '/test-management?tab=plans' })
  await page.getByRole('heading', { name: TM_PLAN_NAME }).click()
  await expect(page.getByText('No test cases in this plan yet. Link a suite or add test cases.')).toBeVisible()
  await networkQuiet(page, api)
  const ITEMS = `/api/v1/test-management/plans/${TM_PLAN_ID}/items`
  const [active, , approved, draft] = SUITE_CASES

  await page.getByRole('button', { name: 'Link Suite', exact: true }).first().click()
  let form = opened(page, 'Link Test Suite to Plan')
  await form.getByRole('combobox').selectOption('Checkout')
  await expect(form.getByLabel('Include cases that are not Active'), 'unticked').not.toBeChecked()
  await form.getByRole('button', { name: 'Link Suite', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Linked 2 test cases from "Checkout" · 1 not Active left out', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${ITEMS}`, body: { test_case_id: active.id } },
    { line: `POST ${ITEMS}`, body: { test_case_id: approved.id } },
  ])

  await page.getByRole('button', { name: 'Link Suite', exact: true }).first().click()
  form = opened(page, 'Link Test Suite to Plan')
  await form.getByRole('combobox').selectOption('Checkout')
  await form.getByLabel('Include cases that are not Active').check()
  await form.getByRole('button', { name: 'Link Suite', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Linked 3 test cases from "Checkout"', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(2)).toEqual([
    { line: `POST ${ITEMS}`, body: { test_case_id: active.id } },
    { line: `POST ${ITEMS}`, body: { test_case_id: approved.id } },
    { line: `POST ${ITEMS}`, body: { test_case_id: draft.id } },
  ])
  expect(world.writes.map((w) => (w.body as { test_case_id: string }).test_case_id), 'never the deprecated case or the automation row')
    .not.toContain(SUITE_CASES[4].id)
  expect(world.writes.map((w) => (w.body as { test_case_id: string }).test_case_id)).not.toContain(SUITE_CASES[1].id)
  assertHermetic(api, errors)
})
