/**
 * W12 SRCH-01 (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.1, §4.1, §4.2), hermetic: Hybrid and Semantic
 * on the Tests scope, the honest fallback, and Re-index now.
 *
 *  - the Mode chips Keyword · Hybrid · Semantic: Hybrid and Semantic are offered on a healthy index and DISABLED, with
 *    the server's reason as their title, on a stack without ChromaDB;
 *  - choosing Semantic on All switches the scope to Tests with ONE status line; choosing another scope then switches
 *    the mode back to Keyword with the other line; the deep link `?mode=semantic&scope=all` loads as Tests (the URL
 *    rewritten) with the first line;
 *  - a Semantic / Hybrid search is `GET /api/v1/search?search_type=…` (a submitted page-1 search carries
 *    `record=true`); its rows are test results (`<suite ?? 'No suite'> · <status>`) that open `/runs/:run/tests/:id`;
 *    the answer is sampled, so the lower-bound banner shows;
 *  - each fallback reason shows ITS sentence above the keyword rows, and the header's mode code reads what answered;
 *  - **Re-index now** (QA lead / admin with one project): `POST /search/reindex?project_id=…&full=true`, the toast,
 *    **Queued**, the queued line, and the 10 s poll that ends it; a refusal's sentence inline; no button for a QA
 *    engineer, a viewer, or All Projects;
 *  - nothing scrolls sideways at 1280 and 1440, and nothing opens an overlay.
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, cardByHeading, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, observed, queryOf, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { TEST_CASE, TEST_CASE_ID, TEST_CASE_NAME } from '../visual/production/fixtures-pages'
import {
  INDEX_NOT_CONFIGURED,
  INDEX_READY,
  INDEX_STATUS_API,
  KEYWORD_ROWS,
  REINDEX_API,
  SEARCH_API,
  SEARCH_TEXT,
  searchWorld,
  semanticAnswer,
  SEMANTIC_ROWS,
  type SearchRole,
  type SearchWorldOptions,
  type SemanticOutcome,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const QUERY = 'payment timeout'

const heading = (p: Page) => p.getByRole('heading', { level: 1, name: 'Search' })
const rail = (p: Page) => p.getByTestId('searchable-data-summary')
const modeChip = (p: Page, name: 'Keyword' | 'Hybrid' | 'Semantic') =>
  p.getByRole('radiogroup', { name: 'Search mode' }).getByRole('radio', { name, exact: true })
const scopeChip = (p: Page, name: string) =>
  p.getByRole('radiogroup', { name: 'Entity scope' }).getByRole('radio', { name: new RegExp(`^${name}\\b`) })
const statusLine = (p: Page, text: string) => p.getByRole('status').filter({ hasText: text })
const results = (p: Page) => cardByHeading(p, /^Results/)
const resultRow = (p: Page, name: string) => results(p).getByRole('button').filter({ hasText: name })
const semanticLine = (p: Page) => p.getByTestId('semantic-index-line')

async function open(page: Page, path: string, world: { handlers: ApiHandlers; user: unknown }, options: { projectId?: string; extra?: ApiHandlers } = {}) {
  watchNativeDialogs(page)
  return openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: world.user,
    user: world.user as Record<string, unknown>,
    projectId: options.projectId ?? P,
    handlers: [...(options.extra ?? []), ...world.handlers],
    ready: heading,
  })
}

const world = (options: SearchWorldOptions = {}) => searchWorld(options)

test('a healthy index offers Hybrid and Semantic beside Keyword', async ({ page }) => {
  const { api, errors } = await open(page, '/search', world({ indexStatus: INDEX_READY }))
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  for (const name of ['Keyword', 'Hybrid', 'Semantic'] as const) await expect(modeChip(page, name)).toBeEnabled()
  await expect(modeChip(page, 'Keyword')).toHaveAttribute('aria-checked', 'true')
  expect(requestsTo(api, SEARCH_API), 'nothing semantic is asked on load').toEqual([])
  assertHermetic(api, errors)
})

test('without ChromaDB, Hybrid and Semantic are disabled with the server\'s reason as their title', async ({ page }) => {
  const { api, errors } = await open(page, '/search', world({ indexStatus: INDEX_NOT_CONFIGURED }))
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  await expect(modeChip(page, 'Keyword')).toBeEnabled()
  for (const name of ['Hybrid', 'Semantic'] as const) {
    await expect(modeChip(page, name), `${name} is disabled`).toBeDisabled()
    await expect(modeChip(page, name)).toHaveAttribute('title', SEARCH_TEXT.semanticReason.not_configured)
  }
  assertHermetic(api, errors)
})

test('Semantic on All moves the scope to Tests with one line; another scope moves the mode back to Keyword with the other', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, `/search?q=${encodeURIComponent(QUERY)}`, w)
  await expect(scopeChip(page, 'All')).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  expect(requestsTo(api, SEARCH_API), 'Keyword never asks GET /search').toEqual([])

  await modeChip(page, 'Semantic').click()
  await expect(modeChip(page, 'Semantic')).toHaveAttribute('aria-checked', 'true')
  await expect(scopeChip(page, 'Tests')).toHaveAttribute('aria-checked', 'true')
  await expect(statusLine(page, SEARCH_TEXT.scopeSwitched)).toHaveCount(1)
  await expect(page).toHaveURL(/[?&]scope=tests(&|$)/)
  await expect(page).toHaveURL(/[?&]mode=semantic(&|$)/)
  await networkQuiet(page, api)
  const asked = queryOf(api, SEARCH_API)
  expect(asked.length, 'one GET /search for the semantic run').toBeGreaterThanOrEqual(1)
  const last = asked[asked.length - 1]
  expect(last.get('search_type')).toBe('semantic')
  expect(last.get('q')).toBe(QUERY)
  expect(last.get('project_id')).toBe(P)

  // The rows are test results by meaning: `<suite ?? 'No suite'> · <status>`, sampled (lower bounds).
  for (const row of SEMANTIC_ROWS) await expect(resultRow(page, row.test_name)).toHaveCount(1)
  await expect(resultRow(page, SEMANTIC_ROWS[0].test_name)).toContainText('Checkout · FAILED')
  await expect(resultRow(page, SEMANTIC_ROWS[2].test_name)).toContainText('No suite · FAILED')
  await expect(results(page).locator('code').filter({ hasText: /^semantic$/ })).toHaveCount(1)
  await expect(page.getByText(SEARCH_TEXT.lowerBound)).toBeVisible()
  await expectNoOverlay(page)

  await scopeChip(page, 'Runs').click()
  await expect(scopeChip(page, 'Runs')).toHaveAttribute('aria-checked', 'true')
  await expect(modeChip(page, 'Keyword')).toHaveAttribute('aria-checked', 'true')
  await expect(statusLine(page, SEARCH_TEXT.modeSwitched)).toHaveCount(1)
  await expect(statusLine(page, SEARCH_TEXT.scopeSwitched), 'the first line went with the change').toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the deep link ?mode=semantic&scope=all loads as Tests (the URL rewritten) with the line', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, `/search?q=${encodeURIComponent(QUERY)}&mode=semantic&scope=all`, w)
  await expect(scopeChip(page, 'Tests')).toHaveAttribute('aria-checked', 'true')
  await expect(modeChip(page, 'Semantic')).toHaveAttribute('aria-checked', 'true')
  await expect(statusLine(page, SEARCH_TEXT.scopeSwitched)).toHaveCount(1)
  await expect(page).toHaveURL(/[?&]scope=tests(&|$)/)
  await networkQuiet(page, api)
  for (const params of queryOf(api, SEARCH_API)) expect(params.get('search_type')).toBe('semantic')
  expect(queryOf(api, SEARCH_API).length).toBeGreaterThanOrEqual(1)
  await expect(resultRow(page, SEMANTIC_ROWS[0].test_name)).toHaveCount(1)
  assertHermetic(api, errors)
})

test('a submitted Semantic search carries record=true; a row opens its test result', async ({ page }) => {
  const w = world()
  const { api, errors } = await open(page, '/search?mode=semantic&scope=tests', w, { extra: TEST_CASE })
  await networkQuiet(page, api)
  const box = page.getByLabel('Search across the workspace')
  await box.fill(QUERY)
  const before = queryOf(api, SEARCH_API).length
  await box.press('Enter')
  await expect(resultRow(page, SEMANTIC_ROWS[0].test_name)).toHaveCount(1)
  await networkQuiet(page, api)
  const submitted = queryOf(api, SEARCH_API).slice(before)
  expect(submitted, 'the submit asked GET /search once').toHaveLength(1)
  expect(Object.fromEntries(submitted[0].entries())).toMatchObject({ q: QUERY, search_type: 'semantic', project_id: P, page: '1', record: 'true' })

  await resultRow(page, SEMANTIC_ROWS[0].test_name).click()
  await expect(page).toHaveURL((url) => url.pathname === `/runs/${RUN_ID}/tests/${TEST_CASE_ID}`)
  await expect(page.getByRole('heading', { level: 1, name: TEST_CASE_NAME })).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

const FALLBACKS: { outcome: Exclude<SemanticOutcome, 'answered'>; q: string; mode: 'semantic' | 'hybrid' }[] = [
  { outcome: 'semantic_unavailable', q: QUERY, mode: 'semantic' },
  { outcome: 'no_semantic_match', q: 'zzqv nothing like it', mode: 'semantic' },
  { outcome: 'no_text', q: 'status:failed', mode: 'hybrid' },
]

for (const { outcome, q, mode } of FALLBACKS) {
  test(`fallback ${outcome}: its sentence above the keyword rows, and the mode code reads keyword`, async ({ page }) => {
    const w = world({ semantic: semanticAnswer(outcome) })
    const { api, errors } = await open(page, `/search?q=${encodeURIComponent(q)}&mode=${mode}&scope=tests`, w)
    const banner = page.getByText(SEARCH_TEXT.fallback[outcome], { exact: true })
    await expect(banner).toBeVisible()
    for (const other of Object.entries(SEARCH_TEXT.fallback).filter(([key]) => key !== outcome)) {
      await expect(page.getByText(other[1], { exact: true }), 'one banner, its own').toHaveCount(0)
    }
    for (const row of KEYWORD_ROWS) await expect(resultRow(page, row.test_name)).toHaveCount(1)
    for (const row of SEMANTIC_ROWS) await expect(resultRow(page, row.test_name)).toHaveCount(0)
    await expect(results(page).locator('code').filter({ hasText: /^keyword$/ })).toHaveCount(1)
    // The banner sits above the Results card.
    const [bannerBox, resultsBox] = [await banner.boundingBox(), await results(page).boundingBox()]
    expect(bannerBox && resultsBox && bannerBox.y < resultsBox.y, 'the banner is above the results').toBe(true)
    await expect(page.getByText(SEARCH_TEXT.lowerBound), 'a keyword answer is exact').toHaveCount(0)
    await networkQuiet(page, api)
    for (const params of queryOf(api, SEARCH_API)) expect(params.get('search_type')).toBe(mode)
    assertHermetic(api, errors)
  })
}

for (const width of [1280, 1440]) {
  test(`@${width}: the semantic results, the line and a fallback banner fit (no sideways scroll)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const w = world({ semantic: semanticAnswer('semantic_unavailable') })
    const { api, errors } = await open(page, `/search?q=${encodeURIComponent(QUERY)}&mode=semantic&scope=all`, w)
    await expect(statusLine(page, SEARCH_TEXT.scopeSwitched)).toHaveCount(1)
    await expect(page.getByText(SEARCH_TEXT.fallback.semantic_unavailable, { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    await expectNoHorizontalOverflow(page, `/search semantic @${width}`)
    const main = await page.locator('#main-content').boundingBox()
    const banner = await page.getByText(SEARCH_TEXT.fallback.semantic_unavailable, { exact: true }).boundingBox()
    console.log(`SEARCH semantic-fallback @${width} ${JSON.stringify({ main: Math.round(main?.width ?? 0), banner: Math.round(banner?.width ?? 0) })}`)
    assertHermetic(api, errors)
  })
}

// ── Re-index now (the rail's semantic line) ────────────────────────────────

test('Re-index now (QA lead, one project): the POST, the toast, Queued, the queued line; the poll ends it', async ({ page }) => {
  const w = world({ indexStatus: INDEX_READY })
  const { api, errors } = await open(page, '/search', w)
  const line = semanticLine(page)
  const button = line.getByRole('button', { name: 'Re-index now' })
  await expect(button).toBeVisible()
  await networkQuiet(page, api)
  const statusReads = requestsTo(api, INDEX_STATUS_API).length

  await button.click()
  await expect(page.getByText(SEARCH_TEXT.reindexQueuedToast, { exact: true })).toBeVisible()
  await expect(line.getByRole('button', { name: 'Queued' })).toBeDisabled()
  await expect(line).toContainText('Re-index queued at 12:00 UTC — this line updates when it finishes.')
  expect(w.writes).toEqual([{ line: `POST ${REINDEX_API}?project_id=${P}&full=true`, body: null }])
  await expectNoOverlay(page)

  // `useSearchIndexStatus` polls every 10 s while the re-index this page queued is pending; the next read is later
  // than the queue time, so the line goes back to the index's own state.
  await expect(line).toContainText('2,296 docs', { timeout: 20_000 })
  await expect(line).not.toContainText('Re-index queued at')
  expect(requestsTo(api, INDEX_STATUS_API).length, 'the status was polled').toBeGreaterThan(statusReads)
  const pollsAfterDone = requestsTo(api, INDEX_STATUS_API).length
  await page.waitForTimeout(11_000)
  expect(requestsTo(api, INDEX_STATUS_API).length, 'the poll stops once it finished').toBe(pollsAfterDone)
  assertHermetic(api, errors)
})

test('a refused Re-index shows the server\'s sentence under the line, nothing else changes', async ({ page }) => {
  const w = world({ reindexRefused: true })
  const { api, errors } = await open(page, '/search', w)
  const line = semanticLine(page)
  await line.getByRole('button', { name: 'Re-index now' }).click()
  await expect(page.getByText(SEARCH_TEXT.reindexRefused, { exact: true })).toBeVisible()
  await expect(line.getByRole('button', { name: 'Queued' })).toHaveCount(0)
  await expect(line).not.toContainText('Re-index queued at')
  await networkQuiet(page, api)
  expect(w.writes).toEqual([{ line: `POST ${REINDEX_API}?project_id=${P}&full=true`, body: null }])
  assertHermetic(api, errors)
})

for (const role of ['ADMIN', 'QA_ENGINEER', 'VIEWER'] as SearchRole[]) {
  test(`Re-index now as ${role} with one project: ${role === 'ADMIN' ? 'offered' : 'never offered'}`, async ({ page }) => {
    const w = world({ role })
    const { api, errors } = await open(page, '/search', w)
    await expect(rail(page)).toBeVisible()
    await networkQuiet(page, api)
    await expect(semanticLine(page).getByRole('button', { name: 'Re-index now' })).toHaveCount(role === 'ADMIN' ? 1 : 0)
    expect(w.writes).toEqual([])
    assertHermetic(api, errors)
  })
}

test('Re-index now as ADMIN in All Projects: not offered (no instance-wide button, D-13)', async ({ page }) => {
  const w = world({ role: 'ADMIN' })
  const { api, errors } = await open(page, '/search', w, { projectId: 'all' })
  await expect(rail(page)).toBeVisible()
  await networkQuiet(page, api)
  await expect(semanticLine(page).getByRole('button', { name: 'Re-index now' })).toHaveCount(0)
  expect(observed(api).filter((line) => !line.startsWith('GET ')), 'All Projects sends no write').toEqual([])
  assertHermetic(api, errors)
})
