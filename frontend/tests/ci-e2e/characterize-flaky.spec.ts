/**
 * W27a PAN-04 CHARACTERIZATION, `/flaky`'s request tabs (`docs/deferred-scope-2026-10-13/work/W27a-contract.md`
 * §2.4, row Q2), hermetic, with `flaky_auto_quarantine` ON. Written against the MODAL tree (Create Jira issue in a
 * modal dialog over the quarantine table) and kept green, UNEDITED, after the in-page conversion: the form is
 * found by the name it keeps (`opened`).
 *
 *  - Q2 Quarantined → a row's File Jira → the preview read for THAT row's fingerprint only once the form opens →
 *    Create issue: `POST /api/v1/projects/{p}/defects/jira` with exactly `{ fingerprint, issue_type,
 *    jira_project_key, target }`; then the request list re-reads (`onCreated`) and the form closes; the same
 *    from a Proposed row; Cancel sends nothing;
 *  - role gating: a viewer sees the rows and no File Jira; a QA engineer may file one.
 *
 * Q1 (Propose quarantine from a Detected row) is `flaky-quarantine.spec.ts` and `fold-flaky.spec.ts`; the inline
 * decision forms (Approve / Release) are `flaky-quarantine.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `flakyJiraWorld()` (`fixtures-in-page.ts`,
 * over `flakyQuarantineWorld()`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { CREATED_JIRA_KEY, CREATED_JIRA_URL, flakyJiraWorld } from '../visual/production/fixtures-in-page'
import { QA_ENGINEER, QUARANTINE_IDS, VIEWER } from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const QUARANTINE_ON = { flaky_auto_quarantine: true }
const JIRA = `/api/v1/projects/${P}/defects/jira`
const primary = (page: Page) => page.locator('[data-primary]')
const request = (page: Page, id: string) => primary(page).locator(`[data-quarantine-id="${id}"]`)
const listReads = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, '/api/v1/quarantine').filter((line) => line.startsWith('GET ')).length

test('Q2 Quarantined → File Jira: the preview for that row only once open, exactly its POST, then the list re-reads', async ({ page }) => {
  const world = flakyJiraWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=quarantined', { handlers: world.handlers, ready, flags: QUARANTINE_ON })
  const row = request(page, QUARANTINE_IDS.quarantined)
  await expect(row).toContainText('test_quarantine_due')
  await networkQuiet(page, api)
  expect(requestsTo(api, `${JIRA}/preview`), 'no preview until the form opens').toEqual([])

  // Cancel: nothing.
  await row.getByRole('button', { name: 'File Jira' }).click()
  let form = opened(page, 'Create Jira issue')
  await expect(form.getByText('test_quarantine_due', { exact: true })).toBeVisible()
  await expect(form.getByLabel('Prefilled description')).toContainText('Failure: fp-q2')
  expect(requestsTo(api, `${JIRA}/preview`)).toEqual([`GET ${JIRA}/preview?fingerprint=fp-q2`])
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const lists = listReads(api)
  await row.getByRole('button', { name: 'File Jira' }).click()
  form = opened(page, 'Create Jira issue')
  await form.getByRole('button', { name: 'Create issue' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('link', { name: CREATED_JIRA_KEY })).toHaveAttribute('href', CREATED_JIRA_URL)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${JIRA}`, body: { fingerprint: 'fp-q2', issue_type: 'Bug', jira_project_key: 'SHOP', target: 'jira' } }])
  expect(listReads(api), 'the request list re-reads after the create').toBeGreaterThan(lists)
  assertHermetic(api, errors)
})

test('Q2 from a Proposed row (as a QA engineer): its own fingerprint in the POST', async ({ page }) => {
  const world = flakyJiraWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=proposed', { handlers: world.handlers, ready, flags: QUARANTINE_ON, user: QA_ENGINEER })
  const row = request(page, QUARANTINE_IDS.liveProposal)
  await expect(row).toContainText('test_checkout_flow_0')
  // A QA engineer decides nothing here (no Approve) but may file Jira.
  await expect(row.getByTestId('quarantine-approve')).toHaveCount(0)
  await row.getByRole('button', { name: 'File Jira' }).click()
  const form = opened(page, 'Create Jira issue')
  await form.getByLabel('Issue type').selectOption('Task')
  await form.getByRole('button', { name: 'Create issue' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${JIRA}`, body: { fingerprint: 'fp-00', issue_type: 'Task', jira_project_key: 'SHOP', target: 'jira' } }])
  assertHermetic(api, errors)
})

test('Q2 role gating: a viewer sees the quarantined rows and no File Jira', async ({ page }) => {
  const world = flakyJiraWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=quarantined', { handlers: world.handlers, ready, flags: QUARANTINE_ON, user: VIEWER })
  await expect(request(page, QUARANTINE_IDS.quarantined)).toContainText('test_quarantine_due')
  await expect(primary(page).getByRole('button', { name: 'File Jira' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, `${JIRA}/preview`)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
