/**
 * W27b PAN-05a CHARACTERIZATION, `/test-management` (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.1,
 * rows L1-L11), hermetic. Written against the MODAL tree (`ModalWrap`'s four forms, Link Suite, Add test suite, the
 * `TransitionReasonDialog`, the Insights `SidePanel`, the citations' fixed edge panel) and kept green, UNEDITED,
 * after the in-page conversion: each container is found by its kept name (`opened`), only named controls are
 * driven (`ModalWrap`'s X is a bare `×` today: never pressed), and Escape only where it works today (the reason
 * form, `useModalFocus`).
 *
 *  - L1 New test case → `POST /api/v1/test-management/cases` (title trimmed, blank optionals OMITTED, the text
 *    fields untrimmed, only steps with an action, their numbers as typed, the duration rounded); both case rolls
 *    re-read; a 2-character title is refused with a toast and nothing sent; Cancel sends nothing;
 *  - L2 AI Generate › From a description → `POST …/cases/ai-generate/async` `{ project_id, requirements (trimmed),
 *    persist: true }`, the toast, the form closes and the list is NOT re-read; › From documents reads the RAG
 *    status and the knowledge sources and nothing else;
 *  - L3 a row's Delete (legacy) / Deprecate (lifecycle v2) → the reason → `DELETE …/cases/{id}` `{ reason }` /
 *    `POST …/cases/{id}/transition` `{ action, reason, expected_version }`; Cancel and Escape send nothing; the
 *    row's click never ran (no case opened);
 *  - L4 the case pane › Lifecycle › Deprecate → a 409 shows its inline alert and keeps the form, the retry
 *    succeeds; Escape closes the form only;
 *  - L5 Plans › New Plan → `POST …/plans` (the whole body); a blank name is refused; L6 a plan › Link Suite →
 *    the suites read on open, `GET …/suites/Checkout/cases?size=500`, one `POST …/plans/{id}/items` per MANUAL
 *    case in order, the toast, the items and the plans re-read;
 *  - L7 Suites › Add test suite (QA lead) → `POST /api/v1/suites` (trimmed, tags split / trimmed / deduped, null
 *    when empty); a duplicate name keeps Create disabled with its sentence;
 *  - L8 More › Strategy › Generate Strategy → `POST …/strategies/ai-generate/async` (context trimmed, the name
 *    untrimmed, omitted when blank); the strategies re-read on EVERY close, Cancel included;
 *  - L9 Approvals › a card's decision → the review notes → `POST …/review-action` `{ action, notes }` (legacy) /
 *    `POST …/transition` `{ action, expected_version, notes }` (v2); Claim review sends at once;
 *  - L10 Insights → a review-queue row opens its case (`?case=`, replace) and Insights stays open; nothing sent;
 *  - L11 Knowledge (RAG on) › Generate → a case's Citations → another case's replaces it → close; nothing sent;
 *  - role gating: All Projects has no New test case / AI Generate / New Plan / Add test suite / Generate
 *    Strategy; a QA engineer has no Add test suite.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testLibraryWorld()`
 * (`fixtures-in-page-b.ts`, over `TEST_CASES`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  CHECKOUT_SUITE_CASES,
  RAG_ANSWER,
  SUITE_OWNER,
  testLibraryWorld,
  TM_PLAN_ID,
  TM_PLAN_NAME,
  tmCaseId,
  tmCaseTitle,
  type TestLibraryOptions,
} from '../visual/production/fixtures-in-page-b'
import { RELEASE_PEOPLE } from '../visual/production/fixtures-pages'
import { ADMIN, QA_ENGINEER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Api = Parameters<typeof requestsTo>[0]
const P = PROJECT_ID
const CASES = '/api/v1/test-management/cases'
const ready = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const V2 = { test_case_lifecycle_v2: true }

async function open(page: Page, path: string, options: TestLibraryOptions & { user?: typeof QA_ENGINEER } = {}) {
  const world = testLibraryWorld(options)
  const opened_ = await openRollout(page, path, {
    handlers: world.handlers,
    ready,
    user: options.user,
    flags: options.lifecycleV2 ? V2 : {},
  })
  return { world, ...opened_ }
}

/** A case's own row in the cases table (never a form row a conversion puts under it). */
const caseRow = (page: Page, i: number) =>
  page.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
/** The two reads a write refreshes: the table's page (`size=25`) and the library health (W13: `GET /test-management/health`, was the `size=200` roll). */
const rollReads = (api: Api) => {
  const lines = requestsTo(api, CASES)
  return { table: lines.filter((l) => l.includes('size=25')).length, health: requestsTo(api, '/api/v1/test-management/health').length }
}
const pathAndSearch = (page: Page) => {
  const url = new URL(page.url())
  return `${url.pathname}${url.search}`
}
/** Every request path the page asked since `from` (index into the request log). */
const pathsSince = (api: Api, from: number) => observed(api).slice(from).map((line) => line.split(' ')[1].split('?')[0])

test('L1 New test case: Cancel sends nothing; a 2-character title is refused; the whole POST, the toast, both rolls re-read', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management')
  await expect(caseRow(page, 0)).toBeVisible()
  await networkQuiet(page, api)

  const create = () => page.getByRole('button', { name: 'New test case from catalog toolbar' }).click()
  await create()
  let form = opened(page, 'New Test Case')
  await expect(form).toBeVisible()
  await form.getByLabel('Title *').fill('Something typed')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)

  await create()
  form = opened(page, 'New Test Case')
  await expect(form.getByLabel('Title *'), 'a reopened form is empty').toHaveValue('')
  await form.getByLabel('Title *').fill('  ab ')
  await form.getByRole('button', { name: 'Create Test Case' }).click()
  await expect(page.getByText('Title must be at least 3 characters', { exact: true })).toBeVisible()
  await expect(form, 'a refused title keeps the form').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const before = rollReads(api)
  await form.getByLabel('Title *').fill('  Guest checkout keeps the basket  ')
  await form.getByLabel('Test Type').selectOption('e2e')
  await form.getByLabel('Priority').selectOption('high')
  await form.getByLabel('Assignee (optional)').selectOption({ label: RELEASE_PEOPLE.priya.name })
  await form.getByLabel('Feature Area').fill(' Checkout ')
  await form.getByLabel('Suite Name').fill('CheckoutTests')
  await form.getByLabel('Objective').fill('Prove the basket survives a reload ')
  await form.getByPlaceholder('Action').nth(0).fill('Add an item')
  await form.getByPlaceholder('Expected result').nth(0).fill('Badge shows 1')
  await form.getByRole('button', { name: 'Add Step' }).click()
  await form.getByPlaceholder('Action').nth(1).fill('   ')
  await form.getByPlaceholder('Expected result').nth(1).fill('dropped: no action')
  await form.getByRole('button', { name: 'Add Step' }).click()
  await form.getByPlaceholder('Action').nth(2).fill('Reload the page')
  await form.getByPlaceholder('Expected result').nth(2).fill('Basket kept')
  await form.getByLabel('Overall Expected Result').fill('The basket is unchanged')
  await form.getByLabel('Estimated Duration (min)').fill('4.6')
  await form.getByRole('button', { name: 'Create Test Case' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case created', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: `POST ${CASES}`,
      body: {
        project_id: P,
        title: 'Guest checkout keeps the basket',
        test_type: 'e2e',
        priority: 'high',
        assignee_id: RELEASE_PEOPLE.priya.id,
        feature_area: ' Checkout ',
        suite_name: 'CheckoutTests',
        objective: 'Prove the basket survives a reload ',
        steps: [
          { step_number: 1, action: 'Add an item', expected_result: 'Badge shows 1' },
          { step_number: 3, action: 'Reload the page', expected_result: 'Basket kept' },
        ],
        expected_result: 'The basket is unchanged',
        estimated_duration_minutes: 5,
        severity: 'medium',
        is_automated: false,
        automation_status: 'not_automated',
      },
    },
  ])
  const after = rollReads(api)
  expect(after.table, 'the table roll re-reads').toBeGreaterThan(before.table)
  expect(after.health, 'the health roll re-reads').toBeGreaterThan(before.health)
  assertHermetic(api, errors)
})

test('L2 AI Generate: Cancel sends nothing; a description → the async POST, the toast, no list re-read; From documents reads only its two sources', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management')
  await expect(caseRow(page, 0)).toBeVisible()
  await networkQuiet(page, api)

  const generate = () => page.getByRole('button', { name: 'AI Generate', exact: true }).first().click()
  await generate()
  let form = opened(page, 'AI Generate Test Cases')
  await expect(form).toBeVisible()
  const field = form.getByLabel('What to generate test cases for')
  await expect(field, 'the description is focused').toBeFocused()
  await field.fill('ab')
  await expect(form.getByRole('button', { name: 'Generate & Save' }), 'under 3 characters: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const casesBefore = requestsTo(api, CASES).length
  await generate()
  form = opened(page, 'AI Generate Test Cases')
  await form.getByLabel('What to generate test cases for').fill('  Guest checkout with a saved card  ')
  await form.getByRole('button', { name: 'Generate & Save' }).click()
  await expect(form).toHaveCount(0)
  await expect(
    page.getByText('Test cases are being generated in the background and will be saved as drafts — refresh the list in a moment.', { exact: true }),
  ).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/ai-generate/async`, body: { project_id: P, requirements: 'Guest checkout with a saved card', persist: true } },
  ])
  expect(requestsTo(api, CASES).length, 'no list re-read after a background generation').toBe(casesBefore)

  // From documents: the knowledge flow's two reads, nothing else.
  await generate()
  form = opened(page, 'AI Generate Test Cases')
  await expect(form.getByRole('tab', { name: 'From a description' })).toHaveAttribute('aria-selected', 'true')
  const from = observed(api).length
  await form.getByRole('tab', { name: 'From documents' }).click()
  await expect(form.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible()
  await expect(form.getByText('Checkout requirements')).toBeVisible()
  await networkQuiet(page, api)
  expect([...new Set(pathsSince(api, from))].sort()).toEqual(['/api/v1/knowledge-sources', '/api/v1/test-management/rag/status'])
  await form.getByRole('tab', { name: 'From a description' }).click()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  expect(world.writes).toHaveLength(1)
  assertHermetic(api, errors)
})

test('L3 a row\'s Delete (legacy): Cancel and Escape send nothing; a reason → the DELETE with its body, the toast, both rolls; no case opened', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management')
  await expect(caseRow(page, 0)).toBeVisible()
  await networkQuiet(page, api)
  const trash = () => caseRow(page, 0).getByRole('button', { name: `Delete ${tmCaseTitle(0)}` })

  await trash().click()
  let form = opened(page, 'Delete test case')
  await expect(form).toBeVisible()
  await expect(form).toContainText(`Delete ${tmCaseTitle(0)}. The case and its version history will be preserved.`)
  const reason = form.getByLabel('Reason')
  await expect(reason, 'the reason is focused').toBeFocused()
  await expect(form.getByRole('button', { name: 'Delete', exact: true }), 'no reason: disabled').toBeDisabled()
  await reason.fill('   ')
  await expect(form.getByRole('button', { name: 'Delete', exact: true }), 'spaces are no reason').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await trash().click()
  form = opened(page, 'Delete test case')
  await form.getByLabel('Reason').fill('typed')
  await form.getByLabel('Reason').press('Escape')
  await expect(form, 'Escape closes the reason form').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const before = rollReads(api)
  await trash().click()
  form = opened(page, 'Delete test case')
  await form.getByLabel('Reason').fill('  Duplicate of case 4  ')
  await form.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case deleted', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE ${CASES}/${tmCaseId(0)}`, body: { reason: 'Duplicate of case 4' } }])
  const after = rollReads(api)
  expect(after.table).toBeGreaterThan(before.table)
  expect(after.health).toBeGreaterThan(before.health)
  await expect(page.locator('[data-case-panel]'), 'the trash never opened the case').toHaveCount(0)
  expect(pathAndSearch(page)).toBe('/test-management')
  assertHermetic(api, errors)
})

test('L3 a row\'s Deprecate (lifecycle v2): a reason → the transition POST with the case version, the toast', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management', { lifecycleV2: true })
  await expect(caseRow(page, 0)).toBeVisible()
  await networkQuiet(page, api)
  await caseRow(page, 0).getByRole('button', { name: `Deprecate ${tmCaseTitle(0)}` }).click()
  const form = opened(page, 'Deprecate test case')
  await expect(form).toContainText(`Deprecate ${tmCaseTitle(0)}. The case and its version history will be preserved.`)
  await form.getByLabel('Reason').fill('  Replaced by the guest checkout case ')
  await form.getByRole('button', { name: 'Deprecate', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case deprecated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(0)}/transition`, body: { action: 'deprecate', reason: 'Replaced by the guest checkout case', expected_version: 1 } },
  ])
  await expect(page.locator('[data-case-panel]')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('L4 the case pane › Lifecycle › Deprecate: Cancel and Escape close only the form; a 409 keeps it with its alert; the retry succeeds', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management', { lifecycleV2: true, refuseFirstTransition: true })
  await expect(caseRow(page, 0)).toBeVisible()
  await caseRow(page, 0).click()
  const pane = page.locator('[data-case-panel]')
  await expect(pane).toBeVisible()
  await pane.getByRole('button', { name: 'Lifecycle', exact: true }).click()
  const lifecycle = pane.getByRole('region', { name: 'Test case lifecycle' })
  await expect(lifecycle.getByRole('button', { name: 'Archive', exact: true })).toBeDisabled()
  await expect(lifecycle.getByRole('button', { name: 'Archive', exact: true })).toHaveAttribute('title', 'Deprecate the case first')
  await networkQuiet(page, api)
  const allowed = `${CASES}/${tmCaseId(0)}/allowed-transitions`
  const allowedReads = requestsTo(api, allowed).length
  expect(allowedReads, 'the actions are read with the tab').toBeGreaterThan(0)

  const deprecate = () => lifecycle.getByRole('button', { name: 'Deprecate', exact: true }).click()
  await deprecate()
  let form = opened(page, 'Deprecate')
  await expect(form).toContainText('This action changes the governed lifecycle and will be recorded in the audit trail.')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await deprecate()
  form = opened(page, 'Deprecate')
  await form.getByLabel('Reason').press('Escape')
  await expect(form).toHaveCount(0)
  await expect(pane, 'Escape in the form leaves the case open').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await deprecate()
  form = opened(page, 'Deprecate')
  await form.getByLabel('Reason').fill('  Superseded by case 7 ')
  await form.getByRole('button', { name: 'Deprecate', exact: true }).click()
  await expect(pane.getByRole('alert')).toHaveText('Deprecate is no longer allowed from deprecated.')
  await expect(form, 'a refused transition keeps the form').toBeVisible()
  const before = rollReads(api)
  await form.getByRole('button', { name: 'Deprecate', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Deprecate complete', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const body = { action: 'deprecate', expected_version: 1, reason: 'Superseded by case 7' }
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(0)}/transition`, body },
    { line: `POST ${CASES}/${tmCaseId(0)}/transition`, body },
  ])
  expect(requestsTo(api, allowed).length, 'the actions re-read').toBeGreaterThan(allowedReads)
  const after = rollReads(api)
  expect(after.table, 'the list re-reads').toBeGreaterThan(before.table)
  expect(after.health).toBeGreaterThan(before.health)
  await expect(pane).toBeVisible()
  assertHermetic(api, errors)
})

test('L5 Plans › New Plan: Cancel sends nothing; a blank name is refused; the whole POST, the toast, the plans re-read', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=plans')
  await expect(page.getByRole('heading', { name: TM_PLAN_NAME })).toBeVisible()
  await networkQuiet(page, api)
  const newPlan = () => page.getByRole('button', { name: 'New Plan' }).first().click()

  await newPlan()
  let form = opened(page, 'New Test Plan')
  await expect(form).toBeVisible()
  await form.getByLabel('Plan Name *').fill('typed')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await newPlan()
  form = opened(page, 'New Test Plan')
  await form.getByLabel('Plan Name *').fill('   ')
  await form.getByRole('button', { name: 'Create Plan' }).click()
  await expect(page.getByText('Name is required', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/test-management/plans').length
  await form.getByLabel('Plan Name *').fill('  Sprint 43 smoke  ')
  await form.getByLabel('Description').fill('Smoke before the cut ')
  await form.getByLabel('Planned Start').fill('2026-10-20')
  await form.getByLabel('Planned End').fill('2026-10-24')
  await form.getByRole('button', { name: 'Create Plan' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test plan created', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sprint 43 smoke' })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/test-management/plans',
      body: {
        project_id: P,
        name: 'Sprint 43 smoke',
        description: 'Smoke before the cut ',
        planned_start_date: '2026-10-20',
        planned_end_date: '2026-10-24',
        status: 'draft',
        ai_generated: false,
        total_cases: 0,
        executed_cases: 0,
        passed_cases: 0,
        failed_cases: 0,
        blocked_cases: 0,
      },
    },
  ])
  expect(requestsTo(api, '/api/v1/test-management/plans').length, 'the plans re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('L6 a plan › Link Suite: the suites read on open; Cancel sends nothing; Checkout → its cases read, one POST per manual case in order', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=plans')
  await page.getByRole('heading', { name: TM_PLAN_NAME }).click()
  await expect(page.getByText('No test cases in this plan yet. Link a suite or add test cases.')).toBeVisible()
  await networkQuiet(page, api)
  const SUITES = '/api/v1/test-management/suites'
  expect(requestsTo(api, SUITES), 'no suite list before Link Suite').toEqual([])
  const link = () => page.getByRole('button', { name: 'Link Suite', exact: true }).first().click()

  await link()
  let form = opened(page, 'Link Test Suite to Plan')
  await expect(form).toBeVisible()
  await expect(form.getByRole('combobox').locator('option')).toHaveText(['— Select a suite —', 'Checkout (4 cases)', 'Auth (4 cases)'])
  await networkQuiet(page, api)
  // `LinkSuiteModal` reads the list in a plain effect: React StrictMode (the dev server) runs it twice.
  expect(requestsTo(api, SUITES)).toEqual([`GET ${SUITES}?project_id=${P}`, `GET ${SUITES}?project_id=${P}`])
  await expect(form.getByRole('button', { name: 'Link Suite', exact: true }), 'no suite: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const plansReads = requestsTo(api, '/api/v1/test-management/plans').length
  const itemsPath = `/api/v1/test-management/plans/${TM_PLAN_ID}/items`
  const itemReads = requestsTo(api, itemsPath).length
  await link()
  form = opened(page, 'Link Test Suite to Plan')
  await form.getByRole('combobox').selectOption('Checkout')
  await form.getByRole('button', { name: 'Link Suite', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Linked 2 test cases from "Checkout"', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, `${SUITES}/Checkout/cases`)).toEqual([`GET ${SUITES}/Checkout/cases?project_id=${P}&size=500`])
  expect(world.writes).toEqual([
    { line: `POST ${itemsPath}`, body: { test_case_id: CHECKOUT_SUITE_CASES[0].id } },
    { line: `POST ${itemsPath}`, body: { test_case_id: CHECKOUT_SUITE_CASES[2].id } },
  ])
  expect(requestsTo(api, itemsPath).length, 'the items re-read').toBeGreaterThan(itemReads)
  expect(requestsTo(api, '/api/v1/test-management/plans').length, 'the plans re-read').toBeGreaterThan(plansReads)
  await expect(page.getByRole('cell', { name: `${CHECKOUT_SUITE_CASES[0].id.slice(0, 8)}…` }), 'the plan lists the two linked cases').toHaveCount(2)
  assertHermetic(api, errors)
})

test('L7 Suites › Add test suite (QA lead): a duplicate keeps Create disabled; trimmed fields and deduped tags → the POST; empty ones → null', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=suites')
  await expect(page.getByText('2 suites')).toBeVisible()
  await networkQuiet(page, api)
  const add = () => page.getByRole('button', { name: 'Add test suite' }).click()

  await add()
  let form = opened(page, 'Add test suite')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Suite name *')).toBeFocused()
  const create = form.getByRole('button', { name: 'Create suite' })
  await form.getByLabel('Suite name *').fill('Checkout')
  await expect(form.getByText('A suite named “Checkout” already exists in this project.')).toBeVisible()
  await expect(create, 'a duplicate name: disabled').toBeDisabled()
  await form.getByLabel('Suite name *').fill('P')
  await expect(create, 'one character: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/test-management/suites').length
  await add()
  form = opened(page, 'Add test suite')
  await form.getByLabel('Suite name *').fill('  Payments smoke  ')
  await form.getByLabel('Description').fill('  Card flows  ')
  await form.getByLabel('Owner').selectOption(SUITE_OWNER.id)
  await form.getByLabel(/^Tags/).fill(' smoke, api ,smoke, , regression ')
  await form.getByRole('button', { name: 'Create suite' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test suite "Payments smoke" created', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/suites',
      body: { project_id: P, name: 'Payments smoke', description: 'Card flows', owner_user_id: SUITE_OWNER.id, tags: ['smoke', 'api', 'regression'] },
    },
  ])
  expect(requestsTo(api, '/api/v1/test-management/suites').length, 'the suites re-read').toBeGreaterThan(reads)

  await add()
  form = opened(page, 'Add test suite')
  await form.getByLabel('Suite name *').fill('Bare')
  await form.getByRole('button', { name: 'Create suite' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: 'POST /api/v1/suites', body: { project_id: P, name: 'Bare', description: null, owner_user_id: null, tags: null } },
  ])
  assertHermetic(api, errors)
})

test('L8 Strategy › Generate Strategy: Cancel sends nothing but re-reads; the context trimmed, the name untrimmed or omitted', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=strategy')
  await expect(page.getByText('No test strategy')).toBeVisible()
  await networkQuiet(page, api)
  const STRATEGIES = '/api/v1/test-management/strategies'
  const generate = () => page.getByRole('button', { name: 'Generate Strategy' }).first().click()

  let reads = requestsTo(api, STRATEGIES).length
  await generate()
  let form = opened(page, 'Generate Test Strategy with AI')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Project Context *')).toBeFocused()
  await expect(form.getByRole('button', { name: 'Generate & Save' }), 'no context: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(requestsTo(api, STRATEGIES).length, 'Cancel still re-reads the strategies').toBeGreaterThan(reads)

  reads = requestsTo(api, STRATEGIES).length
  await generate()
  form = opened(page, 'Generate Test Strategy with AI')
  await form.getByLabel('Strategy Name (optional)').fill(' Q4 plan ')
  await form.getByLabel('Project Context *').fill('  Web shop, six engineers, weekly releases  ')
  await form.getByRole('button', { name: 'Generate & Save' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Strategy is being generated in the background and will appear in the list shortly.', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: `POST ${STRATEGIES}/ai-generate/async`,
      body: { project_id: P, project_context: 'Web shop, six engineers, weekly releases', strategy_name: ' Q4 plan ' },
    },
  ])
  expect(requestsTo(api, STRATEGIES).length, 'the strategies re-read').toBeGreaterThan(reads)

  await generate()
  form = opened(page, 'Generate Test Strategy with AI')
  await form.getByLabel('Project Context *').fill('Mobile app')
  await form.getByRole('button', { name: 'Generate & Save' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: `POST ${STRATEGIES}/ai-generate/async`, body: { project_id: P, project_context: 'Mobile app' } }])
  assertHermetic(api, errors)
})

/** An approval card by its case's title. */
const approvalCard = (page: Page, i: number): Locator =>
  page.locator('[data-tab-panel="approvals"] .card').filter({ has: page.getByRole('heading', { name: tmCaseTitle(i), level: 4 }) })

test('L9 Approvals (legacy) › Request changes: Cancel sends nothing; notes → the review-action POST, the toast, the queues re-read', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=approvals')
  await expect(approvalCard(page, 2)).toBeVisible()
  await networkQuiet(page, api)
  const decide = () => approvalCard(page, 2).getByRole('button', { name: 'Request changes', exact: true }).first().click()

  await decide()
  let form = opened(page, 'Request changes')
  await expect(form).toBeVisible()
  await expect(form).toContainText('Record a nonblank review note for the audit trail.')
  await expect(form.getByLabel('Review notes')).toBeFocused()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const queueReads = requestsTo(api, CASES).filter((l) => l.includes('status=review_requested')).length
  await decide()
  form = opened(page, 'Request changes')
  await form.getByLabel('Review notes').fill('  Steps 2 and 3 overlap  ')
  await form.getByRole('button', { name: 'Request changes', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case request changes', { exact: true })).toBeVisible()
  await expect(approvalCard(page, 2), 'the case left the queue').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/${tmCaseId(2)}/review-action`, body: { action: 'request_changes', notes: 'Steps 2 and 3 overlap' } },
  ])
  expect(requestsTo(api, CASES).filter((l) => l.includes('status=review_requested')).length, 'the queues re-read').toBeGreaterThan(queueReads)
  assertHermetic(api, errors)
})

test('L9 Approvals (lifecycle v2): Claim review sends at once; Approve → the notes → the transition POST with the version', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=approvals', { lifecycleV2: true })
  await expect(approvalCard(page, 5)).toBeVisible()
  await networkQuiet(page, api)

  await approvalCard(page, 2).getByRole('button', { name: 'Claim review', exact: true }).click()
  await expect(page.getByText('Test case claim review', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${CASES}/${tmCaseId(2)}/transition`, body: { action: 'claim_review', expected_version: 1 } }])

  await approvalCard(page, 5).getByRole('button', { name: 'Approve', exact: true }).first().click()
  const form = opened(page, 'Approve')
  await form.getByLabel('Review notes').fill(' Looks complete ')
  await form.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case approve', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `POST ${CASES}/${tmCaseId(5)}/transition`, body: { action: 'approve', expected_version: 1, notes: 'Looks complete' } },
  ])
  assertHermetic(api, errors)
})

test('L10 Insights: nothing sent; a review-queue row opens its case (?case=, replace) and Insights stays open; Close panel', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management')
  await expect(caseRow(page, 0)).toBeVisible()
  await networkQuiet(page, api)
  await expect(opened(page, 'Insights')).toHaveCount(0)
  const from = observed(api).length

  await page.getByRole('button', { name: /^Insights/ }).click()
  const insights = opened(page, 'Insights')
  await expect(insights).toBeVisible()
  await expect(insights.getByRole('heading', { name: 'Review queue · 2' })).toBeVisible()
  await networkQuiet(page, api)
  expect(observed(api).slice(from), 'Insights sends nothing: its cards come from the tab\'s reads').toEqual([])

  const entries = await page.evaluate(() => window.history.length)
  await insights.getByRole('button', { name: new RegExp(tmCaseTitle(2).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click()
  await expect.poll(() => pathAndSearch(page)).toBe(`/test-management?case=${tmCaseId(2)}`)
  await expect(page.locator('[data-case-panel]')).toBeVisible()
  await expect(insights, 'Insights stays open').toBeVisible()
  expect(await page.evaluate(() => window.history.length), 'the case replaces the entry').toBe(entries)

  await insights.getByRole('button', { name: 'Close panel' }).click()
  await expect(insights).toHaveCount(0)
  await expect(page.locator('[data-case-panel]'), 'the case stays open').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

/** The citations of one generated case: today a fixed edge panel with no role and no name, after W27b an `aside`
 *  named by its heading `Citations (<n>)`. Found through the heading either way (the panel's only stable name). */
const citationsHeading = (page: Page) => page.getByRole('heading', { name: /^Citations \(\d+\)$/ })
const citations = (page: Page) =>
  opened(page, /^Citations \(\d+\)$/).or(page.locator('div.fixed').filter({ has: citationsHeading(page) }))
/** The citations' close control: today an unnamed icon button beside the heading, after W27b the X `Close`. */
const citationsClose = (page: Page) => citationsHeading(page).locator('xpath=following-sibling::button[1]')
const generatedCard = (page: Page, title: string) =>
  page.getByRole('heading', { name: title, level: 4 }).locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " space-y-2 ")][1]')

test('L11 Knowledge (RAG on) › Generate → a case\'s Citations → another case\'s replaces it → close; nothing sent by the citations', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=knowledge')
  await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible()
  await networkQuiet(page, api)
  await page.getByPlaceholder(/^Describe what test cases to generate/).fill('Coupons and tax')
  await page.getByRole('button', { name: 'Generate Test Cases' }).click()
  await expect(page.getByText('Generated 2 test cases', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${CASES}/rag-generate`, body: { project_id: P, prompt_text: 'Coupons and tax', source_ids: [], persist: true } },
  ])
  const from = observed(api).length
  const [first, second] = RAG_ANSWER.test_cases

  await expect(citations(page)).toHaveCount(0)
  await generatedCard(page, first.title).getByRole('button', { name: 'Citations' }).click()
  await expect(citations(page)).toHaveCount(1)
  await expect(citationsHeading(page)).toHaveText('Citations (2)')
  await expect(citations(page).getByText('Checkout requirements')).toBeVisible()
  await expect(citations(page).getByText('Cart persistence RFC')).toBeVisible()
  await expect(citations(page).getByRole('link', { name: 'Open source' }).first()).toHaveAttribute('href', 'https://wiki.example.test/checkout')

  // The other case's Citations (keyboard: today the edge panel covers that button) replaces the first.
  const other = generatedCard(page, second.title).getByRole('button', { name: 'Citations' })
  await other.focus()
  await page.keyboard.press('Enter')
  await expect(citations(page)).toHaveCount(1)
  await expect(citationsHeading(page)).toHaveText('Citations (1)')
  await expect(citations(page).getByText('Tax rules')).toBeVisible()
  await expect(citations(page).getByText('Checkout requirements')).toHaveCount(0)

  await citationsClose(page).click()
  await expect(citations(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(observed(api).slice(from), 'the citations come with the answer: nothing is read').toEqual([])
  expect(world.writes).toHaveLength(1)
  assertHermetic(api, errors)
})

test('role gating: All Projects (admin) has no New test case, AI Generate, New Plan, Add test suite or Generate Strategy', async ({ page }) => {
  const world = testLibraryWorld()
  const { api, errors } = await openProductionPage(page, '/test-management', {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: world.handlers,
    ready,
  })
  await expect(page.locator('[data-primary] tbody tr').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'New test case from catalog toolbar' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'AI Generate', exact: true })).toHaveCount(0)
  await page.goto('/test-management?tab=plans')
  await expect(page.getByText('No test plans').or(page.getByRole('heading', { name: TM_PLAN_NAME }))).toBeVisible()
  await expect(page.getByRole('button', { name: 'New Plan' })).toHaveCount(0)
  await page.goto('/test-management?tab=suites')
  await expect(page.getByText(/pick a project to add a new suite/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add test suite' })).toHaveCount(0)
  await page.goto('/test-management?tab=strategy')
  await expect(page.getByText('No test strategy')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate Strategy' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('role gating: a QA engineer has New test case but no Add test suite', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management?tab=suites', { user: QA_ENGINEER })
  await expect(page.getByText('2 suites')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add test suite' })).toHaveCount(0)
  await page.goto('/test-management')
  await expect(page.getByRole('button', { name: 'New test case from catalog toolbar' })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
