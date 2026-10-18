/**
 * W06 DEF-06, hermetic (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.6, §4.5), on
 * `/my-failures?tab=approvals&source=defects` as an OFFLINE install answers it:
 *
 *  - a QA lead sees the Defects source with its count, and the pending defect with why it waits, the Jira project
 *    it would be filed in and the run it came from;
 *  - Approve → one POST `.../review` with `{"action":"approve"}`, the toast "Approved — Jira not filed (offline
 *    mode)", the row leaves, the chip's and the Approvals tab's counts drop by one;
 *  - Reject needs a reason (blank or spaces keep it disabled), sends it trimmed, and says the defect was closed;
 *  - a QA engineer and a viewer get no Defects source and never request the queue (it is a QA lead's: an
 *    unconditional fetch would toast a 403 at every lower role).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-pages.ts` (`inboxHandlers`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  inboxHandlers,
  PENDING_DEFECT_ID,
  PENDING_DEFECT_RUN_ID,
  PENDING_DEFECT_TITLE,
  type InboxWrite,
} from '../visual/production/fixtures-pages'
import { PROJECT_ID } from '../visual/production/fixtures'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const SOURCE_URL = '/my-failures?tab=approvals&source=defects'
const PENDING = '/api/v1/deep-investigate/defects/pending-review'
const REVIEW_LINE = `POST /api/v1/deep-investigate/defects/${PENDING_DEFECT_ID}/review`

const ready = (p: Page) => p.locator('[data-page-header] h1')
const defectsChip = (page: Page) => page.locator('[data-chip-option="defects"]')
const section = (page: Page) => page.locator('[data-primary]').getByRole('region', { name: 'Defects' })
const defectRow = (page: Page) => section(page).getByTestId('defect-approval-row')
const approvalsTab = (page: Page) => page.getByRole('tab', { name: /^Approvals/ })

test('a QA lead: the Defects source lists the pending defect, with why it waits, its Jira project and its run', async ({ page }) => {
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: inboxHandlers(), ready })
  await expect(defectsChip(page)).toHaveAttribute('aria-checked', 'true')
  await expect(defectsChip(page)).toHaveText('Defects 1')
  // 1 report + 2 proposals + 2 cases + 1 defect.
  await expect(approvalsTab(page)).toHaveText('Approvals6')
  await expect(defectRow(page)).toHaveCount(1)
  const row = defectRow(page)
  await expect(row).toContainText(PENDING_DEFECT_TITLE)
  await expect(row).toContainText('HIGH')
  await expect(row).toContainText('payments-api · payments-team')
  await expect(row).toContainText('Waiting because:')
  await expect(row).toContainText('Jira ticket creation requires human approval')
  await expect(row).toContainText('Severity HIGH requires human review')
  await expect(row).toContainText('Jira project QA')
  await expect(row).toContainText('created 2d ago')
  await expect(row.getByRole('link', { name: 'Open run' })).toHaveAttribute('href', `/runs/${PENDING_DEFECT_RUN_ID}?tab=analysis`)
  await expect(section(page).getByRole('link', { name: 'Open Defects' })).toHaveAttribute('href', /^\/defects/)
  // One source at a time: the other three are not shown.
  for (const other of ['AI reports', 'Quarantine proposals', 'Test cases']) {
    await expect(page.locator('[data-primary]').getByRole('region', { name: other })).toHaveCount(0)
  }
  await networkQuiet(page, api)
  // The project's queue, the first page of 100 (contract §1.8).
  expect(requestsTo(api, PENDING)).toContain(`GET ${PENDING}?project_id=${PROJECT_ID}&page=1&page_size=100`)
  assertHermetic(api, errors)
})

test('Approve: one POST {"action":"approve"}, "Approved — Jira not filed (offline mode)", the row leaves and the counts drop', async ({ page }) => {
  const writes: InboxWrite[] = []
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: inboxHandlers(writes), ready })
  await expect(defectRow(page)).toHaveCount(1)
  await expect(approvalsTab(page)).toHaveText('Approvals6')

  await defectRow(page).getByTestId('defect-approve').click()
  await expect(page.getByText('Approved — Jira not filed (offline mode)', { exact: true })).toBeVisible()
  await expect(defectRow(page)).toHaveCount(0)
  await expect(section(page).getByText('No defects are waiting for approval.')).toBeVisible()
  await expect(defectsChip(page)).toHaveText('Defects 0')
  await expect(approvalsTab(page)).toHaveText('Approvals5')
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: REVIEW_LINE, body: { action: 'approve' } }])
  assertHermetic(api, errors)
})

test('Reject needs a reason: disabled while blank, then sent trimmed; the defect is closed as Invalid', async ({ page }) => {
  const writes: InboxWrite[] = []
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: inboxHandlers(writes), ready })
  await defectRow(page).getByTestId('defect-reject').click()
  const dialog = opened(page, 'Reject this defect?')
  await expect(dialog).toContainText('It is closed as Invalid and stops counting toward the release gate.')
  const reason = dialog.getByLabel(/^Reason \(recorded in the activity log\)/)
  const confirm = dialog.getByRole('button', { name: 'Reject', exact: true })
  await expect(confirm).toBeDisabled()
  await reason.fill('   ')
  await expect(confirm, 'spaces are no reason').toBeDisabled()
  expect(writes).toEqual([])
  await reason.fill('  Duplicate of SHOP-812  ')
  await confirm.click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('Rejected — closed as Invalid', { exact: true })).toBeVisible()
  await expect(defectRow(page)).toHaveCount(0)
  await expect(defectsChip(page)).toHaveText('Defects 0')
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: REVIEW_LINE, body: { action: 'reject', reason: 'Duplicate of SHOP-812' } }])
  assertHermetic(api, errors)
})

for (const user of [QA_ENGINEER, VIEWER]) {
  test(`a ${user.role} gets no Defects source and never requests the queue`, async ({ page }) => {
    const writes: InboxWrite[] = []
    const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: inboxHandlers(writes), ready, user })
    await expect(approvalsTab(page)).toHaveAttribute('aria-selected', 'true')
    // `?source=defects` reads as All for them; its count (1 report + 2 proposals + 2 cases) never waits on the queue.
    const sources = page.getByRole('radiogroup', { name: 'Approval source' })
    await expect(sources.locator('[data-chip-option="all"]')).toHaveAttribute('aria-checked', 'true')
    await expect(sources.locator('[data-chip-option="all"]')).toHaveText('All 5')
    await expect(defectsChip(page)).toHaveCount(0)
    await expect(section(page)).toHaveCount(0)
    await expect(page.getByTestId('defect-approval-row')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, PENDING), 'the queue is a QA lead\'s: never asked').toEqual([])
    expect(writes).toEqual([])
    assertHermetic(api, errors)
  })
}
