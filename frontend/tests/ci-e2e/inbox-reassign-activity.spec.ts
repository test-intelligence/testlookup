/**
 * D-0021 (GPT-6): a QA lead reassigned a failure in the Inbox, and the project's Activity log had no entry for it,
 * whatever they searched ("assigned", "reassigned", the test name). The server now records `failure.reassigned`
 * on a real change (owner decision Q4: this one event); re-choosing the current assignee records nothing.
 *
 * The reassign handler here answers like the fixed server: it appends one ledger row only when the assignee
 * changes, then delegates to the Inbox world's own handler (which records the write). The `/activity` feed is the
 * activity world's stateful ledger, with its `q` search. Like D-0011's spec, this pins the round trip the reader
 * sees; the SQLite regression and the Postgres test prove the server writes the row.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { openedByHeading } from '../lib/inPage'
import type { ApiHandler, ApiHandlers, ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { activityLiveWorld, activityRow } from '../visual/production/fixtures-activity-live'
import { inboxInPageWorld, REASSIGN_OPTIONS } from '../visual/production/fixtures-in-page'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const FAILURE = { id: '00000000-0000-4000-8000-000000000403', name: 'test_checkout_step_3', run: '00000000-0000-4000-8000-000000000503' }
const ME = '/api/v1/me/assigned-failures'
const REASSIGN = `${ME}/${FAILURE.id}/reassign`
const FEED = `/api/v1/projects/${PROJECT_ID}/activity`
const NAMES: Record<string, string> = Object.fromEntries(
  [REASSIGN_OPTIONS.suiteOwner, ...REASSIGN_OPTIONS.engineers].map((p) => [p.user_id, p.full_name]),
)

const inboxReady = (p: Page) => p.locator('[data-page-header] h1')
const activityReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Activity' })
const failureRow = (p: Page) =>
  p.locator('tbody tr:not([data-in-page-row])').filter({ has: p.getByText(FAILURE.name, { exact: true }) })
const feedRows = (p: Page) => p.getByRole('feed').getByTestId('activity-row')
const reassignedRows = (p: Page) => feedRows(p).filter({ hasText: /reassigned/ })

function world() {
  const inbox = inboxInPageWorld()
  const activity = activityLiveWorld({
    feed: [activityRow({
      id: 'a-review-1', category: 'analysis', event_type: 'review.rejected',
      entity: { type: 'run', id: FAILURE.run, label: `run ${FAILURE.run.slice(0, 8)}`, href: `/runs/${FAILURE.run}` },
      summary: `Priya Nair rejected the AI report for run ${FAILURE.run.slice(0, 8)} (wrong_category)`,
      context: {}, source: null,
    })],
  })
  const inboxPut = inbox.handlers.find(([matcher, , method]) => method === 'PUT' && String(matcher).includes('reassign'))
  if (!inboxPut) throw new Error('the inbox world has no reassign handler')
  let assignee = 'Dana Whitfield'
  let n = 0
  // The fixed server: one `failure.reassigned` row per real change, then the Inbox world's own answer.
  const reassign: ApiHandler = (request: ApiRequest) => {
    const body = JSON.parse(request.route.request().postData() ?? '{}') as { new_assignee_user_id?: string }
    const to = NAMES[body.new_assignee_user_id ?? ''] ?? 'Unknown'
    if (to !== assignee) {
      activity.feed.push(activityRow({
        id: `a-reassign-${++n}`, category: 'quality', event_type: 'failure.reassigned',
        entity: { type: 'run', id: FAILURE.run, label: `run ${FAILURE.run.slice(0, 8)}`, href: `/runs/${FAILURE.run}` },
        summary: `Failure ${FAILURE.name} reassigned from ${assignee} to ${to}`,
        context: { test_name: FAILURE.name, from_name: assignee, to_name: to }, source: null,
      }))
      assignee = to
    }
    return inboxPut[1](request)
  }
  const handlers: ApiHandlers = [[REASSIGN, reassign, 'PUT'], ...inbox.handlers, ...activity.handlers]
  return { handlers, inbox, activity }
}

test('D-0021: a reassignment in the Inbox is in the Activity log, once, and its searches find it', async ({ page }) => {
  const w = world()
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: w.handlers, ready: inboxReady })
  await failureRow(page).getByRole('button', { name: 'Reassign' }).click()
  const form = openedByHeading(page, 'Reassign failure')
  await form.getByRole('radio', { name: /Priya Nair/ }).check()
  await form.getByRole('button', { name: 'Reassign', exact: true }).click()
  await expect(page.getByText('Failure reassigned')).toBeVisible()
  await networkQuiet(page, api)
  expect(w.inbox.writes).toEqual([
    { line: `PUT ${REASSIGN}`, body: { new_assignee_user_id: REASSIGN_OPTIONS.engineers[0].user_id } },
  ])

  await page.goto('/activity')
  await expect(activityReady(page)).toBeVisible()
  await expect(reassignedRows(page)).toHaveCount(1)
  const row = reassignedRows(page)
  await expect(row).toContainText(`Failure ${FAILURE.name} reassigned from Dana Whitfield to Priya Nair`)
  await expect(row.locator(`a[href="/runs/${FAILURE.run}"]`).first()).toBeVisible()
  await expect(feedRows(page).filter({ hasText: 'rejected the AI report' })).toHaveCount(1)

  await page.getByLabel('Search activity').fill('reassigned')
  await page.getByLabel('Search activity').press('Enter')
  await expect(feedRows(page)).toHaveCount(1)
  await networkQuiet(page, api)
  expect(requestsTo(api, FEED).some((line) => line.includes('q=reassigned'))).toBe(true)

  await page.reload()
  await expect(activityReady(page)).toBeVisible()
  await expect(reassignedRows(page)).toHaveCount(1)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
