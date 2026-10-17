/**
 * W27a PAN-04 CHARACTERIZATION, `/my-failures` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.4, rows
 * M1, M2), hermetic. Written against the MODAL tree (Reassign and Update triage status in modal dialogs that have
 * NO accessible name today, §0.23) and kept green, UNEDITED, after the in-page conversion: each form is found by
 * its heading, today's dialog or tomorrow's container named by it (`openedByHeading`).
 *
 *  - M1 Reassign (QA lead) → the reassignment options read only once the form opens → the suite owner
 *    preselected, another person picked → `PUT /api/v1/me/assigned-failures/{id}/reassign`
 *    `{ new_assignee_user_id }`, `Failure reassigned`, the inbox (list and counts) re-reads without the row;
 *    Cancel sends nothing;
 *  - M2 Update status → a status (+ notes) → `PUT …/{id}/triage` `{ status, notes }` (notes trimmed, `null`
 *    when blank), the toast, the inbox re-reads without the row; Cancel and the X send nothing;
 *  - role gating: a QA engineer gets Update status but no Reassign.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `inboxInPageWorld()`
 * (`fixtures-in-page.ts`, over `inboxHandlers()`).
 */
import { expect, test, type Page } from '@playwright/test'
import { openedByHeading } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { inboxInPageWorld, REASSIGN_OPTIONS } from '../visual/production/fixtures-in-page'
import { QA_ENGINEER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Api = Parameters<typeof requestsTo>[0]
const ready = (p: Page) => p.locator('[data-page-header] h1')
const FAILURE = { id: '00000000-0000-4000-8000-000000000403', name: 'test_checkout_step_3' }
const OTHER = { id: '00000000-0000-4000-8000-000000000405', name: 'test_checkout_step_5' }
const ME = '/api/v1/me/assigned-failures'
/** A failure's own row (never a form row a conversion might put under it). */
const row = (page: Page, name: string) =>
  page.locator('tbody tr:not([data-in-page-row])').filter({ has: page.getByText(name, { exact: true }) })
const listReads = (api: Api) => requestsTo(api, ME).length
const countReads = (api: Api) => requestsTo(api, `${ME}/count`).length

test('M1 Reassign: the options read on open, the suite owner preselected, another person → the PUT; the inbox re-reads without the row', async ({ page }) => {
  const world = inboxInPageWorld()
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready })
  const OPTIONS = `${ME}/${FAILURE.id}/reassign-options`
  await expect(row(page, FAILURE.name)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, OPTIONS), 'no options until the form opens').toEqual([])

  // Cancel: nothing.
  await row(page, FAILURE.name).getByRole('button', { name: 'Reassign' }).click()
  let form = openedByHeading(page, 'Reassign failure')
  await expect(form).toBeVisible()
  await expect(form.getByText(FAILURE.name)).toBeVisible()
  await expect(form.getByRole('radio', { name: /Dana Whitfield/ }), 'the suite owner is preselected').toBeChecked()
  expect(requestsTo(api, OPTIONS)).toEqual([`GET ${OPTIONS}`])
  await form.getByRole('radio', { name: /Priya Nair/ }).check()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const lists = listReads(api)
  const counts = countReads(api)
  await row(page, FAILURE.name).getByRole('button', { name: 'Reassign' }).click()
  form = openedByHeading(page, 'Reassign failure')
  await form.getByRole('radio', { name: /Priya Nair/ }).check()
  await form.getByRole('button', { name: 'Reassign', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Failure reassigned')).toBeVisible()
  await expect(row(page, FAILURE.name), 'the row left the inbox').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PUT ${ME}/${FAILURE.id}/reassign`, body: { new_assignee_user_id: REASSIGN_OPTIONS.engineers[0].user_id } },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)
  expect(countReads(api), 'the counts re-read').toBeGreaterThan(counts)

  // Straight away: the preselected suite owner.
  await row(page, OTHER.name).getByRole('button', { name: 'Reassign' }).click()
  form = openedByHeading(page, 'Reassign failure')
  await expect(form.getByRole('radio', { name: /Dana Whitfield/ })).toBeChecked()
  await form.getByRole('button', { name: 'Reassign', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `PUT ${ME}/${OTHER.id}/reassign`, body: { new_assignee_user_id: REASSIGN_OPTIONS.suiteOwner.user_id } },
  ])
  assertHermetic(api, errors)
})

test('M2 Update status: a status and trimmed notes → the triage PUT; blank notes travel as null; Cancel and the X send nothing', async ({ page }) => {
  const world = inboxInPageWorld()
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready })
  const open = (name: string) => row(page, name).getByRole('button', { name: 'Update status' }).click()

  await open(FAILURE.name)
  let form = openedByHeading(page, 'Update triage status')
  await expect(form).toBeVisible()
  await expect(form.getByRole('radio', { name: /^Reviewed & approved/ })).toBeChecked()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await open(FAILURE.name)
  form = openedByHeading(page, 'Update triage status')
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const lists = listReads(api)
  await open(FAILURE.name)
  form = openedByHeading(page, 'Update triage status')
  await form.getByRole('radio', { name: /^Defect created/ }).check()
  await expect(form.getByLabel(/^Notes/)).toHaveAttribute('placeholder', 'https://your-jira/BUG-1234')
  await form.getByLabel(/^Notes/).fill('  https://jira.example.test/browse/SHOP-77  ')
  await form.getByRole('button', { name: 'Update status' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Status updated to defect created')).toBeVisible()
  await expect(row(page, FAILURE.name), 'the row left the inbox').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PUT ${ME}/${FAILURE.id}/triage`, body: { status: 'DEFECT_CREATED', notes: 'https://jira.example.test/browse/SHOP-77' } },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)

  // The default status and no notes: `notes: null`.
  await open(OTHER.name)
  form = openedByHeading(page, 'Update triage status')
  await form.getByRole('button', { name: 'Update status' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: `PUT ${ME}/${OTHER.id}/triage`, body: { status: 'REVIEWED_APPROVED', notes: null } }])
  assertHermetic(api, errors)
})

test('role gating: a QA engineer gets Update status and no Reassign', async ({ page }) => {
  const world = inboxInPageWorld()
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready, user: QA_ENGINEER })
  await expect(row(page, FAILURE.name).getByRole('button', { name: 'Update status' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reassign' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/reassign-options$/)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
