/**
 * W21d ACT-02 "mentions on a test-case comment" (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §2.3, §2.4.3,
 * §4.1, §4.2), hermetic, 1440 × 900 (the geometry at 1280 and 1440):
 *
 *  - as a TESTER, a case's Comments › typing `@al` offers exactly the two ELIGIBLE members (an inactive, a synthetic
 *    and a service account that also start with `al` are left out; no e-mail is shown), from the page's own members
 *    read (no new request path); a click inserts `@alice `, the list closes; Post Comment sends ONE
 *    `POST …/cases/{id}/comments` `{ content, comment_type: 'general' }` and toasts `Comment added · notified @alice`;
 *    the comments re-read lists it;
 *  - no suggestions inside a word (`email@al`), after a space, or for a service account (`@albot`); a comment naming
 *    one posts as plain text and toasts `Comment added`;
 *  - the suggestions list sits in the case pane at 1280 and 1440 (no overlay, `#main-content` never scrolls
 *    sideways), its slack printed (`INPAGE /test-management mention-suggestions …`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `activityLiveWorld()`
 * (`fixtures-activity-live.ts`) over `testCaseEditingWorld()` (`fixtures-test-case-editing.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { activityLiveWorld, defaultComments } from '../visual/production/fixtures-activity-live'
import { tmCaseId, tmCaseTitle, tmMembers } from '../visual/production/fixtures-in-page-b'
import { EDIT_CASE, TESTER, testCaseEditingWorld } from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const CASE_ID = tmCaseId(EDIT_CASE)
const COMMENTS = `/api/v1/test-management/cases/${CASE_ID}/comments`
const MEMBERS = `/api/v1/projects/${PROJECT_ID}/members`
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')
const composer = (p: Page) => pane(p).locator('[data-comment-composer]')
const box = (p: Page) => composer(p).getByPlaceholder('Add a comment…')
const suggestions = (p: Page) => composer(p).locator('[data-mention-suggestions]')

/** The case project's members: two eligible `al…` people, three ineligible `al…` look-alikes, and the tester. */
function members() {
  const base = tmMembers()
  const like = (n: number, username: string, fullName: string, flags: Record<string, boolean> = {}) => ({
    ...base[1],
    id: `00000000-0000-4000-8000-0000000002${String(n).padStart(2, '0')}`,
    user_id: `00000000-0000-4000-8000-0000000003${String(n).padStart(2, '0')}`,
    email: `${username}@example.test`,
    username,
    full_name: fullName,
    ...flags,
  })
  return [
    ...base,
    like(1, 'alice', 'Alice Ng'),
    like(2, 'alan.ortiz', 'Alan Ortiz'),
    like(3, 'alma.old', 'Alma Former', { is_active: false }),
    like(4, 'al-qa-lead', 'Checkout QA lead (system)', { is_synthetic: true }),
    like(5, 'albot', 'Al CI bot', { is_service_account: true }),
    { ...like(6, TESTER.username, TESTER.full_name), user_id: TESTER.id, role: 'TESTER' },
  ]
}

async function open(page: Page) {
  const live = activityLiveWorld({
    role: TESTER,
    members: members(),
    comments: [{ ...defaultComments()[0], test_case_id: CASE_ID }],
    base: testCaseEditingWorld({ user: TESTER }).handlers,
  })
  const opened_ = await openRollout(page, '/test-management', { handlers: live.handlers, ready: tmReady, user: TESTER })
  await caseRow(page, EDIT_CASE).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(EDIT_CASE) })).toBeVisible()
  await pane(page).getByRole('button', { name: 'Comments', exact: true }).click()
  await expect(pane(page).getByText('Step 2 fails on a slow network.')).toBeVisible()
  await expect(box(page)).toBeVisible()
  return { live, ...opened_ }
}

test('ACT-02 a tester mentions a member: @al offers the eligible members, a click inserts the name, Post notifies', async ({ page }) => {
  watchNativeDialogs(page)
  const { live, api, errors } = await open(page)
  await box(page).click()
  await box(page).pressSequentially('@al')
  await expect(suggestions(page).getByRole('button')).toHaveText(['Alan Ortiz · @alan.ortiz', 'Alice Ng · @alice'])
  await expect(composer(page)).not.toContainText('example.test')
  await suggestions(page).getByRole('button', { name: 'Alice Ng · @alice' }).click()
  await expect(box(page)).toHaveValue('@alice ')
  await expect(suggestions(page)).toHaveCount(0)
  await expect(box(page)).toBeFocused()
  await box(page).pressSequentially('please re-check step 3')
  await composer(page).getByRole('button', { name: 'Post Comment' }).click()
  await expect(page.getByText('Comment added · notified @alice', { exact: true })).toBeVisible()
  await expect(pane(page).getByText('@alice please re-check step 3', { exact: true })).toBeVisible()
  await expect(box(page)).toHaveValue('')
  await networkQuiet(page, api)
  expect(live.writes).toEqual([
    { line: `POST ${COMMENTS}`, body: { content: '@alice please re-check step 3', comment_type: 'general' } },
  ])
  // The suggestions read the page's own members key; nothing else is asked for them.
  expect(requestsTo(api, MEMBERS).length).toBeGreaterThanOrEqual(1)
  expect(requestsTo(api, /mention/i)).toEqual([])
  assertHermetic(api, errors)
})

test('ACT-02 no suggestion inside a word, after a space or for a service account; such a name posts as plain text', async ({ page }) => {
  watchNativeDialogs(page)
  const { live, api, errors } = await open(page)
  await box(page).click()
  await box(page).pressSequentially('mail email@al')
  await expect(suggestions(page)).toHaveCount(0)
  await box(page).fill('')
  await box(page).pressSequentially('@al ')
  await expect(suggestions(page)).toHaveCount(0)
  await box(page).fill('')
  await box(page).pressSequentially('cc @albot')
  await expect(suggestions(page)).toHaveCount(0)
  await box(page).pressSequentially(' and @tes')
  await expect(suggestions(page), 'never the signed-in user').toHaveCount(0)
  await composer(page).getByRole('button', { name: 'Post Comment' }).click()
  await expect(page.getByText('Comment added', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(live.writes.map((w) => w.body)).toEqual([{ content: 'cc @albot and @tes', comment_type: 'general' }])
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 } })

    test(`@${width}: the suggestions list sits in the case pane; nothing scrolls sideways`, async ({ page }) => {
      watchNativeDialogs(page)
      const { live, api, errors } = await open(page)
      await box(page).click()
      await box(page).pressSequentially('@al')
      const list = suggestions(page)
      await expect(list.getByRole('button')).toHaveCount(2)
      await networkQuiet(page, api)
      await expectNoOverlay(page)
      await expectMainFits(page, `/test-management mention-suggestions @${width}`)
      const [listBox, paneBox] = [await boxOf(list), await boxOf(pane(page))]
      expect(listBox.left, 'inside the pane').toBeGreaterThanOrEqual(paneBox.left - 1)
      expect(listBox.right, 'inside the pane').toBeLessThanOrEqual(paneBox.right + 1)
      inPageLine('/test-management mention-suggestions', width, { pane: paneBox.width, list: listBox.width, slack: await minContentSlack(list) })
      expect(live.writes).toEqual([])
      assertHermetic(api, errors)
    })
  })
}
