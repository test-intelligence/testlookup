/**
 * W21d ACT-02, hermetic (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §2.4, §4.1, §4.2), on `/my-failures`
 * (the Inbox), its Activity tab — EVERY role:
 *
 *  - the default tab asks for no `/activity/mine` (fold-inbox's inventory is unchanged), and the Activity tab has no
 *    count in its label;
 *  - `?tab=activity` reads it once with `view=all`, pinned to the active project (`project_id`), and lists the rows
 *    with their involvement chip and the server's `total` as the count line;
 *  - in All Projects no `project_id` is sent and each row names its project; Mine / Involving me re-read;
 *  - a failed read says "Could not load the activity." — no count line, no 0;
 *  - a tester and a viewer open the same tab;
 *  - an entity link in another project switches the active project first, then opens the server's link.
 *
 * Harness: `tests/lib/rollout.ts` / `production-pages.ts` (fail closed, pinned clock). World:
 * `fixtures-activity-live.ts` over `inboxHandlers()` (`fixtures-pages.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { openProductionPage, respond, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, USER } from '../visual/production/fixtures'
import { inboxHandlers } from '../visual/production/fixtures-pages'
import { activityLiveWorld, LIVE_DEFECT, SECOND_PROJECT } from '../visual/production/fixtures-activity-live'
import { ADMIN } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const MINE = '/api/v1/activity/mine'

const ready = (p: Page) => p.locator('[data-page-header] h1')
const activityTab = (p: Page) => p.getByRole('tab', { name: /^Activity/ })
const rows = (p: Page) => p.locator('[data-inbox-activity]').getByTestId('activity-row')
const countLine = (p: Page) => p.locator('[data-activity-count]')
const pathAndSearch = (p: Page) => {
  const url = new URL(p.url())
  return `${url.pathname}${url.search}`
}
/** The query of each `/activity/mine` read, in order. */
const mineQueries = (api: Parameters<typeof requestsTo>[0]) =>
  requestsTo(api, MINE).map((line) => new URL(line.slice(line.indexOf(' ') + 1), 'http://inbox.test').searchParams)

/**
 * All Projects is an ADMIN's: the top bar pins anyone else to their first project (`TopBar.tsx`), so the
 * All-Projects cases sign in as the fixture admin (`openRollout` always pins the fixture project).
 */
function openAllProjects(page: Page, handlers: ApiHandlers) {
  return openProductionPage(page, '/my-failures?tab=activity', {
    theme: 'signal', now: NOW, me: ADMIN, user: ADMIN, projectId: 'all', handlers, ready,
  })
}

test('the default tab asks for no /activity/mine; Activity reads it once, pinned to the project, with the server total', async ({ page }) => {
  const world = activityLiveWorld({ base: inboxHandlers() })
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  expect(requestsTo(api, MINE), 'the default tab reads no activity').toEqual([])
  await expect(activityTab(page)).toHaveText('Activity')

  await activityTab(page).click()
  await expect.poll(() => pathAndSearch(page)).toBe('/my-failures?tab=activity')
  await expect(rows(page)).toHaveCount(3)
  await expect(countLine(page)).toHaveText('3 events in the last 30 days')
  await expect(rows(page).locator('[data-involvement]')).toHaveText(['Mentioned you', 'You', 'Your test case'])
  await expect(page.locator('[data-activity-project]')).toHaveCount(0)
  const queries = mineQueries(api)
  expect(queries).toHaveLength(1)
  expect(queries[0].get('view')).toBe('all')
  expect(queries[0].get('project_id')).toBe(PROJECT_ID)
  await expect(activityTab(page)).toHaveText('Activity')
  assertHermetic(api, errors)
})

test('All Projects sends no project_id and names each row\'s project; Mine and Involving me re-read with their view', async ({ page }) => {
  const world = activityLiveWorld({ role: ADMIN, base: inboxHandlers() })
  const { api, errors } = await openAllProjects(page, world.handlers)
  await expect(rows(page)).toHaveCount(4)
  await expect(rows(page).locator('[data-involvement]')).toHaveText(['Mentioned you', 'You', 'Assigned to you', 'Your test case'])
  await expect(page.locator('[data-activity-project]')).toHaveText(['Checkout', 'Checkout', 'Payments', 'Checkout'])
  await expect(countLine(page)).toHaveText('4 events in the last 30 days')
  expect(mineQueries(api)[0].has('project_id')).toBe(false)

  await page.getByRole('radio', { name: 'Mine' }).click()
  await expect(rows(page)).toHaveCount(1)
  await expect(countLine(page)).toHaveText('1 event in the last 30 days')
  await page.getByRole('radio', { name: 'Involving me' }).click()
  await expect(rows(page)).toHaveCount(3)
  await expect.poll(() => pathAndSearch(page)).toBe('/my-failures?tab=activity&view=involving')
  expect(mineQueries(api).map((q) => q.get('view'))).toEqual(['all', 'mine', 'involving'])
  assertHermetic(api, errors)
})

test('a failed read says "Could not load the activity." — no count line, no 0', async ({ page }) => {
  const world = activityLiveWorld({ base: inboxHandlers() })
  const handlers: ApiHandlers = [[MINE, () => respond(500, { detail: 'boom' })], ...world.handlers]
  const { api, errors } = await openRollout(page, '/my-failures?tab=activity', { handlers, ready })
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load the activity.' })).toBeVisible()
  await expect(countLine(page)).toHaveCount(0)
  await expect(page.locator('[data-inbox-activity]')).toHaveCount(0)
  assertHermetic(api, errors)
})

for (const role of ['TESTER', 'VIEWER']) {
  test(`a ${role.toLowerCase()} opens the same tab`, async ({ page }) => {
    const user = { ...USER, role }
    const world = activityLiveWorld({ role: user, base: inboxHandlers() })
    const { api, errors } = await openRollout(page, '/my-failures?tab=activity', { handlers: world.handlers, ready, user })
    await expect(activityTab(page)).toHaveAttribute('aria-selected', 'true')
    await expect(rows(page)).toHaveCount(3)
    await expect(countLine(page)).toHaveText('3 events in the last 30 days')
    assertHermetic(api, errors)
  })
}

test('an entity link in another project switches to that project first, then opens the server link', async ({ page }) => {
  const world = activityLiveWorld({ role: ADMIN, base: inboxHandlers() })
  const { api, errors } = await openAllProjects(page, world.handlers)
  await expect(rows(page)).toHaveCount(4)
  await networkQuiet(page, api)
  // Everything up to the click was answered; the defects page it opens has no fixture here.
  assertHermetic(api, errors)

  await page.getByRole('link', { name: LIVE_DEFECT.title }).click()
  await expect.poll(() => pathAndSearch(page)).toBe(`/defects?defect=${LIVE_DEFECT.id}`)
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('testlookup-active-project') ?? '{}'))
  expect(stored?.state?.activeProjectId).toBe(SECOND_PROJECT.id)
})
