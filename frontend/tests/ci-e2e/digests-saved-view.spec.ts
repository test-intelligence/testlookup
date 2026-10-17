/**
 * W09 NTF-04 (contract §2.4, §4.4), hermetic, on `/settings/digests`: digest saved views are the project's
 * REPORT views, and a Daily / Weekly digest with one is filtered by it.
 *
 *  - Saved Views lists the report views ("Checkout regressions": `Saved on Trends`, its scope) and the legacy
 *    empty rows (`No filters — edit to add some`), never a widget layout row; a hostile name is text;
 *  - **New view** opens an editor at the top of the tab (in the page, no dialog): Name, Suites, Window → Save
 *    POSTs the report-view shape through the saved-views API (`page: 'trends'`); a one-character name blocks
 *    Save with the reason as text;
 *  - Subscriptions ask for no view on load: the **Saved view (optional)** field (Daily and Weekly only) starts
 *    as `Choose a saved view`, whose click makes the one saved-views read; Weekly + the view → the POST names
 *    `saved_view_id`; the row reads ` · View: Checkout regressions`; a row whose view was deleted reads
 *    `View deleted — this digest covers the whole project`, and its **Change view** PATCHes `saved_view_id`;
 *  - Preview with the view → the GET carries `saved_view_id`, and the page shows `Filtered by view: Checkout
 *    regressions — …`, the dropped-release note and the project-wide note; without a view the GET is as before.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `digestsWorld()` (`fixtures-notify-flaky.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, expectHostileAsText, networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  DELETED_VIEW_SUB,
  DIGEST_PREVIEW_PATH,
  DIGEST_SUBSCRIPTIONS_PATH,
  digestsWorld,
  HOSTILE_VIEW,
  LAYOUT_VIEW,
  LEGACY_VIEW,
  REGRESSIONS_VIEW,
  SAVED_VIEWS_PATH,
  SUB_IDS,
  VIEW_IDS,
  WEEKLY_WITH_VIEW,
} from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const tab = (page: Page, name: string) => page.getByRole('tab', { name })
const viewRow = (page: Page, id: string) => page.locator(`[data-digest-view-row="${id}"]`)
const subRow = (page: Page, id: string) => page.locator(`[data-subscription-row="${id}"]`)
const form = (page: Page) => page.locator('[data-subscription-form]')
const VIEWS_LINE = `GET ${SAVED_VIEWS_PATH}?project_id=${P}`

/** The ids `digestsWorld` gives its first created view and subscription. */
const CREATED_VIEW_ID = '00000000-0000-4000-8000-000000000d11'
const CREATED_SUB_ID = '00000000-0000-4000-8000-000000000e11'

test('Saved Views: report views and legacy rows, never a layout; New view saves the report-view shape', async ({ page }) => {
  const world = digestsWorld({ views: [REGRESSIONS_VIEW, LEGACY_VIEW, LAYOUT_VIEW, HOSTILE_VIEW] })
  const { api, errors } = await openRollout(page, '/settings/digests?tab=saved-views', { handlers: world.handlers, ready })
  await expect(tab(page, 'Saved Views')).toHaveAttribute('aria-selected', 'true')

  const regressions = viewRow(page, VIEW_IDS.regressions)
  await expect(regressions).toContainText('Checkout regressions')
  await expect(regressions).toContainText('Shared')
  // The two release ids that are not this project's are counted, never shown raw.
  await expect(regressions).toContainText('Saved on Trends · Releases: 2026.11, plus 2 not in this project · Suites: checkout · Last 7 days')
  await expect(viewRow(page, VIEW_IDS.legacy)).toContainText('No filters — edit to add some')
  await expect(viewRow(page, VIEW_IDS.layout), 'a widget layout is not a digest view').toHaveCount(0)
  await expect(page.getByText(LAYOUT_VIEW.name)).toHaveCount(0)
  const hostile = viewRow(page, VIEW_IDS.hostile)
  await expect(hostile).toContainText(HOSTILE_VIEW.name)
  await expect(hostile).toContainText('Saved on Failures')
  await expectHostileAsText(page, hostile, 'a view named with markup')

  // New view: an in-page section at the top of the tab.
  await page.getByRole('button', { name: 'New view' }).click()
  const editor = page.locator('section[data-digest-view-editor="new"]')
  await expect(editor).toHaveAttribute('aria-label', 'New view')
  await expect(page.locator('#main-content section[data-digest-view-editor]')).toHaveCount(1)
  await expect(page.getByRole('dialog'), 'the editor is in the page').toHaveCount(0)
  const editorBox = await editor.boundingBox()
  const firstRow = await regressions.boundingBox()
  expect(editorBox && firstRow && editorBox.y < firstRow.y, 'the editor sits above the list').toBe(true)

  await editor.getByLabel('Name').fill('Checkout')
  await editor.getByLabel(/^Suites/).fill(' checkout ')
  await expect(editor.getByLabel('Window')).toHaveValue('7')
  const post = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === SAVED_VIEWS_PATH)
  await editor.getByRole('button', { name: 'Save', exact: true }).click()
  expect((await post).postDataJSON()).toEqual({
    project_id: P,
    name: 'Checkout',
    page: 'trends',
    is_shared: false,
    filters: { kind: 'report_view', page: 'trends', window: 7, suites: ['checkout'] },
  })
  await expect(editor).toHaveCount(0)
  await expect(viewRow(page, CREATED_VIEW_ID)).toContainText('Saved on Trends · Any release · Suites: checkout · Last 7 days')
  await networkQuiet(page, api)
  expect(world.state.writes.map((w) => w.line)).toEqual([`POST ${SAVED_VIEWS_PATH}`])
  assertHermetic(api, errors)
})

test('a one-character name blocks Save with the reason as text', async ({ page }) => {
  const world = digestsWorld()
  const { api, errors } = await openRollout(page, '/settings/digests?tab=saved-views', { handlers: world.handlers, ready })
  await page.getByRole('button', { name: 'New view' }).click()
  const editor = page.locator('section[data-digest-view-editor="new"]')
  await editor.getByLabel('Name').fill('C')
  await editor.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(editor.locator('[data-view-error="name"]')).toHaveText('A name needs 2 to 255 characters.')
  await expect(editor).toBeVisible()
  await networkQuiet(page, api)
  expect(world.state.writes, 'nothing was saved').toEqual([])
  assertHermetic(api, errors)
})

test('Subscriptions: the view picker is asked for on a click, Daily / Weekly only; Weekly + a view POSTs it; a deleted view is said and changed in the row', async ({ page }) => {
  const world = digestsWorld({ subscriptions: [WEEKLY_WITH_VIEW, DELETED_VIEW_SUB], views: [REGRESSIONS_VIEW, LEGACY_VIEW, LAYOUT_VIEW] })
  const { api, errors } = await openRollout(page, '/settings/digests', { handlers: world.handlers, ready })
  await expect(subRow(page, SUB_IDS.weekly).locator('[data-subscription-summary]')).toContainText(' · View: Checkout regressions')
  const deleted = subRow(page, SUB_IDS.deletedView)
  await expect(deleted.locator('[data-scope-problem="saved_view_deleted"]')).toHaveText('View deleted — this digest covers the whole project')
  await expect(deleted.locator('[data-subscription-summary]')).not.toContainText('View:')
  await networkQuiet(page, api)
  expect(requestsTo(api, SAVED_VIEWS_PATH), 'no view is read on load').toEqual([])

  // Daily and Weekly only.
  await form(page).getByLabel('Schedule').selectOption({ label: 'Per Run' })
  await expect(form(page).getByText('Saved view (optional)')).toHaveCount(0)
  await form(page).getByLabel('Schedule').selectOption({ label: 'Daily' })
  await expect(form(page).getByText('Saved view (optional)')).toBeVisible()
  await form(page).getByLabel('Schedule').selectOption({ label: 'Weekly' })

  // The click makes the one saved-views read; the select then offers the project's report views only.
  await form(page).getByRole('button', { name: 'Choose a saved view' }).click()
  const picker = form(page).getByLabel('Saved view (optional)')
  await expect(picker.locator('option')).toHaveText(['No view (whole project)', 'Checkout regressions'])
  expect(requestsTo(api, SAVED_VIEWS_PATH)).toEqual([VIEWS_LINE])

  await form(page).getByLabel('Digest Name').fill('Weekly checkout digest')
  await picker.selectOption({ label: 'Checkout regressions' })
  const post = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === DIGEST_SUBSCRIPTIONS_PATH)
  await form(page).getByRole('button', { name: 'Subscribe' }).click()
  expect((await post).postDataJSON()).toEqual({
    project_id: P,
    name: 'Weekly checkout digest',
    schedule: 'WEEKLY',
    channel: 'email',
    report_attachment: false,
    saved_view_id: VIEW_IDS.regressions,
  })
  await expect(subRow(page, CREATED_SUB_ID).locator('[data-subscription-summary]')).toHaveText(
    /^Weekly via email · View: Checkout regressions · Delivered: 0 times/,
  )

  // The deleted view's fix, in the row: Change view → a report view → Save.
  await deleted.getByRole('button', { name: 'Change view' }).click()
  const change = deleted.locator('[data-change-view]')
  await change.locator('[data-digest-view-select]').selectOption({ label: 'Checkout regressions' })
  const patch = page.waitForRequest(
    (r) => r.method() === 'PATCH' && new URL(r.url()).pathname === `${DIGEST_SUBSCRIPTIONS_PATH}/${SUB_IDS.deletedView}`,
  )
  await change.getByRole('button', { name: 'Save', exact: true }).click()
  expect((await patch).postDataJSON()).toEqual({ saved_view_id: VIEW_IDS.regressions })
  await expect(deleted.locator('[data-scope-problem]')).toHaveCount(0)
  await expect(deleted.locator('[data-subscription-summary]')).toContainText(' · View: Checkout regressions')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.state.writes.map((w) => w.line)).toEqual([
    `POST ${DIGEST_SUBSCRIPTIONS_PATH}`,
    `PATCH ${DIGEST_SUBSCRIPTIONS_PATH}/${SUB_IDS.deletedView}`,
  ])
  assertHermetic(api, errors)
})

test('Preview with the view: saved_view_id on the wire and "Filtered by view" with its notes', async ({ page }) => {
  const world = digestsWorld()
  const { api, errors } = await openRollout(page, '/settings/digests?tab=preview', { handlers: world.handlers, ready })
  const picker = page.getByLabel('Saved view', { exact: true })
  await picker.selectOption({ label: 'Checkout regressions' })
  await page.getByRole('button', { name: 'Generate Preview' }).click()
  const scope = page.locator('[data-digest-scope="saved_view"]')
  await expect(scope).toContainText('Filtered by view: Checkout regressions — release 2026.11 · suite checkout · last 7 days')
  await expect(scope).toContainText('2 releases in this view no longer exist in this project and were left out.')
  await expect(scope).toContainText('Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest.')
  await networkQuiet(page, api)
  const asked = queryOf(api, DIGEST_PREVIEW_PATH)
  expect(asked.map((q) => Object.fromEntries(q))).toEqual([{ project_id: P, period: 'weekly', saved_view_id: VIEW_IDS.regressions }])

  // Without a view: the whole project, no scope block, and the GET asks exactly what it did before W09.
  await picker.selectOption({ label: 'No view (whole project)' })
  await page.getByRole('button', { name: 'Generate Preview' }).click()
  await expect(page.locator('[data-digest-scope]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(queryOf(api, DIGEST_PREVIEW_PATH).map((q) => Object.fromEntries(q))[1]).toEqual({ project_id: P, period: 'weekly' })
  assertHermetic(api, errors)
})
