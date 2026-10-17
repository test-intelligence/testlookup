/**
 * W27b PAN-05a CHARACTERIZATION, the `TransitionReasonDialog` hosts outside the cases table
 * (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.1, rows L12-L15), hermetic. Written against the MODAL
 * tree (the reason form a modal dialog at the end of each host) and kept green, UNEDITED, after the in-page
 * conversion (the form at its control): each form is found by its kept name (`opened`), only named controls are
 * driven, Escape where the dialog has it today (`useModalFocus`).
 *
 *  - L12 Cases › Unconfirmed automation retirements › Confirm retirement (QA lead) → the reason →
 *    `POST /api/v1/canonical-test-cases/{id}/confirm-retirement` `{ reason }` (trimmed); a 409 shows the inline
 *    alert AND the toast and keeps the form; the retry hides the row, toasts and re-reads the list; Cancel and
 *    Escape send nothing; a QA engineer sees the list without the button;
 *  - L13 `/canonical-test-cases/:id` › Unlink managed case (QA engineer) → the reason →
 *    `DELETE …/{id}/managed-link` with `{ reason }`, `Managed case link removed`, the control gone, the suite
 *    re-read; Cancel and Escape send nothing; a viewer has no control;
 *  - L14 Inbox › Approvals › Test cases › Approve / Request changes → the notes → `POST …/review-action`
 *    `{ action, notes }` (legacy) / `POST …/transition` `{ action, expected_version, notes }` (v2); Claim review
 *    sends at once; the queues re-read;
 *  - L15 Inbox › Approvals › Defects › Reject: Cancel and Escape send nothing (the reject itself is pinned by
 *    `inbox-defect-approvals.spec.ts`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `lifecycleHostsWorld()`
 * (`fixtures-in-page-b.ts`, over `TEST_CASES`, `inboxHandlers()` and `SUITE_DETAIL`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, SUITE_ID } from '../visual/production/fixtures'
import {
  CANONICAL_ID,
  CANONICAL_NAME,
  lifecycleHostsWorld,
  ORPHANS,
  RETIREMENT_REFUSAL,
  type LifecycleHostsOptions,
} from '../visual/production/fixtures-in-page-b'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const headerReady = (p: Page) => p.locator('[data-page-header] h1')

async function open(page: Page, path: string, ready: (p: Page) => ReturnType<Page['locator']>, options: LifecycleHostsOptions & { user?: typeof QA_ENGINEER } = {}) {
  const world = lifecycleHostsWorld(options)
  const opened_ = await openRollout(page, path, {
    handlers: world.handlers,
    ready,
    user: options.user,
    flags: options.lifecycleV2 ? { test_case_lifecycle_v2: true } : {},
  })
  return { world, ...opened_ }
}

const orphanCard = (page: Page) => page.getByRole('region', { name: 'Execution evidence gaps' }).locator('article').filter({ hasText: 'Unconfirmed automation retirements' })
/** An orphan's own list item (never a form item a conversion puts after it). */
const orphanItem = (page: Page, name: string) => orphanCard(page).locator('li:not([data-in-page-row])').filter({ hasText: name })

test('L12 Confirm retirement (QA lead): Cancel and Escape send nothing; a 409 keeps the form with its alert and toast; the retry hides the row', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management', tmReady, { refuseFirstRetirement: true })
  await orphanCard(page).getByRole('button', { name: /^Unconfirmed automation retirements/ }).click()
  await expect(orphanItem(page, ORPHANS[0].test_name)).toBeVisible()
  await networkQuiet(page, api)
  // The card's toggle tracks inside a state updater, which React StrictMode (the dev server) runs twice.
  const opened_ = { event_name: 'test_case_evidence_list_opened', project_id: P, payload: { kind: 'orphaned' } }
  expect(world.tracked, 'opening the list is tracked, nothing else').toEqual([opened_, opened_])
  const confirm = () => orphanItem(page, ORPHANS[0].test_name).getByRole('button', { name: 'Confirm retirement' }).click()

  await confirm()
  let form = opened(page, 'Confirm automation retirement')
  await expect(form).toBeVisible()
  await expect(form).toContainText(`Confirm that ${ORPHANS[0].test_name} was intentionally removed. The canonical and managed records will be preserved.`)
  await expect(form.getByLabel('Reason')).toBeFocused()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await confirm()
  form = opened(page, 'Confirm automation retirement')
  await form.getByLabel('Reason').press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/canonical-test-cases/orphaned').length
  await confirm()
  form = opened(page, 'Confirm automation retirement')
  await form.getByLabel('Reason').fill('  Removed with the coupon redesign  ')
  await form.getByRole('button', { name: 'Confirm retirement' }).click()
  await expect(page.getByRole('alert').filter({ hasText: RETIREMENT_REFUSAL })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: RETIREMENT_REFUSAL }).first(), 'the toast says it too').toBeVisible()
  await expect(form, 'a refused confirmation keeps the form').toBeVisible()
  await form.getByRole('button', { name: 'Confirm retirement' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Automation retirement confirmed', { exact: true })).toBeVisible()
  await expect(orphanItem(page, ORPHANS[0].test_name), 'the confirmed row is hidden').toHaveCount(0)
  await expect(orphanItem(page, ORPHANS[1].test_name)).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: RETIREMENT_REFUSAL }), 'the alert clears on success').toHaveCount(0)
  await networkQuiet(page, api)
  const line = `POST /api/v1/canonical-test-cases/${ORPHANS[0].id}/confirm-retirement`
  expect(world.writes).toEqual([
    { line, body: { reason: 'Removed with the coupon redesign' } },
    { line, body: { reason: 'Removed with the coupon redesign' } },
  ])
  expect(requestsTo(api, '/api/v1/canonical-test-cases/orphaned').length, 'the list re-reads').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('L12 role gating: a QA engineer sees the unconfirmed retirements without Confirm retirement', async ({ page }) => {
  const { world, api, errors } = await open(page, '/test-management', tmReady, { user: QA_ENGINEER })
  await orphanCard(page).getByRole('button', { name: /^Unconfirmed automation retirements/ }).click()
  await expect(orphanItem(page, ORPHANS[0].test_name)).toBeVisible()
  await expect(orphanCard(page).getByRole('button', { name: 'Confirm retirement' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('L13 Unlink managed case (QA engineer): Cancel and Escape send nothing; a reason → the DELETE with its body, the toast, the suite re-read', async ({ page }) => {
  const { world, api, errors } = await open(page, `/canonical-test-cases/${CANONICAL_ID}`, headerReady, { user: QA_ENGINEER })
  await expect(headerReady(page)).toHaveText(CANONICAL_NAME)
  await networkQuiet(page, api)
  const unlink = () => page.getByRole('button', { name: 'Unlink managed case' }).click()

  await unlink()
  let form = opened(page, 'Unlink managed case')
  await expect(form).toBeVisible()
  await expect(form).toContainText('Explain why this canonical automation identity was linked to the wrong managed case. Both records will be preserved.')
  await expect(form.getByRole('button', { name: 'Unlink', exact: true }), 'no reason: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await unlink()
  form = opened(page, 'Unlink managed case')
  await form.getByLabel('Reason').press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const suiteReads = requestsTo(api, `/api/v1/suites/${SUITE_ID}`).length
  await unlink()
  form = opened(page, 'Unlink managed case')
  await form.getByLabel('Reason').fill('  Linked to the logout case by mistake ')
  await form.getByRole('button', { name: 'Unlink', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Managed case link removed', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Unlink managed case' }), 'the link is gone').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `DELETE /api/v1/canonical-test-cases/${CANONICAL_ID}/managed-link`, body: { reason: 'Linked to the logout case by mistake' } },
  ])
  expect(requestsTo(api, `/api/v1/suites/${SUITE_ID}`).length, 'the suite key re-reads').toBeGreaterThan(suiteReads)
  assertHermetic(api, errors)
})

test('L13 role gating: a viewer has no Unlink managed case', async ({ page }) => {
  const { world, api, errors } = await open(page, `/canonical-test-cases/${CANONICAL_ID}`, headerReady, { user: VIEWER })
  await expect(headerReady(page)).toHaveText(CANONICAL_NAME)
  await expect(page.getByRole('button', { name: 'Unlink managed case' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

const CASES_URL = '/my-failures?tab=approvals&source=cases'
const caseRow = (page: Page, name: string) =>
  page.locator('[data-primary]').getByRole('region', { name: 'Test cases' }).getByTestId('case-approval-row').filter({ hasText: name })
const queueReads = (api: Parameters<typeof requestsTo>[0]) =>
  requestsTo(api, '/api/v1/test-management/cases').filter((l) => l.includes('status=review_requested')).length

test('L14 Inbox › Test cases (legacy) › Approve: Cancel and Escape send nothing; notes → the review-action POST, the toast, the queues re-read', async ({ page }) => {
  const { world, api, errors } = await open(page, CASES_URL, headerReady)
  await expect(caseRow(page, 'Checkout case c1')).toBeVisible()
  await networkQuiet(page, api)
  const approve = () => caseRow(page, 'Checkout case c1').getByRole('button', { name: 'Approve', exact: true }).click()

  await approve()
  let form = opened(page, 'Approve')
  await expect(form).toBeVisible()
  await expect(form).toContainText('Record a nonblank review note for the audit trail.')
  await expect(form.getByLabel('Review notes')).toBeFocused()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await approve()
  form = opened(page, 'Approve')
  await form.getByLabel('Review notes').press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = queueReads(api)
  await approve()
  form = opened(page, 'Approve')
  await form.getByLabel('Review notes').fill('  Covers the refund path  ')
  await form.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case approve', { exact: true })).toBeVisible()
  await expect(caseRow(page, 'Checkout case c1'), 'the approved case left the queue').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/test-management/cases/c1/review-action', body: { action: 'approve', notes: 'Covers the refund path' } },
  ])
  expect(queueReads(api), 'the queues re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('L14 Inbox › Test cases (lifecycle v2): Claim review sends at once; Request changes → the notes → the transition POST with the version', async ({ page }) => {
  const { world, api, errors } = await open(page, CASES_URL, headerReady, { lifecycleV2: true })
  await expect(caseRow(page, 'Checkout case c2')).toBeVisible()
  await networkQuiet(page, api)

  await caseRow(page, 'Checkout case c1').getByRole('button', { name: 'Claim review', exact: true }).click()
  await expect(page.getByText('Test case claim review', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/test-management/cases/c1/transition', body: { action: 'claim_review', expected_version: 1 } },
  ])

  await caseRow(page, 'Checkout case c2').getByRole('button', { name: 'Request changes', exact: true }).click()
  const form = opened(page, 'Request changes')
  await form.getByLabel('Review notes').fill(' Step 3 has no expected result ')
  await form.getByRole('button', { name: 'Request changes', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test case request changes', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: 'POST /api/v1/test-management/cases/c2/transition', body: { action: 'request_changes', expected_version: 1, notes: 'Step 3 has no expected result' } },
  ])
  assertHermetic(api, errors)
})

test('L15 Inbox › Defects › Reject: Cancel and Escape send nothing and the defect stays', async ({ page }) => {
  const { api, errors } = await open(page, '/my-failures?tab=approvals&source=defects', headerReady)
  const row = page.locator('[data-primary]').getByRole('region', { name: 'Defects' }).getByTestId('defect-approval-row')
  await expect(row).toHaveCount(1)
  await networkQuiet(page, api)

  await row.getByTestId('defect-reject').click()
  let form = opened(page, 'Reject this defect?')
  await expect(form).toBeVisible()
  await expect(form.getByLabel(/^Reason \(recorded in the activity log\)/)).toBeFocused()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await row.getByTestId('defect-reject').click()
  form = opened(page, 'Reject this defect?')
  await form.getByLabel(/^Reason \(recorded in the activity log\)/).fill('typed, then Escape')
  await form.getByLabel(/^Reason \(recorded in the activity log\)/).press('Escape')
  await expect(form).toHaveCount(0)
  await expect(row, 'the defect stays in the queue').toHaveCount(1)
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/deep-investigate\/defects\/[^/]+\/review$/)).toEqual([])
  assertHermetic(api, errors)
})
