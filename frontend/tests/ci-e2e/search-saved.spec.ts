/**
 * W12 SRCH-03 (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.2, §4.1, §4.2, D-6, D-7), hermetic: saved
 * searches on the server, recents from the server, suggested searches, and the one-time move out of the browser.
 *
 *  - **Saved searches** are `saved_views` rows (`page: "search"`, `filters: {kind: "search", q, scope, mode}`), listed
 *    by `GET /api/v1/saved-views?project_id=&page=search` in the server's order; the first three carry ⌘1-⌘3; a shared
 *    row reads `Shared`; the owner's rows have Rename / Share or Stop sharing / Delete IN the page (a form under the
 *    row, an `InlineConfirm`); a row another member shared is run-only; an All-Projects row is never shareable;
 *  - **Save search** (the Results header, or the No results card for a search that found nothing; never on a browse)
 *    opens `Save this search` at the top of that card: the name prefilled
 *    with the query (`Search: <q>` for one character), **Share with Checkout** with one project; Save → ONE
 *    `POST /api/v1/saved-views` with the whole body, the toast `Saved “<name>”.`, the row listed; a 422's sentence in
 *    the form; no cap (a fifth save is allowed);
 *  - **Recent searches** come from `GET /api/v1/search/recent`; a search the page runs is prepended at once (no
 *    re-read); picking a recent / saved / suggested row runs it in the CURRENT project;
 *  - **Suggested** (one project, box empty): three groups from `GET /api/v1/search/suggested?project_id=`; omitted
 *    when every group is empty and never asked in All Projects;
 *  - the first load moves `localStorage['tl.search.saved']` to the server (one POST per entry the server lacks, each
 *    success removed, the key deleted when empty, a failure kept) and deletes `tl.search.recent` unread;
 *  - nothing opens an overlay; the rail fits at 1280 and 1440.
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { answerConfirm, expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { boxOf, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, cardByHeading, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, observed, queryOf, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  GLOBAL_API,
  globalAnswer,
  GLOBAL_ROWS,
  NEW_SAVED_ID,
  RECENT_API,
  RECENTS,
  SAVED_SEARCHES,
  SAVED_VIEWS_API,
  savedId,
  searchWorld,
  SUGGESTED_API,
  SUGGESTIONS,
  type SearchWorldOptions,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const heading = (p: Page) => p.getByRole('heading', { level: 1, name: 'Search' })
const rail = (p: Page) => p.getByTestId('searchable-data-summary')
const savedCard = (p: Page) => cardByHeading(p, 'Saved searches')
const recentCard = (p: Page) => cardByHeading(p, 'Recent searches')
const suggestedCard = (p: Page) => cardByHeading(p, 'Suggested')
const results = (p: Page) => cardByHeading(p, /^Results/)
/** One row of a rail card (`[data-saved-search]` / `[data-recent-search]` / `[data-suggested-search]`), by its text. */
const rowOf = (card: Locator, name: string) =>
  card.locator('[data-saved-search], [data-recent-search], [data-suggested-search]').filter({ has: card.page().getByText(name, { exact: true }) })
const writesOf = (api: Parameters<typeof observed>[0], from = 0) => observed(api).slice(from).filter((line) => !line.startsWith('GET '))

async function open(page: Page, path: string, world: ReturnType<typeof searchWorld>, options: { projectId?: string; extra?: ApiHandlers } = {}) {
  watchNativeDialogs(page)
  return openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: world.user,
    user: world.user,
    projectId: options.projectId ?? P,
    handlers: [...(options.extra ?? []), ...world.handlers],
    ready: heading,
  })
}

const world = (options: SearchWorldOptions = {}) => searchWorld({ recents: RECENTS, ...options })

// ── Saved searches ─────────────────────────────────────────────────────────

test('the saved list is the server\'s: ⌘1-⌘3 on the first three, Shared badges, the owner\'s actions, a member\'s row run-only', async ({ page }) => {
  const w = world({ saved: SAVED_SEARCHES })
  const { api, errors } = await open(page, '/search', w)
  await expect(rail(page)).toBeVisible()
  const card = savedCard(page)
  for (const row of SAVED_SEARCHES) await expect(card.getByText(row.name, { exact: true })).toHaveCount(1)
  await networkQuiet(page, api)
  const asked = queryOf(api, SAVED_VIEWS_API).filter((q) => q.get('page') === 'search')
  expect(asked, 'one saved-searches read').toHaveLength(1)
  expect(Object.fromEntries(asked[0].entries())).toEqual({ project_id: P, page: 'search' })

  // The server order, the first three with their shortcut.
  const names = SAVED_SEARCHES.map((r) => r.name)
  for (const [i, name] of names.entries()) {
    const row = rowOf(card, name)
    if (i < 3) await expect(row).toContainText(`⌘${i + 1}`)
    else await expect(row).not.toContainText('⌘')
  }
  // `scope · mode · q`, and Shared on the shared rows.
  await expect(rowOf(card, 'Checkout timeouts')).toContainText('tests · keyword')
  await expect(rowOf(card, 'Checkout timeouts')).toContainText('checkout timeout')
  await expect(rowOf(card, 'Failed payments').getByText('Shared', { exact: true })).toHaveCount(1)
  await expect(rowOf(card, 'Checkout timeouts').getByText('Shared', { exact: true })).toHaveCount(0)

  // The owner's project-bound row: Rename · Share · Delete; the shared one: Stop sharing.
  await expect(rowOf(card, 'Checkout timeouts').getByRole('button', { name: /^Rename/ })).toHaveCount(1)
  await expect(rowOf(card, 'Checkout timeouts').getByRole('button', { name: /^Share/ })).toHaveCount(1)
  await expect(rowOf(card, 'Checkout timeouts').getByRole('button', { name: /^Delete/ })).toHaveCount(1)
  await expect(rowOf(card, 'Failed payments').getByRole('button', { name: /^Stop sharing/ })).toHaveCount(1)
  // The All-Projects row: no Share (it has no project to share with).
  await expect(rowOf(card, 'Login flakes').getByRole('button', { name: /^(Share|Stop sharing)/ })).toHaveCount(0)
  await expect(rowOf(card, 'Login flakes').getByRole('button', { name: /^Rename/ })).toHaveCount(1)
  // Another member's shared row: run-only.
  await expect(rowOf(card, 'Release blockers').getByRole('button', { name: /^(Rename|Share|Stop sharing|Delete)/ })).toHaveCount(0)
  expect(w.writes).toEqual([])
  assertHermetic(api, errors)
})

test('with no saved search on the server the card is not there', async ({ page }) => {
  const { api, errors } = await open(page, '/search', world({ saved: [] }))
  await expect(rail(page)).toBeVisible()
  await expect(recentCard(page)).toBeVisible()
  await networkQuiet(page, api)
  await expect(page.getByRole('heading', { name: 'Saved searches', exact: true })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('Save search: the form at the top of Results, the whole POST body, the toast, the row listed', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, '/search?q=checkout', w)
  await expect(results(page)).toBeVisible()
  await networkQuiet(page, api)
  await results(page).getByRole('button', { name: 'Save search' }).click()
  const form = opened(page, 'Save this search')
  await expect(form).toBeVisible()
  await expect(page.locator('#save-search-title')).toHaveText('Save this search')
  expect(await results(page).locator('#save-search-title').count(), 'inside the Results card').toBe(1)
  await expectNoOverlay(page)
  const name = form.getByLabel('Name')
  await expect(name).toHaveValue('checkout')
  await form.getByLabel('Share with Checkout').check()
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved “checkout”.', { exact: true })).toBeVisible()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(w.writes).toEqual([
    {
      line: `POST ${SAVED_VIEWS_API}`,
      body: { project_id: P, name: 'checkout', page: 'search', filters: { kind: 'search', q: 'checkout', scope: 'all', mode: 'keyword' }, is_shared: true },
    },
  ])
  // Cleared, the box shows the rail: the new row is in the saved list (read back from the server).
  await page.getByLabel('Clear search').click()
  await expect(savedCard(page).getByText('checkout', { exact: true }).first()).toBeVisible()
  expect(w.state.saved.map((r) => r.id)).toContain(NEW_SAVED_ID)
  assertHermetic(api, errors)
})

test('Save search: a one-character query is prefilled "Search: <q>"; Cancel sends nothing', async ({ page }) => {
  const w = world({ global: globalAnswer({ rows: () => GLOBAL_ROWS }) })
  const { api, errors } = await open(page, '/search?q=x', w)
  await networkQuiet(page, api)
  await results(page).getByRole('button', { name: 'Save search' }).click()
  const form = opened(page, 'Save this search')
  await expect(form.getByLabel('Name')).toHaveValue('Search: x')
  await form.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(w.writes).toEqual([])
  assertHermetic(api, errors)
})

test('a search that found nothing can be saved from the No results card: the form there, the whole POST body', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, '/search?q=zzqv%20nothing&scope=tests', w)
  const none = cardByHeading(page, 'No results')
  await expect(none).toBeVisible()
  await expect(page.getByRole('heading', { name: /^Results/ })).toHaveCount(0)
  await networkQuiet(page, api)
  await none.getByRole('button', { name: 'Save search' }).click()
  const form = opened(page, 'Save this search')
  expect(await none.locator('#save-search-title').count(), 'the form opens in the card that answered').toBe(1)
  await expect(form.getByLabel('Name')).toHaveValue('zzqv nothing')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved “zzqv nothing”.', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(w.writes).toEqual([
    {
      line: `POST ${SAVED_VIEWS_API}`,
      body: { project_id: P, name: 'zzqv nothing', page: 'search', filters: { kind: 'search', q: 'zzqv nothing', scope: 'tests', mode: 'keyword' }, is_shared: false },
    },
  ])
  assertHermetic(api, errors)
})

test('a browse (box empty) offers no Save search', async ({ page }) => {
  const w = world({ saved: SAVED_SEARCHES })
  const { api, errors } = await open(page, '/search', w)
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  await expect(page.getByRole('button', { name: 'Save search' })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('Save search refused: the server\'s 422 sentence renders in the form, no toast, nothing listed', async ({ page }) => {
  const sentence = 'A search saved for all projects cannot be shared: pin a project first.'
  const w = world({ saveRefused: sentence })
  const { api, errors } = await open(page, '/search?q=checkout', w)
  await expect(results(page)).toBeVisible()
  await results(page).getByRole('button', { name: 'Save search' }).click()
  const form = opened(page, 'Save this search')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(form.getByText(sentence, { exact: true })).toBeVisible()
  await expect(page.getByText(/^Saved “/)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(w.writes.map((x) => x.line)).toEqual([`POST ${SAVED_VIEWS_API}`])
  expect(w.state.saved).toEqual([])
  assertHermetic(api, errors)
})

test('no cap: with four saved, a fifth save is allowed', async ({ page }) => {
  const w = world({ saved: SAVED_SEARCHES })
  const { api, errors } = await open(page, '/search?q=checkout', w)
  await expect(results(page)).toBeVisible()
  await results(page).getByRole('button', { name: 'Save search' }).click()
  const form = opened(page, 'Save this search')
  await form.getByLabel('Name').fill('Checkout everything')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved “Checkout everything”.', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(w.state.saved).toHaveLength(5)
  assertHermetic(api, errors)
})

test('Rename, Share, Stop sharing and Delete happen under the row: the PATCH / DELETE bodies, Cancel sends nothing', async ({ page }) => {
  const w = world({ saved: SAVED_SEARCHES })
  const { api, errors } = await open(page, '/search', w)
  const card = savedCard(page)
  await expect(card.getByText('Checkout timeouts', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const own = savedId(1)

  // Rename: an in-page form under the row.
  await rowOf(card, 'Checkout timeouts').getByRole('button', { name: /^Rename/ }).click()
  const rename = opened(rowOf(card, 'Checkout timeouts'), 'Rename saved search')
  await expect(rename, 'the form opens under its row').toBeVisible()
  await expectNoOverlay(page)
  await expect(rename.getByLabel('Name')).toHaveValue('Checkout timeouts')
  await rename.getByLabel('Name').fill('Checkout timeouts (all)')
  await rename.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(card.getByText('Checkout timeouts (all)', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(w.writes).toEqual([{ line: `PATCH ${SAVED_VIEWS_API}/${own}`, body: { name: 'Checkout timeouts (all)' } }])

  // Share / Stop sharing: one PATCH each, at once.
  await rowOf(card, 'Checkout timeouts (all)').getByRole('button', { name: /^Share/ }).click()
  await expect(rowOf(card, 'Checkout timeouts (all)').getByText('Shared', { exact: true })).toBeVisible()
  await rowOf(card, 'Failed payments').getByRole('button', { name: /^Stop sharing/ }).click()
  await expect(rowOf(card, 'Failed payments').getByText('Shared', { exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(w.writes.slice(1)).toEqual([
    { line: `PATCH ${SAVED_VIEWS_API}/${own}`, body: { is_shared: true } },
    { line: `PATCH ${SAVED_VIEWS_API}/${savedId(2)}`, body: { is_shared: false } },
  ])

  // Delete: an InlineConfirm under the row; Cancel sends nothing; Delete sends the DELETE and the row goes.
  const del = () => rowOf(card, 'Checkout timeouts (all)').getByRole('button', { name: /^Delete/ })
  const asked = await answerConfirm(page, del(), 'dismiss', { scope: card })
  expect(asked.message).toBe('Delete the saved search “Checkout timeouts (all)”?')
  await networkQuiet(page, api)
  expect(w.writes).toHaveLength(3)
  await answerConfirm(page, del(), 'accept', { scope: card })
  await expect(card.getByText('Checkout timeouts (all)', { exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(w.writes.slice(3)).toEqual([{ line: `DELETE ${SAVED_VIEWS_API}/${own}`, body: null }])
  assertHermetic(api, errors)
})

test('picking a saved row runs it in the current project: its q, scope and mode', async ({ page }) => {
  const w = world({ saved: SAVED_SEARCHES })
  const { api, errors } = await open(page, '/search', w)
  const card = savedCard(page)
  await expect(card.getByText('Login flakes', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const before = queryOf(api, GLOBAL_API).length
  await card.getByText('Login flakes', { exact: true }).click()
  await expect(page).toHaveURL(/[?&]q=login(&|$)/)
  await expect(page).toHaveURL(/[?&]scope=flaky(&|$)/)
  await networkQuiet(page, api)
  const run = queryOf(api, GLOBAL_API).slice(before)
  expect(run.length).toBeGreaterThanOrEqual(1)
  const last = run[run.length - 1]
  expect([last.get('q'), last.get('entity_types'), last.get('project_id')]).toEqual(['login', 'flaky_test', P])
  assertHermetic(api, errors)
})

// ── Recent searches ────────────────────────────────────────────────────────

test('recents are the server\'s; a search the page runs is prepended at once without a re-read; picking one runs it here', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, '/search', w)
  const card = recentCard(page)
  for (const recent of RECENTS) await expect(card.getByText(recent.q, { exact: true })).toBeVisible()
  await expect(rowOf(card, RECENTS[0].q)).toContainText('hybrid · tests')
  await expect(rowOf(card, RECENTS[1].q)).toContainText('3 results')
  await networkQuiet(page, api)
  expect(requestsTo(api, RECENT_API), 'one recents read').toHaveLength(1)

  const box = page.getByLabel('Search across the workspace')
  await box.fill('checkout')
  await box.press('Enter')
  await expect(results(page)).toBeVisible()
  await networkQuiet(page, api)
  const recorded = queryOf(api, GLOBAL_API).filter((q) => q.get('q') === 'checkout')
  expect(recorded.map((q) => q.get('record')), 'the submitted page-1 search records').toEqual(['true'])
  await page.getByLabel('Clear search').click()
  await expect(card.getByText('checkout', { exact: true })).toBeVisible()
  const rows = await card.getByText(/^(checkout|checkout timeout|login flaky)$/).allTextContents()
  expect(rows[0], 'the new search is first').toBe('checkout')
  await networkQuiet(page, api)
  expect(requestsTo(api, RECENT_API), 'prepended locally: no re-read').toHaveLength(1)

  const before = queryOf(api, GLOBAL_API).length
  await card.getByText(RECENTS[1].q, { exact: true }).click()
  await expect(page).toHaveURL(/[?&]scope=tests(&|$)/)
  await networkQuiet(page, api)
  const run = queryOf(api, GLOBAL_API).slice(before)
  const last = run[run.length - 1]
  expect([last.get('q'), last.get('entity_types'), last.get('project_id')]).toEqual([RECENTS[1].q, 'test_case', P])
  assertHermetic(api, errors)
})

// ── Suggested ──────────────────────────────────────────────────────────────

test('Suggested (one project, box empty): three groups; the release row runs release:… status:failed on Tests', async ({ page }) => {
  const w = world({ suggested: SUGGESTIONS })
  const { api, errors } = await open(page, '/search', w)
  const card = suggestedCard(page)
  await expect(card).toBeVisible()
  for (const group of ['Failing in the last 7 days', 'Open defects', 'Active release']) await expect(card.getByRole('heading', { name: group, exact: true })).toBeVisible()
  await expect(card.getByText('checkout total includes tax', { exact: true })).toBeVisible()
  await expect(card).toContainText('12 failures')
  await expect(card.getByText('PAY-412', { exact: true })).toBeVisible()
  await expect(card).toContainText('Open · HIGH')
  await expect(card.getByText('Failures in 2026.11', { exact: true })).toBeVisible()
  // Top to bottom: Suggested, Recent searches.
  const [s, r] = [await boxOf(card), await boxOf(recentCard(page))]
  expect(s.top, 'Suggested above Recent searches').toBeLessThan(r.top)
  await networkQuiet(page, api)
  const asked = queryOf(api, SUGGESTED_API)
  expect(asked).toHaveLength(1)
  expect(Object.fromEntries(asked[0].entries())).toEqual({ project_id: P })

  const before = queryOf(api, GLOBAL_API).length
  await card.getByText('Failures in 2026.11', { exact: true }).click()
  await expect(page).toHaveURL(/[?&]scope=tests(&|$)/)
  await networkQuiet(page, api)
  const run = queryOf(api, GLOBAL_API).slice(before)
  const last = run[run.length - 1]
  expect([last.get('q'), last.get('entity_types'), last.get('project_id')]).toEqual(['release:2026.11 status:failed', 'test_case', P])
  assertHermetic(api, errors)
})

test('Suggested with three empty groups is asked once and omitted', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, '/search', w)
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, SUGGESTED_API)).toHaveLength(1)
  await expect(page.getByRole('heading', { name: 'Suggested', exact: true })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('All Projects (admin): no suggested read and no Suggested card', async ({ page }) => {
  const w = world({ role: 'ADMIN', suggested: SUGGESTIONS })
  const { api, errors } = await open(page, '/search', w, { projectId: 'all' })
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, SUGGESTED_API)).toEqual([])
  await expect(page.getByRole('heading', { name: 'Suggested', exact: true })).toHaveCount(0)
  const listed = queryOf(api, SAVED_VIEWS_API).filter((q) => q.get('page') === 'search')
  expect(listed.map((q) => Object.fromEntries(q.entries())), 'All Projects lists without a project').toEqual([{ page: 'search' }])
  assertHermetic(api, errors)
})

// ── The one-time move out of the browser ───────────────────────────────────

/** Two legacy saved searches as SearchPage kept them (`tl.search.saved`), the first already on the server. */
const LEGACY_SAVED = [
  { id: 's1', label: 'Checkout timeouts', query: 'checkout timeout', mode: 'keyword', scope: 'tests', slot: 1 },
  { id: 's2', label: 'Coupon failures', query: 'coupon', mode: 'keyword', scope: 'all', slot: 2 },
]
const LEGACY_RECENT = [{ id: 'r1', query: 'old local query', mode: 'keyword', scope: 'all', resultCount: 4, ts: Date.parse('2026-09-17T09:00:00Z') }]

async function seedLegacy(page: Page) {
  await page.addInitScript(({ saved, recent }) => {
    if (!sessionStorage.getItem('w12-seeded')) {
      localStorage.setItem('tl.search.saved', JSON.stringify(saved))
      localStorage.setItem('tl.search.recent', JSON.stringify(recent))
      sessionStorage.setItem('w12-seeded', '1')
    }
  }, { saved: LEGACY_SAVED, recent: LEGACY_RECENT })
}
const stored = (page: Page) => page.evaluate(() => [localStorage.getItem('tl.search.saved'), localStorage.getItem('tl.search.recent')])

test('first load moves the browser\'s saved searches: one POST per missing entry, the keys gone, local recents dropped', async ({ page }) => {
  await seedLegacy(page)
  const w = world({ saved: SAVED_SEARCHES.slice(0, 1) })
  const { api, errors } = await open(page, '/search', w)
  await expect(savedCard(page).getByText('Coupon failures', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(w.writes).toEqual([
    {
      line: `POST ${SAVED_VIEWS_API}`,
      body: { project_id: P, name: 'Coupon failures', page: 'search', filters: { kind: 'search', q: 'coupon', scope: 'all', mode: 'keyword' }, is_shared: false },
    },
  ])
  expect(await stored(page), 'both keys deleted').toEqual([null, null])
  await expect(page.getByText('old local query', { exact: true }), 'local recents are not moved').toHaveCount(0)
  // A reload moves nothing again.
  await page.reload()
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  expect(writesOf(api).filter((line) => line.startsWith('POST '))).toHaveLength(1)
  assertHermetic(api, errors)
})

test('a move that fails keeps its entry for the next load', async ({ page }) => {
  await seedLegacy(page)
  const w = world({ saved: SAVED_SEARCHES.slice(0, 1), saveRefused: 'A saved search holds only kind, q, scope and mode.' })
  const { api, errors } = await open(page, '/search', w)
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  expect(w.writes.map((x) => x.line)).toEqual([`POST ${SAVED_VIEWS_API}`])
  const [saved, recent] = await stored(page)
  expect(recent, 'local recents are deleted unread').toBeNull()
  expect(JSON.parse(saved ?? '[]').map((e: { query: string }) => e.query), 'the failed entry stays').toEqual(['coupon'])
  assertHermetic(api, errors)
})

// ── Geometry ───────────────────────────────────────────────────────────────

for (const width of [1280, 1440]) {
  test(`@${width}: Suggested, Recent and Saved in the left column fit, the saved row keeps room for its query`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const w = world({ saved: SAVED_SEARCHES, suggested: SUGGESTIONS })
    const { api, errors } = await open(page, '/search', w)
    const card = savedCard(page)
    await expect(card.getByText('Checkout timeouts', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    await expectNoHorizontalOverflow(page, `/search rail @${width}`)
    const row = rowOf(card, 'Checkout timeouts')
    const [cardBox, rowSlack] = [await boxOf(card), await minContentSlack(row)]
    // The owner's SHARED row (Rename · Stop sharing · Delete, the widest actions): its Shared badge is on screen,
    // not clipped by a truncating line — the element at the badge's centre is the badge.
    const shared = rowOf(card, 'Failed payments')
    const badge = shared.locator('[data-saved-search-shared]')
    await expect(badge).toBeVisible()
    // On screen first (the rail runs below the fold of a 1000 px window; a point off screen hits nothing).
    await badge.scrollIntoViewIfNeeded()
    const badgeHit = await badge.evaluate((el) => {
      const box = el.getBoundingClientRect()
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
      return !!hit && (hit === el || el.contains(hit))
    })
    expect(badgeHit, 'the Shared badge is visible, not clipped').toBe(true)
    const [sharedSlack, badgeBox, sharedBox] = [await minContentSlack(shared), await boxOf(badge), await boxOf(shared)]
    expect(badgeBox.right, 'the badge ends inside its row').toBeLessThanOrEqual(sharedBox.right)
    inPageLine('/search saved-row', width, { card: cardBox.width, rowSlack, sharedSlack })
    expect(rowSlack, 'the saved row is not wider than its card').toBeGreaterThanOrEqual(0)
    expect(sharedSlack, 'the shared row is not wider than its card').toBeGreaterThanOrEqual(0)
    assertHermetic(api, errors)
  })
}
