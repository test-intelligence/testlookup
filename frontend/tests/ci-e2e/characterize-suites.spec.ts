/**
 * W27b PAN-05a CHARACTERIZATION, `/suites` and `/suites/:id` (`docs/deferred-scope-2026-10-13/work/W27b-contract.md`
 * §2.1, rows L16-L19), hermetic. Written against the MODAL tree (New suite, Move and the bulk Move in modal dialogs,
 * a suite's Delete behind a native `confirm()`) and kept green, UNEDITED, after the in-page conversion: each form
 * is found by its kept name (`opened`), the question is answered whichever shape asks it (`answerConfirm`), and
 * only named controls are driven (the dialogs' X has no name today and Escape does nothing in them: neither is used).
 *
 *  - L16 New suite (QA lead, one project) → `POST /api/v1/suites` `{ project_id, name (trimmed), description
 *    (trimmed) || null }`, `Suite "<name>" created in <project>`, the list re-read with the new row; Cancel sends
 *    nothing; All Projects (admin): the project picker, Create disabled until a project is picked;
 *  - L17 a suite's Delete (QA lead): dismissed → nothing; accepted → `DELETE /api/v1/suites/{id}`,
 *    `Suite "<name>" deleted`, the list re-read without it; the default suite has no Delete;
 *  - L18 `/suites/:id` a row's Move → the target suite → `POST /api/v1/canonical-test-cases/{id}/link`
 *    `{ test_suite_id }`, `Moved "<test>" to <suite>`, the suites re-read; Cancel sends nothing;
 *  - L19 two rows selected → the bar's Move → `POST /api/v1/canonical-test-cases/bulk-link`
 *    `{ target_test_suite_id, canonical_ids }` (selection order), the joined toast, the selection cleared;
 *  - role gating: a QA engineer creates and moves but has no Delete; a viewer has no New suite, no Move, no
 *    selection; nothing is sent.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `suitesInPageWorld()`
 * (`fixtures-in-page-b.ts`, over `SUITE_DETAIL_ON`).
 */
import { expect, test, type Page } from '@playwright/test'
import { answerConfirm, opened } from '../lib/inPage'
import { openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, SECOND_SUITE_ID, SUITE, SUITE_ID } from '../visual/production/fixtures'
import { LEGACY_SUITE_ID, LEGACY_SUITE_NAME, suitesInPageWorld } from '../visual/production/fixtures-in-page-b'
import { ADMIN, QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Api = Parameters<typeof requestsTo>[0]
const listReady = (p: Page) => p.getByRole('heading', { name: 'Test Suites', level: 1 })
const testsReady = (p: Page) => p.locator('[data-suite-tests]')
/** A suite's own row on `/suites` (never a form or strip row a conversion puts under it). */
const suiteRow = (page: Page, name: string) =>
  page.locator('[data-primary] tbody tr:not([data-in-page-row])').filter({ has: page.locator('[data-suite-name]', { hasText: new RegExp(`^${name}$`) }) })
/** A test's own row on the suite page's Tests tab. */
const testRow = (page: Page, name: string) =>
  page.locator('[data-suite-tests] tbody tr:not([data-in-page-row])').filter({ has: page.getByText(name, { exact: true }) })
const listReads = (api: Api) => requestsTo(api, '/api/v1/suites').length
const CATALOG = (i: number) => `88888888-8888-4888-8888-00000000000${i}`

test('L16 New suite: Cancel sends nothing; a trimmed name and description → the POST, the toast, the new row', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, '/suites', { handlers: world.handlers, ready: listReady })
  await expect(suiteRow(page, SUITE)).toBeVisible()
  await networkQuiet(page, api)

  await page.getByRole('button', { name: 'New suite' }).click()
  let form = opened(page, 'New Test Suite')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Project'), 'one project: no picker').toHaveCount(0)
  await form.getByLabel('Name').fill('Payments API')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = listReads(api)
  await page.getByRole('button', { name: 'New suite' }).click()
  form = opened(page, 'New Test Suite')
  await expect(form.getByLabel('Name'), 'a reopened form is empty').toHaveValue('')
  const create = form.getByRole('button', { name: 'Create suite' })
  await expect(create, 'no name: Create disabled').toBeDisabled()
  await form.getByLabel('Name').fill('   ')
  await expect(create, 'spaces are no name').toBeDisabled()
  await form.getByLabel('Name').fill('  Payments API  ')
  await form.getByLabel('Description (optional)').fill('  Card and wallet endpoints  ')
  await create.click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Suite "Payments API" created in Checkout', { exact: true })).toBeVisible()
  await expect(suiteRow(page, 'Payments API')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/suites', body: { project_id: PROJECT_ID, name: 'Payments API', description: 'Card and wallet endpoints' } },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(reads)

  // A blank description travels as null.
  await page.getByRole('button', { name: 'New suite' }).click()
  form = opened(page, 'New Test Suite')
  await form.getByLabel('Name').fill('Smoke')
  await form.getByLabel('Description (optional)').fill('   ')
  await form.getByRole('button', { name: 'Create suite' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: 'POST /api/v1/suites', body: { project_id: PROJECT_ID, name: 'Smoke', description: null } }])
  assertHermetic(api, errors)
})

test('L16 All Projects (admin): the project picker, Create disabled until a project is picked, then the POST', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openProductionPage(page, '/suites', {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: world.handlers,
    ready: listReady,
  })
  await expect(suiteRow(page, SUITE)).toBeVisible()
  await networkQuiet(page, api)
  await page.getByRole('button', { name: 'New suite' }).click()
  const form = opened(page, 'New Test Suite')
  await expect(form).toBeVisible()
  const picker = form.getByLabel('Project')
  await expect(picker).toHaveValue('')
  await form.getByLabel('Name').fill('Cross-project smoke')
  const create = form.getByRole('button', { name: 'Create suite' })
  await expect(create, 'no project picked: Create disabled').toBeDisabled()
  expect(world.writes).toEqual([])
  await picker.selectOption(PROJECT_ID)
  await create.click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Suite "Cross-project smoke" created in Checkout', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/suites', body: { project_id: PROJECT_ID, name: 'Cross-project smoke', description: null } },
  ])
  assertHermetic(api, errors)
})

test('L17 Delete (QA lead): dismissed sends nothing; accepted → the DELETE, the toast, the list re-read without it', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, '/suites', { handlers: world.handlers, ready: listReady })
  await expect(suiteRow(page, LEGACY_SUITE_NAME)).toBeVisible()
  await expect(suiteRow(page, 'All Tests').getByRole('button', { name: 'Delete' }), 'the default suite has no Delete').toHaveCount(0)
  await networkQuiet(page, api)

  const trigger = () => suiteRow(page, LEGACY_SUITE_NAME).getByRole('button', { name: 'Delete' })
  const asked = await answerConfirm(page, trigger(), 'dismiss')
  expect(asked.message).toBe(`Delete suite "${LEGACY_SUITE_NAME}"? Test cases must already be moved out.`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await expect(page, 'the row click did not run: still on the list').toHaveURL(/\/suites$/)
  await expect(suiteRow(page, LEGACY_SUITE_NAME)).toBeVisible()

  const reads = listReads(api)
  await answerConfirm(page, trigger(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText(`Suite "${LEGACY_SUITE_NAME}" deleted`, { exact: true })).toBeVisible()
  await expect(suiteRow(page, LEGACY_SUITE_NAME)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/suites/${LEGACY_SUITE_ID}`, body: null }])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(reads)
  await expect(page).toHaveURL(/\/suites$/)
  assertHermetic(api, errors)
})

test('L18 a row\'s Move: Cancel sends nothing; a target suite → the link POST, the toast, the suites re-read', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, `/suites/${SUITE_ID}`, { handlers: world.handlers, ready: testsReady })
  await expect(testRow(page, 'token refresh')).toBeVisible()
  await networkQuiet(page, api)

  const move = () => testRow(page, 'token refresh').getByRole('button', { name: 'Move', exact: true }).click()
  await move()
  let form = opened(page, 'Move test case')
  await expect(form).toBeVisible()
  await expect(form.getByText('AuthSpec :: token refresh')).toBeVisible()
  const target = form.getByLabel('Target suite')
  await expect(target, 'the first other suite is preselected').toHaveValue(SECOND_SUITE_ID)
  await expect(target.locator('option')).toHaveText(['All Tests (default)', LEGACY_SUITE_NAME])
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = listReads(api)
  const suiteReads = requestsTo(api, `/api/v1/suites/${SUITE_ID}`).length
  await move()
  form = opened(page, 'Move test case')
  await form.getByLabel('Target suite').selectOption(LEGACY_SUITE_ID)
  await form.getByRole('button', { name: 'Move', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Moved "token refresh" to ${LEGACY_SUITE_NAME}`, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/canonical-test-cases/${CATALOG(2)}/link`, body: { test_suite_id: LEGACY_SUITE_ID } }])
  expect(listReads(api), 'the suites list re-reads').toBeGreaterThan(reads)
  expect(requestsTo(api, `/api/v1/suites/${SUITE_ID}`).length, 'the suite re-reads').toBeGreaterThan(suiteReads)
  assertHermetic(api, errors)
})

test('L19 two selected → the bar\'s Move: Cancel sends nothing; Move 2 → the bulk-link POST in selection order, the toast, the selection cleared', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, `/suites/${SUITE_ID}`, { handlers: world.handlers, ready: testsReady })
  await expect(testRow(page, 'logout clears session')).toBeVisible()
  await networkQuiet(page, api)

  await page.getByRole('checkbox', { name: 'Select logout clears session' }).check()
  await page.getByRole('checkbox', { name: 'Select login succeeds' }).check()
  const bar = page.getByRole('region', { name: 'Bulk actions' })
  await expect(bar).toContainText('2 selected')
  await bar.getByRole('button', { name: 'Move', exact: true }).click()
  let form = opened(page, 'Move 2 test cases')
  await expect(form).toBeVisible()
  await expect(form.getByText('All selected cases will be moved to the target suite within the same project.')).toBeVisible()
  await expect(form.getByLabel('Target suite')).toHaveValue(SECOND_SUITE_ID)
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await expect(bar, 'Cancel keeps the selection').toContainText('2 selected')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = listReads(api)
  await bar.getByRole('button', { name: 'Move', exact: true }).click()
  form = opened(page, 'Move 2 test cases')
  await form.getByLabel('Target suite').selectOption(LEGACY_SUITE_ID)
  await form.getByRole('button', { name: 'Move 2', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Moved 2 of 2 to ${LEGACY_SUITE_NAME}`, { exact: true })).toBeVisible()
  await expect(bar, 'the selection is cleared').toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: 'Select logout clears session' })).not.toBeChecked()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/canonical-test-cases/bulk-link',
      body: { target_test_suite_id: LEGACY_SUITE_ID, canonical_ids: [CATALOG(3), CATALOG(0)] },
    },
  ])
  expect(listReads(api), 'the suites re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('role gating: a QA engineer has New suite and Move but no Delete', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, '/suites', { handlers: world.handlers, ready: listReady, user: QA_ENGINEER })
  await expect(suiteRow(page, LEGACY_SUITE_NAME)).toBeVisible()
  await expect(page.getByRole('button', { name: 'New suite' })).toBeVisible()
  await expect(page.locator('[data-primary]').getByRole('button', { name: 'Delete' }), 'Delete is QA lead').toHaveCount(0)
  await page.goto(`/suites/${SUITE_ID}`)
  await expect(testsReady(page)).toBeVisible()
  await expect(testRow(page, 'token refresh').getByRole('button', { name: 'Move', exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('role gating: a viewer has no New suite, no Delete, no Move and no selection; nothing is sent', async ({ page }) => {
  const world = suitesInPageWorld()
  const { api, errors } = await openRollout(page, '/suites', { handlers: world.handlers, ready: listReady, user: VIEWER })
  await expect(suiteRow(page, LEGACY_SUITE_NAME)).toBeVisible()
  await expect(page.getByRole('button', { name: 'New suite' })).toHaveCount(0)
  await expect(page.locator('[data-primary]').getByRole('button', { name: 'Delete' })).toHaveCount(0)
  await page.goto(`/suites/${SUITE_ID}`)
  await expect(testRow(page, 'token refresh')).toBeVisible()
  await expect(page.locator('[data-suite-tests]').getByRole('button', { name: 'Move', exact: true })).toHaveCount(0)
  await expect(page.locator('[data-suite-tests]').getByRole('checkbox')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(requestsTo(api, /^\/api\/v1\/canonical-test-cases\//)).toEqual([])
  assertHermetic(api, errors)
})
