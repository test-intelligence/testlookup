/**
 * W12 SRCH-03 the top-bar typeahead (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.2, §4.1, §4.2, D-8),
 * hermetic, on `/overview` (any page: the top bar is the shell's):
 *
 *  - nothing is asked under two characters or before 250 ms after the last keystroke; then ONE
 *    `GET /api/v1/search/global?q=<q>&size=15&project_id=<p>` (exactly those keys: never `record`, `page` or
 *    `entity_types`), and a key typed faster than the debounce never asks for the intermediate queries;
 *  - the list is a `role="listbox"` named `Matches` UNDER the input (never a dialog, never a portal, never wider than
 *    the input): up to five DISTINCT rows (title, subtitle, the kind's label) — one test seen in several runs is ONE
 *    row (its first, most relevant run) with ` · N runs` after its subtitle, another kind ` · N matches`, `N+` when
 *    more candidates exist than were fetched; the same title under another kind stays its own row — then
 *    `Search everything for “<q>”`;
 *  - clicking a match opens its page; the last row and Enter open `/search?q=<q>` exactly as before; Escape and a blur
 *    close it with no further request;
 *  - `No matches. Press Enter to search everything.` and `Could not load matches.`;
 *  - while closed, the input's markup is what it was before W12 (no new attribute).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts` over `OVERVIEW_ON`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { respond, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import { TEST_CASE, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import {
  GLOBAL_API,
  GLOBAL_ROWS,
  SAME_TEST_TITLE,
  SEARCH_TEXT,
  searchWorld,
  typeaheadAnswer,
  TYPEAHEAD_SAME_TEST,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const PLACEHOLDER = 'Search tests, runs, defects… (Enter)'
/** The input's attributes before W12 (`TopBar.tsx`): the typeahead adds none while the list is closed. */
const CLOSED_INPUT_ATTRIBUTES = ['class', 'placeholder', 'type', 'value']
const LABELS: Record<string, string> = { test_case: 'Tests', test_run: 'Runs', suite: 'Suites', defect: 'Defects', flaky_test: 'Flaky', release: 'Releases' }

const input = (p: Page) => p.getByPlaceholder(PLACEHOLDER)
const list = (p: Page) => p.getByRole('listbox', { name: 'Matches' })
/** The list's container: the listbox and, under it, its state line (Searching… / No matches / Could not load). */
const panel = (p: Page) => p.locator('[data-search-typeahead]')
const firstMatch = (p: Page) => list(p).getByText(GLOBAL_ROWS[0].title, { exact: true })
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })
/** The typeahead's reads: 15 candidates for five distinct rows (the Search page asks with its own size, 25). */
const typeaheadAsks = (api: Parameters<typeof requestsTo>[0]) => queryOf(api, GLOBAL_API).filter((q) => q.get('size') === '15')
const option = (p: Page, title: string, label: string) =>
  list(p).getByRole('option').filter({ has: p.getByText(title, { exact: true }) }).filter({ has: p.getByText(label, { exact: true }) })

/**
 * `GET /search/global` for the typeahead (`typeaheadAnswer`: "check…" 8 candidates / 6 distinct with one test in 3
 * runs, "test_check…" one test in 5 runs + a suite of the same name + a run, "login…" capped, nothing else), and a
 * 500 for "boom". The typeahead's reads (size 15) answer after 400 ms, so its `Searching…` state lasts long enough to
 * be seen on a loaded CI runner (Node-side: the page's pinned clock neither stretches nor skips it).
 */
const candidates = typeaheadAnswer()
async function global(request: ApiRequest) {
  const q = request.url.searchParams.get('q') ?? ''
  if (request.url.searchParams.get('size') === '15') await new Promise((resolve) => setTimeout(resolve, 400))
  if (q.startsWith('boom')) return respond(500, { detail: 'Internal Server Error' })
  return candidates(request)
}

async function open(page: Page) {
  watchNativeDialogs(page)
  const world = searchWorld({ global })
  const handlers: ApiHandlers = [...TEST_CASE, ...world.handlers, ...OVERVIEW_ON]
  const opened = await openRollout(page, '/overview', { handlers, ready })
  await networkQuiet(page, opened.api)
  return opened
}

test('two characters and 250 ms: one global read of 15, no record; five distinct rows under the input (a test in 3 runs is one row) and the footer', async ({ page }) => {
  const { api, errors } = await open(page)
  const box = input(page)
  const closed = await box.evaluate((el) => el.getAttributeNames().sort())
  expect(closed, 'closed: the input carries no new attribute').toEqual(CLOSED_INPUT_ATTRIBUTES)

  await box.fill('c')
  await page.waitForTimeout(600)
  expect(typeaheadAsks(api), 'one character: nothing is asked').toEqual([])
  await expect(list(page)).toHaveCount(0)

  await box.fill('check')
  expect(typeaheadAsks(api), 'nothing before the debounce').toEqual([])
  await expect(panel(page).getByText(SEARCH_TEXT.typeahead.searching, { exact: true }), 'Searching… while it settles').toBeVisible()
  await expect.poll(() => typeaheadAsks(api).length, { timeout: 5_000 }).toBe(1)
  expect(Object.fromEntries(typeaheadAsks(api)[0].entries())).toEqual({ q: 'check', size: '15', project_id: P })

  const listbox = list(page)
  await expect(listbox).toBeVisible()
  for (const row of GLOBAL_ROWS.slice(0, 5)) {
    await expect(listbox.getByText(row.title, { exact: true }), row.title).toHaveCount(1)
    await expect(listbox.getByText(row.subtitle, { exact: true })).toHaveCount(1)
    await expect(listbox.getByText(LABELS[row.entity_type], { exact: true })).toHaveCount(1)
  }
  await expect(listbox.getByText(GLOBAL_ROWS[5].title, { exact: true }), 'five distinct rows at most').toHaveCount(0)
  await expect(listbox.getByRole('option'), 'five rows + the footer').toHaveCount(6)
  // The test's three runs are ONE row: its first run's subtitle, then " · 3 runs"; the other runs' subtitles are not rows.
  await expect(option(page, GLOBAL_ROWS[0].title, 'Tests').locator('[data-typeahead-count]')).toHaveText('· 3 runs')
  await expect(listbox.locator('[data-typeahead-count]'), 'only the collapsed row carries a count').toHaveCount(1)
  await expect(listbox.getByText('Checkout · PASSED', { exact: true }), 'a collapsed run is not a row').toHaveCount(0)
  await expect(listbox.getByText(/^Search everything for “check”/)).toHaveCount(1)
  await expectNoOverlay(page)

  // Anchored under the input, never wider than it.
  const [inputBox, listBox] = [await box.boundingBox(), await listbox.boundingBox()]
  expect(inputBox && listBox, 'both laid out').toBeTruthy()
  if (inputBox && listBox) {
    expect(listBox.y, 'under the input').toBeGreaterThanOrEqual(inputBox.y + inputBox.height - 1)
    expect(listBox.x).toBeGreaterThanOrEqual(inputBox.x - 1)
    const wrapper = await box.locator('xpath=..').boundingBox()
    expect(listBox.x + listBox.width, 'never wider than the input wrapper').toBeLessThanOrEqual((wrapper?.x ?? 0) + (wrapper?.width ?? 0) + 1)
    console.log(`TYPEAHEAD list ${JSON.stringify({ input: Math.round(inputBox.width), list: Math.round(listBox.width), height: Math.round(listBox.height) })}`)
  }
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the same test in five runs is ONE row (" · 5 runs") that opens its first run; the same title as a suite stays its own row', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('test_check')
  const test = option(page, SAME_TEST_TITLE, 'Tests')
  const suite = option(page, SAME_TEST_TITLE, 'Suites')
  await expect(test).toHaveCount(1)
  await expect(suite, 'another kind with the same title is not merged').toHaveCount(1)
  await expect(list(page).getByRole('option'), 'test · suite · run + the footer').toHaveCount(4)
  await expect(test.locator('[data-typeahead-count]')).toHaveText('· 5 runs')
  await expect(test).toContainText('Checkout · FAILED')
  await expect(suite.locator('[data-typeahead-count]'), 'a single match carries no count').toHaveCount(0)
  await networkQuiet(page, api)
  expect(typeaheadAsks(api).map((q) => Object.fromEntries(q.entries()))).toEqual([{ q: 'test_check', size: '15', project_id: P }])
  await test.click()
  // The kept row is the server's first (the most relevant run): a whole-path compare.
  await expect(page).toHaveURL((url) => url.pathname === TYPEAHEAD_SAME_TEST[0].navigation_url)
  await expect(page.getByRole('heading', { level: 1, name: TEST_CASE_NAME })).toBeVisible()
  await expect(list(page)).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('more candidates than fetched: the counts read "3+ runs" and, for another kind, "2+ matches"', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('login')
  await expect(option(page, 'login keeps session', 'Tests').locator('[data-typeahead-count]')).toHaveText('· 3+ runs')
  await expect(option(page, 'AUTH-7', 'Defects').locator('[data-typeahead-count]')).toHaveText('· 2+ matches')
  await expect(option(page, 'Auth', 'Suites').locator('[data-typeahead-count]')).toHaveCount(0)
  await expect(list(page).getByRole('option')).toHaveCount(4)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('keys faster than the debounce ask for the last query; the list shows its answer', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).pressSequentially('check', { delay: 40 })
  await expect(firstMatch(page)).toBeVisible()
  await networkQuiet(page, api)
  const asked = typeaheadAsks(api).map((q) => q.get('q') ?? '')
  // 40 ms between keys is well inside the 250 ms debounce: locally ONE read ('check'). A loaded CI runner may stall
  // a key past it, so the invariant pinned is the one that matters: the last read is the last query, every read is
  // a 2+ character prefix of it, and fewer reads than keystrokes went out.
  expect(asked[asked.length - 1]).toBe('check')
  expect(asked.every((q) => q.length >= 2 && 'check'.startsWith(q))).toBe(true)
  expect(asked.length).toBeLessThan(4)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('clicking a match opens its page and closes the list', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('check')
  const match = list(page).getByText(GLOBAL_ROWS[0].title, { exact: true })
  await expect(match).toBeVisible()
  await match.click()
  await expect(page).toHaveURL((url) => url.pathname === TEST_CASE_PATH)
  await expect(page.getByRole('heading', { level: 1, name: TEST_CASE_NAME })).toBeVisible()
  await expect(list(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(typeaheadAsks(api)).toHaveLength(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Enter goes to /search?q=… exactly as before, and the list closes', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('check')
  await expect(firstMatch(page), 'the matches answered').toBeVisible()
  await input(page).press('Enter')
  await expect(page).toHaveURL((url) => url.pathname === '/search' && url.searchParams.get('q') === 'check')
  await expect(list(page)).toHaveCount(0)
  await expect(page.getByRole('heading', { level: 1, name: 'Search' })).toBeVisible()
  await networkQuiet(page, api)
  expect(typeaheadAsks(api), 'one typeahead read; the Search page asks with its own size').toHaveLength(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the footer row goes to /search?q=…', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('check')
  await list(page).getByText(/^Search everything for “check”/).click()
  await expect(page).toHaveURL((url) => url.pathname === '/search' && url.searchParams.get('q') === 'check')
  await expect(list(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Escape and a blur close the list with no further request', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('check')
  await expect(firstMatch(page)).toBeVisible()
  await input(page).press('Escape')
  await expect(list(page)).toHaveCount(0)
  await page.waitForTimeout(600)
  expect(typeaheadAsks(api)).toHaveLength(1)

  await input(page).fill('checkout')
  await expect(firstMatch(page)).toBeVisible()
  expect(typeaheadAsks(api).map((q) => q.get('q'))).toEqual(['check', 'checkout'])
  // A blur (the list covers the page under it, so the focus is moved away, not clicked away).
  await input(page).evaluate((el) => (el as HTMLInputElement).blur())
  await expect(list(page)).toHaveCount(0)
  await page.waitForTimeout(600)
  expect(typeaheadAsks(api)).toHaveLength(2)
  const closed = await input(page).evaluate((el) => el.getAttributeNames().sort())
  expect(closed, 'closed again: no new attribute').toEqual(CLOSED_INPUT_ATTRIBUTES)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('no match reads its own line; a failed read reads "Could not load matches."', async ({ page }) => {
  const { api, errors } = await open(page)
  await input(page).fill('zzqv')
  await expect(panel(page).getByText(SEARCH_TEXT.typeahead.none, { exact: true })).toBeVisible()
  await input(page).fill('boom')
  await expect(panel(page).getByText(SEARCH_TEXT.typeahead.error, { exact: true })).toBeVisible({ timeout: 10_000 })
  await expect(panel(page).getByText(SEARCH_TEXT.typeahead.none, { exact: true })).toHaveCount(0)
  // The list's line is the only message: no global error toast (read once, NOT retried — a toast leaves by itself
  // after a few seconds, so a waiting `toHaveCount(0)` would pass anyway).
  expect(await page.getByText('Internal Server Error').count(), 'no global error toast for a typeahead read').toBe(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
