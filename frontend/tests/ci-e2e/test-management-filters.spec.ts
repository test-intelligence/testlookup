/**
 * W13 TCL-01 "multi-select filters in the URL, an Owner filter, the four views that really filter, saved case
 * views" (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §2.2, §4.2), hermetic, 1440 × 900:
 *
 *  - as a QA ENGINEER: Status = Draft + Review requested and Owner = Me (the bar's `MultiSelect`s) write
 *    `status=draft,review_requested&owner=me` into the URL with `replace` (no history entry), the list read sends
 *    `status=draft&status=review_requested&owner=me` (repeated keys), only the reader's two cases are listed, the
 *    authored-only note shows (`automation_rows_excluded`), a reload restores the exact list, Export sends the same
 *    filters;
 *  - the four presets send their PARAMS (My drafts, My P0/P1, Unautomated, AI drafts) and a chip is active exactly
 *    when the state equals it;
 *  - Save view → `POST /saved-views` with `page: test_management` and the filters, shared; the chip appears; another
 *    member's shared view applies (every key replaced) and offers no action; a 422 renders its sentence in the form.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `caseFiltersWorld()`
 * (`fixtures-test-library.ts`, over `TEST_CASES`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  caseFiltersWorld,
  EXPORT_API,
  LIBRARY_TEXT,
  LIBRARY_USERS,
  listQueries,
  MY_CASES,
  NEW_CASE_VIEW_ID,
  SAVED_VIEWS_API,
  tmCaseId,
  type CaseFiltersOptions,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const rows = (p: Page) => p.locator('[data-primary] tbody tr[data-case-row]:not([data-in-page-row])')
const trigger = (p: Page, key: string) => p.locator(`[data-case-filter="${key}"]`)
const views = (p: Page) => p.locator('[data-case-views]')
const urlParams = (p: Page) => new URL(p.url()).searchParams
const historyLength = (p: Page) => p.evaluate(() => window.history.length)

async function open(page: Page, options: CaseFiltersOptions = {}, path = '/test-management') {
  const world = caseFiltersWorld(options)
  const opened_ = await openRollout(page, path, { handlers: world.handlers, ready: tmReady, user: LIBRARY_USERS[options.role ?? 'QA_ENGINEER'] })
  await expect(rows(page).first()).toBeVisible()
  return { world, ...opened_ }
}

/** Pick `labels` in the bar's `key` MultiSelect, then close its popover. */
async function pick(page: Page, key: string, name: string, labels: string[]) {
  await trigger(page, key).click()
  const popover = page.getByRole('dialog', { name: `${name} options` })
  await expect(popover).toBeVisible()
  for (const label of labels) await popover.getByRole('option', { name: label, exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(popover).toHaveCount(0)
}

/** The last list read's QUERY (the table's page). */
const lastList = (world: ReturnType<typeof caseFiltersWorld>) => {
  const queries = listQueries(world.reads).filter((q) => q.get('size') === '25')
  expect(queries.length, 'the table read the list').toBeGreaterThan(0)
  return queries[queries.length - 1]
}

test('TCL-01 as a QA engineer: Status = Draft + Review requested and Owner = Me → the URL (replace), repeated keys on the wire, the authored-only note; a reload restores it', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page)
  await networkQuiet(page, api)
  await expect(page.locator('[data-cases-filter-note]'), 'no note in the default world').toHaveCount(0)
  const historyBefore = await historyLength(page)

  await pick(page, 'status', 'Status', ['Draft', 'Review requested'])
  await pick(page, 'owner', 'Owner', ['Me'])
  await expect(rows(page)).toHaveCount(MY_CASES.length)
  for (const i of MY_CASES) await expect(page.locator(`[data-primary] tr[data-case-row="${tmCaseId(i)}"]`)).toBeVisible()
  await networkQuiet(page, api)

  const params = urlParams(page)
  expect(params.get('status'), 'the statuses as one CSV value').toBe('draft,review_requested')
  expect(params.get('owner')).toBe('me')
  expect(await historyLength(page), 'written with replace: no history entry per click').toBe(historyBefore)
  const sent = lastList(world)
  expect(sent.getAll('status'), 'repeated keys on the wire').toEqual(['draft', 'review_requested'])
  expect(sent.get('owner')).toBe('me')
  expect(sent.get('page'), 'a filter change resets the page').toBe('1')
  expect(sent.has('assignee_id'), 'never the old ignored parameter').toBe(false)
  await expect(page.locator('[data-cases-filter-note]').getByRole('note')).toHaveText(LIBRARY_TEXT.authoredOnly)
  await expect(trigger(page, 'status')).toHaveAttribute('aria-label', 'Status, 2 selected')

  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect.poll(() => requestsTo(api, EXPORT_API).length).toBe(1)
  const exported = new URL(requestsTo(api, EXPORT_API)[0].slice(4), 'http://x').searchParams
  expect(exported.getAll('status'), 'the export carries repeated keys too').toEqual(['draft', 'review_requested'])
  expect(exported.get('owner')).toBe('me')

  await page.reload()
  await expect(tmReady(page)).toBeVisible()
  await expect(rows(page)).toHaveCount(MY_CASES.length)
  await expect(trigger(page, 'status')).toHaveAttribute('aria-label', 'Status, 2 selected')
  await networkQuiet(page, api)
  expect(lastList(world).getAll('status'), 'the reload restores the exact list').toEqual(['draft', 'review_requested'])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

const PRESETS: { label: string; url: Record<string, string>; wire: (q: URLSearchParams) => unknown; expected: unknown }[] = [
  { label: 'My drafts', url: { owner: 'me', status: 'draft' }, wire: (q) => [q.get('owner'), q.getAll('status')], expected: ['me', ['draft']] },
  { label: 'My P0/P1', url: { owner: 'me', priority: 'critical,high' }, wire: (q) => [q.get('owner'), q.getAll('priority')], expected: ['me', ['critical', 'high']] },
  { label: 'Unautomated', url: { automation: 'not_automated' }, wire: (q) => q.get('automation'), expected: 'not_automated' },
  { label: 'AI drafts', url: { status: 'draft', ai: '1' }, wire: (q) => [q.getAll('status'), q.get('ai_generated')], expected: [['draft'], 'true'] },
]

test('TCL-01 the four views send their PARAMS, each active exactly when the state equals it; a preset replaces every key', async ({ page }) => {
  const { world, api, errors } = await open(page, {}, '/test-management?type=functional&q=checkout')
  for (const preset of PRESETS) {
    await views(page).getByRole('button', { name: preset.label, exact: true }).click()
    await expect(views(page).getByRole('button', { name: preset.label, exact: true })).toHaveAttribute('aria-pressed', 'true')
    await networkQuiet(page, api)
    const params = urlParams(page)
    for (const [key, value] of Object.entries(preset.url)) expect(params.get(key), `${preset.label}: ?${key}`).toBe(value)
    expect(params.has('type') || params.has('q'), `${preset.label} replaced the earlier keys`).toBe(false)
    expect(preset.wire(lastList(world)), `${preset.label} on the wire`).toEqual(preset.expected)
    for (const other of PRESETS.filter((p) => p !== preset)) {
      await expect(views(page).getByRole('button', { name: other.label, exact: true })).toHaveAttribute('aria-pressed', 'false')
    }
  }
  await expect(page.locator('[data-cases-filter-note]').getByRole('note'), 'Unautomated / AI drafts answer authored cases only').toHaveText(LIBRARY_TEXT.authoredOnly)
  assertHermetic(api, errors)
})

test('TCL-01 Save view: POST /saved-views with page test_management and the filters (shared); the chip; another member\'s shared view applies and has no action', async ({ page }) => {
  const { world, api, errors } = await open(page, {}, '/test-management?status=draft,review_requested&owner=me')
  await networkQuiet(page, api)
  await views(page).getByRole('button', { name: 'Save view', exact: true }).click()
  const form = opened(page, 'Save this view')
  await expect(form).toBeVisible()
  await expect(form.locator('#case-view-save-title')).toHaveText('Save this view')
  await form.getByLabel('Name').fill('My open reviews')
  await form.getByLabel(/^Share with /).check()
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)

  const posts = world.writes.filter((w) => w.line === `POST ${SAVED_VIEWS_API}`)
  expect(posts).toHaveLength(1)
  expect(posts[0].body).toEqual({
    project_id: PROJECT_ID,
    name: 'My open reviews',
    page: 'test_management',
    filters: { kind: 'test_case_view', statuses: ['draft', 'review_requested'], owner: 'me' },
    is_shared: true,
  })
  const chip = views(page).locator(`[data-case-view="${NEW_CASE_VIEW_ID}"]`)
  await expect(chip).toContainText('My open reviews')
  await expect(chip, 'the state equals the new view').toHaveAttribute('aria-pressed', 'true')

  // Another member's shared view: applies, every key replaced; never in Manage.
  await views(page).getByRole('button', { name: /^Checkout regression/ }).click()
  await networkQuiet(page, api)
  const params = urlParams(page)
  expect(params.get('type')).toBe('regression')
  expect(params.has('status') || params.has('owner'), 'the view replaced every key').toBe(false)
  expect(lastList(world).getAll('test_type')).toEqual(['regression'])
  await views(page).getByRole('button', { name: 'Manage', exact: true }).click()
  const manage = opened(page, 'Manage your views')
  await expect(manage.locator('[data-case-view-manage]')).toHaveCount(2)
  await expect(manage.getByText('Checkout regression')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('TCL-01 a refused save renders the server sentence in the form', async ({ page }) => {
  const sentence = 'Pick a project to share this view with its members.'
  const { world, api, errors } = await open(page, { saveRefused: sentence })
  await views(page).getByRole('button', { name: 'Save view', exact: true }).click()
  const form = opened(page, 'Save this view')
  await form.getByLabel('Name').fill('Mine')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(form.getByRole('alert')).toHaveText(sentence)
  expect(world.writes.filter((w) => w.line === `POST ${SAVED_VIEWS_API}`)).toHaveLength(1)
  assertHermetic(api, errors)
})
