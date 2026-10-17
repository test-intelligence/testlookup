/**
 * W06 DEF-01 + DEF-02, hermetic (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.1, §2.2), on `/defects`
 * as an OFFLINE install answers it (the demonstrated default):
 *
 *  - the Owner column: the assigned person with the team under it, the defect's own team, a team from the
 *    ownership rules (marked so), and "Unassigned";
 *  - a QA engineer gets "⋯ Defect actions" on every row; Change status… → In progress → Save: the PATCH body is
 *    exactly `{"resolution_status":"IN_PROGRESS"}`, the toast says so, and the row moves to the In progress tab
 *    without a reload;
 *  - Assign… → Priya Nair → Save: the PATCH carries only `assignee_user_id`, and the Owner cell reads Priya Nair;
 *  - Link Jira issue… → `abc-123` (uppercased as typed) → Save: the PATCH carries `jira_key: "ABC-123"`, the toast
 *    says it was saved locally (offline mode), and the Jira cell links the server-built
 *    `https://jira.example.test/browse/ABC-123`; the Jira metadata is asked for only once the dialog opens;
 *  - a VIEWER and a TESTER see no menu (and still the "Open … in Jira" link), and write nothing.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-defects.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { DEFECT_ID, defectsWorld, QA_ENGINEER, RELEASE_PEOPLE, TESTER, VIEWER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Defect KPIs')

/** The table's columns (contract §4.2): Key · Title · Severity · Status · Owner · Jira · Age · Actions. */
const COLUMNS = ['Key', 'Title', 'Severity', 'Status', 'Owner', 'Jira', 'Age', 'Actions']
const col = (name: string) => COLUMNS.indexOf(name)

const table = (page: Page) => page.getByRole('table', { name: 'Defects' })
/** The body row of the defect whose test is `test` (the title cell shows it). */
const row = (page: Page, testName: string) => table(page).locator('tbody tr').filter({ hasText: testName })
const cell = (page: Page, testName: string, column: string) => row(page, testName).getByRole('cell').nth(col(column))
const statusTab = (page: Page, label: string) =>
  page.getByRole('tablist', { name: 'Status filter' }).getByRole('tab', { name: new RegExp(`^${label}`) })

async function openMenuItem(page: Page, testName: string, item: string) {
  await row(page, testName).getByRole('button', { name: 'Defect actions' }).click()
  await page.getByRole('menuitem', { name: item }).click()
}

const patchLine = (id: string) => `PATCH /api/v1/projects/${P}/defects/${id}`

test('the Owner column: the assignee with the team, the defect\'s own team, a team from the ownership rules, and Unassigned', async ({ page }) => {
  const { handlers } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user: QA_ENGINEER })
  // A sortable header carries its arrow after the name ("Age ↓").
  await expect(table(page).locator('thead th')).toHaveText(COLUMNS.map((name) => new RegExp(`^${name}\\b`, 'i')))
  // An assignee, with the defect's own team under the name.
  await expect(cell(page, 'checkout total includes tax', 'Owner')).toHaveText(/^Dana Whitfield\s*checkout-team$/)
  // No assignee: the team, and where it came from (nothing for the defect's own team).
  await expect(cell(page, 'refund webhook retried', 'Owner')).toHaveText('payments-team')
  await expect(cell(page, 'card declined shows reason', 'Owner')).toHaveText(/^payments-team\s*from ownership rules$/)
  await expect(cell(page, 'login rejects bad password', 'Owner')).toHaveText(/^identity-team\s*from the component map$/)
  await expect(cell(page, 'token refresh', 'Owner')).toHaveText(/^auth-squad\s*from the test's owner label$/)
  // Nobody at all.
  await expect(cell(page, 'search paginates', 'Owner')).toHaveText('Unassigned')
  // Owners are searchable (D-15).
  await page.getByRole('searchbox', { name: 'Search defects' }).fill('identity-team')
  await expect(table(page).locator('tbody tr')).toHaveCount(1)
  await expect(row(page, 'login rejects bad password')).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Change status… → In progress: the PATCH body is {"resolution_status":"IN_PROGRESS"} and the row moves to the In progress tab', async ({ page }) => {
  const { handlers, writes } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user: QA_ENGINEER })
  // A QA engineer gets the menu on every row (each row carries its project).
  await expect(table(page).getByRole('button', { name: 'Defect actions' })).toHaveCount(9)
  await expect(statusTab(page, 'In progress')).toHaveText('In progress1')

  await openMenuItem(page, 'search paginates', 'Change status…')
  const dialog = opened(page, 'Change status')
  await expect(dialog.getByRole('radio', { name: 'Open', exact: true })).toBeChecked()
  const save = dialog.getByRole('button', { name: 'Save' })
  await expect(save, 'nothing to save until another status is picked').toBeDisabled()
  await dialog.getByRole('radio', { name: 'In progress', exact: true }).check()
  await save.click()
  await expect(dialog).toHaveCount(0)
  // Not linked to Jira: no write-back clause in the toast.
  await expect(page.getByText('Status changed to In progress', { exact: true })).toBeVisible()

  expect(writes).toEqual([{ line: patchLine(DEFECT_ID.searchPaginates), body: { resolution_status: 'IN_PROGRESS' } }])
  await expect(cell(page, 'search paginates', 'Status')).toHaveText('In progress')
  await expect(statusTab(page, 'In progress')).toHaveText('In progress2')
  await statusTab(page, 'In progress').click()
  await expect(row(page, 'search paginates')).toBeVisible()
  await statusTab(page, 'Open').click()
  await expect(row(page, 'search paginates')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes, 'one PATCH, nothing else written').toHaveLength(1)
  assertHermetic(api, errors)
})

test('a linked defect\'s status change is saved and, offline, not written back to Jira', async ({ page }) => {
  const { handlers, writes } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user: QA_ENGINEER })
  await openMenuItem(page, 'login rejects bad password', 'Change status…')
  const dialog = opened(page, 'Change status')
  await dialog.getByRole('radio', { name: 'Resolved', exact: true }).check()
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Status changed to Resolved · Jira not updated (offline)', { exact: true })).toBeVisible()
  expect(writes).toEqual([{ line: patchLine(DEFECT_ID.loginRejects), body: { resolution_status: 'RESOLVED' } }])
  await expect(cell(page, 'login rejects bad password', 'Status')).toHaveText('Resolved')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Assign… → Priya Nair: the PATCH carries only assignee_user_id and the Owner cell reads Priya Nair', async ({ page }) => {
  const { handlers, writes } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user: QA_ENGINEER })
  await openMenuItem(page, 'search paginates', 'Assign…')
  const dialog = opened(page, 'Assign defect')
  const assignee = dialog.getByLabel('Assignee')
  await expect(assignee).toHaveValue('')
  const offered = await assignee.locator('option').allTextContents()
  expect(offered[0]).toBe('Unassigned')
  expect(offered).toEqual(expect.arrayContaining(['Priya Nair', 'Dana Whitfield', 'QA Lead']))
  // Never the synthetic QA lead, a service account or a deactivated user (the server refuses them too).
  for (const refused of ['Checkout QA lead (system)', 'CI bot', 'Old Contractor']) expect(offered).not.toContain(refused)

  await assignee.selectOption({ label: 'Priya Nair' })
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('Assigned to Priya Nair', { exact: true })).toBeVisible()
  // The team was left alone, so it is not sent.
  expect(writes).toEqual([{ line: patchLine(DEFECT_ID.searchPaginates), body: { assignee_user_id: RELEASE_PEOPLE.priya.id } }])
  await expect(cell(page, 'search paginates', 'Owner')).toHaveText('Priya Nair')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Link Jira issue… → ABC-123 offline: saved locally, linked to the configured domain, the metadata read only once it opens', async ({ page }) => {
  const { handlers, writes } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user: QA_ENGINEER })
  await expect(cell(page, 'search paginates', 'Jira')).toHaveText('unlinked')
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/defects\/jira\/metadata$/), 'no Jira metadata until the dialog opens').toEqual([])

  await openMenuItem(page, 'search paginates', 'Link Jira issue…')
  const dialog = opened(page, 'Link a Jira issue')
  await expect(dialog.getByText('Jira is not reachable from here (offline mode), so the key is saved without a check.')).toBeVisible()
  const key = dialog.getByLabel('Jira issue key')
  const save = dialog.getByRole('button', { name: 'Save' })
  await expect(key).toHaveAttribute('placeholder', 'ABC-123')
  await key.fill('abc-1x')
  await expect(save, 'not a Jira key').toBeDisabled()
  await key.fill('abc-123')
  await expect(key, 'uppercased as typed').toHaveValue('ABC-123')
  await expect(save).toBeEnabled()
  await save.click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('Linked to ABC-123 — saved locally (offline mode)', { exact: true })).toBeVisible()

  expect(writes).toEqual([{ line: patchLine(DEFECT_ID.searchPaginates), body: { jira_key: 'ABC-123' } }])
  const link = cell(page, 'search paginates', 'Jira').getByRole('link', { name: /ABC-123/ })
  await expect(link).toHaveAttribute('href', 'https://jira.example.test/browse/ABC-123')
  await expect(row(page, 'search paginates').getByRole('link', { name: 'Open ABC-123 in Jira' })).toHaveAttribute(
    'href',
    'https://jira.example.test/browse/ABC-123',
  )
  // Linked now: the menu offers to change it.
  await row(page, 'search paginates').getByRole('button', { name: 'Defect actions' }).click()
  await expect(page.getByRole('menuitem', { name: 'Change Jira issue…' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Link Jira issue…' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/defects\/jira\/metadata$/).length).toBeGreaterThan(0)
  assertHermetic(api, errors)
})

for (const user of [VIEWER, TESTER]) {
  test(`a ${user.role} sees no Defect actions, keeps the Jira link, and writes nothing`, async ({ page }) => {
    const { handlers, writes } = defectsWorld()
    const { api, errors } = await openRollout(page, '/defects', { handlers, ready, user })
    await expect(row(page, 'checkout total includes tax')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Defect actions' })).toHaveCount(0)
    await expect(row(page, 'checkout total includes tax').getByRole('link', { name: 'Open SHOP-812 in Jira' })).toBeVisible()
    await networkQuiet(page, api)
    expect(writes).toEqual([])
    expect(requestsTo(api, /\/members$/)).toEqual([])
    assertHermetic(api, errors)
  })
}
