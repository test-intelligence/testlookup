/**
 * W12 "search" baselines (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §6.5, the 9 NEW regions), in
 * signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `/search`, box empty, a QA lead in Checkout: `search-usage-strip` (the three tiles under the ribbon, measured),
 *    `search-rail-lists` (the left column: Suggested · Recent searches · Saved searches with the owner's actions and
 *    a member's run-only row), `search-searchable-data` (the rail card after **Re-index now**: Queued and the queued
 *    line), `search-typeahead` (the top-bar list for "check": five DISTINCT matches — the test seen in three runs is one
 *    row with · 3 runs — and the footer);
 *  - `/search` Semantic on Tests with the index down: `search-semantic-fallback` (the warning banner);
 *  - `/search` with `status:failed since:7d foo:bar checkout`: `search-syntax-chips` (the command bar with the
 *    Filters row, two chips, the muted ignored-token note and four warning notes; Syntax closed);
 *  - `/runs/:run/tests/:test?tab=similar`: `test-similar-failures` (the fourth tab's five links);
 *  - `/settings/ai` (an admin, a re-embed in progress): `ai-config-embedding-card`;
 *  - `/settings/storage` (an admin, prefix `demo` in use): `storage-chroma-card`.
 *
 * CHANGED: none (§6.5 proves each candidate). The focus ring is not part of a baseline: the page is blurred before
 * each region, except the typeahead's (a blur closes that list by design; its region is the list, not the input).
 * Hermetic fixtures: `production/fixtures-search.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux);
 * nobody regenerates them locally (a local run passes `--update-snapshots=none` to prove each region is reached).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, cardAround, openProductionPage, PINNED, THEMES_WITH_PAPER, visualRegion, type ApiHandlers, type Theme } from '../lib/production-pages'
import { networkQuiet } from '../lib/rollout'
import { NOW, PROJECT_ID } from './production/fixtures'
import { TEST_CASE_NAME, TEST_CASE_PATH } from './production/fixtures-pages'
import {
  ADMIN,
  embeddingWorld,
  INDEX_BUILDING,
  INDEX_MINILM_IN_SYNC,
  INDEX_NOT_CONFIGURED,
  INDEX_READY,
  RECENTS,
  SAVED_SEARCHES,
  SEARCH_TEXT,
  searchWorld,
  semanticAnswer,
  similarWorld,
  SUGGESTIONS,
  SYNTAX_REGION_QUERY,
  syntaxGlobal,
  typeaheadAnswer,
  USAGE_MEASURED,
} from './production/fixtures-search'

test.use(PINNED)

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const settingsCard = (page: Page, name: string) =>
  page.locator('[data-settings-form-grid] > *').filter({ has: page.getByRole('heading', { name, exact: true }) })

async function open(page: Page, path: string, theme: Theme, handlers: ApiHandlers, user: Record<string, unknown>, ready: (p: Page) => Locator) {
  return openProductionPage(page, path, { theme, now: NOW, me: user, user, projectId: PROJECT_ID, handlers, ready })
}

for (const theme of THEMES_WITH_PAPER) {
  test(`/search, box empty: the usage strip, the rail lists, Searchable data after Re-index now, the typeahead — ${theme}`, async ({ page }) => {
    // The re-index's later polls answer the same status, so the queued line holds still while it is shot.
    // The typeahead's candidates: one test in three runs among them (`typeaheadAnswer`), so the region shows a count.
    const world = searchWorld({ recents: RECENTS, saved: SAVED_SEARCHES, suggested: SUGGESTIONS, usage: USAGE_MEASURED, indexStatus: INDEX_READY, indexAfterReindex: INDEX_READY, global: typeaheadAnswer() })
    const { api, errors } = await open(page, '/search', theme, world.handlers, world.user, (p) => p.getByTestId('searchable-data-summary'))
    await expect(page.locator('[data-usage-tile="zeroRate"]')).toContainText('33 %')
    await expect(page.getByTestId('saved-searches').getByText('Release blockers', { exact: true })).toBeVisible()
    await expect(page.getByTestId('suggested-searches')).toBeVisible()
    await networkQuiet(page, api)

    await blur(page)
    await visualRegion(page, 'search-usage-strip', theme, page.locator('[data-search-usage]'))

    await blur(page)
    await visualRegion(page, 'search-rail-lists', theme, page.getByTestId('recent-searches').locator('xpath=..'))

    const line = page.getByTestId('semantic-index-line')
    await line.getByRole('button', { name: 'Re-index now' }).click()
    await expect(line).toContainText('Re-index queued at 12:00 UTC — this line updates when it finishes.')
    await expect(page.getByText(SEARCH_TEXT.reindexQueuedToast, { exact: true }), 'the toast is gone before the shot').toHaveCount(0, { timeout: 15_000 })
    await blur(page)
    await visualRegion(page, 'search-searchable-data', theme, cardAround(page.getByTestId('searchable-data-summary')))

    const top = page.getByPlaceholder('Search tests, runs, defects… (Enter)')
    await top.fill('check')
    const list = page.locator('[data-search-typeahead]')
    await expect(list.getByRole('listbox', { name: 'Matches' }).getByRole('option')).toHaveCount(6)
    await expect(list.locator('[data-typeahead-count]')).toHaveText('· 3 runs')
    await networkQuiet(page, api)
    // No blur here: a blur closes the list (150 ms later) by design.
    await visualRegion(page, 'search-typeahead', theme, list)
    assertHermetic(api, errors)
  })

  test(`/search Semantic on Tests, the index down: the fallback banner — ${theme}`, async ({ page }) => {
    const world = searchWorld({ indexStatus: INDEX_NOT_CONFIGURED, semantic: semanticAnswer('semantic_unavailable') })
    const { api, errors } = await open(page, `/search?q=${encodeURIComponent('payment timeout')}&mode=semantic&scope=tests`, theme, world.handlers, world.user, h1('Search'))
    const banner = page.locator('[data-search-fallback="semantic_unavailable"]')
    await expect(banner).toHaveText(SEARCH_TEXT.fallback.semantic_unavailable)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'search-semantic-fallback', theme, banner)
    assertHermetic(api, errors)
  })

  test(`/search with filters, an ignored token and unsupported kinds: the command bar's chips and notes — ${theme}`, async ({ page }) => {
    const world = searchWorld({ global: syntaxGlobal() })
    const { api, errors } = await open(page, `/search?q=${encodeURIComponent(SYNTAX_REGION_QUERY)}&scope=all`, theme, world.handlers, world.user, h1('Search'))
    await expect(page.locator('[data-search-notes] li')).toHaveCount(5)
    await expect(page.locator('[data-search-chip]')).toHaveCount(2)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'search-syntax-chips', theme, page.locator('section[aria-label="Search"]'))
    assertHermetic(api, errors)
  })

  test(`a failed result's Similar failures tab — ${theme}`, async ({ page }) => {
    const world = similarWorld()
    const { api, errors } = await open(page, `${TEST_CASE_PATH}?tab=similar`, theme, world.handlers, searchWorld().user, h1(TEST_CASE_NAME))
    const panel = page.locator('[data-test-case-tab="similar"]')
    await expect(panel.getByRole('link')).toHaveCount(5)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'test-similar-failures', theme, panel)
    assertHermetic(api, errors)
  })

  test(`Settings › AI: the Embedding card while a re-embed runs — ${theme}`, async ({ page }) => {
    const world = embeddingWorld({ index: INDEX_BUILDING, pollsUntilDone: 1_000 })
    const { api, errors } = await open(page, '/settings/ai', theme, world.handlers, ADMIN, h1('AI Configuration'))
    const card = settingsCard(page, 'Embedding')
    await expect(card.locator('[data-embedding-index]')).toContainText('Test results 1,200 / 2,290')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'ai-config-embedding-card', theme, card)
    assertHermetic(api, errors)
  })

  test(`Settings › Data & storage: the ChromaDB card with the prefix demo in use — ${theme}`, async ({ page }) => {
    const demo = { ...INDEX_MINILM_IN_SYNC, active: { ...INDEX_MINILM_IN_SYNC.active, prefix: 'demo' }, configured: { ...INDEX_MINILM_IN_SYNC.configured, prefix: 'demo' } }
    const world = embeddingWorld({ index: demo, storedPrefix: 'demo' })
    const { api, errors } = await open(page, '/settings/storage', theme, world.handlers, ADMIN, h1('Data & Storage'))
    const card = settingsCard(page, 'ChromaDB (Vector Store)')
    await expect(card).toContainText('In use: collections start with “demo_”.')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'storage-chroma-card', theme, card)
    assertHermetic(api, errors)
  })
}
