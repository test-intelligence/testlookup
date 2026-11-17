/**
 * HON-08, hermetic: the /search right rail on a stack without ChromaDB (the
 * `make dev` default) and on one whose index has been idle for hours.
 *
 * The rail was "Index health": one vector-index status, plus a 5-minute
 * staleness rule, copied onto all six entity rows, so both stacks showed
 * Tests, Runs, Suites, Defects, Flaky and Releases "Down" although global
 * search is a SQL keyword match over each table. It is now "Searchable data":
 * six keyword rows with their row counts, and a separate line for the
 * semantic index (test cases only).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { networkQuiet, openRollout } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { LAYOUT } from '../visual/production/fixtures'
import { SEARCH_LOAD_READS } from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const COUNTS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }
const ENTITIES = ['Tests', 'Runs', 'Suites', 'Defects', 'Flaky', 'Releases']

function searchHandlers(indexStatus: Record<string, unknown>): ApiHandlers {
  return [
    // W12 (contract §4.2): every `/search` load also reads the reader's recents, the project's suggestions and (a
    // QA lead, this spec's user) the usage tiles; answered empty here (this spec is about the rail).
    ...SEARCH_LOAD_READS,
    ...LAYOUT,
    ['/api/v1/search/index-status', () => indexStatus],
    ['/api/v1/search/entity-counts', () => COUNTS],
    ['/api/v1/search/global', ({ url }) => ({
      items: [], total: 0, query: url.searchParams.get('q') ?? '', search_type: 'keyword',
      entity_counts: COUNTS, page: 1, size: 25, pages: 0,
    })],
  ]
}

const ready = (p: Page) => p.getByTestId('searchable-data-summary')

test('without ChromaDB the rail lists six keyword-searchable entities and says the semantic index is not configured', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/search', {
    handlers: searchHandlers({ status: 'not_configured', document_count: 0, last_indexed_at: null }),
    ready,
  })
  await networkQuiet(page, api)

  await expect(ready(page)).toHaveText('6 entity types · semantic: not configured')
  for (const entity of ENTITIES) {
    await expect(page.getByLabel(`${entity}: keyword search`)).toHaveText('Keyword')
  }
  await expect(page.getByText('84 rows · keyword match', { exact: true })).toBeVisible()
  await expect(page.getByText('4 rows · keyword match', { exact: true })).toBeVisible()
  const semantic = page.getByTestId('semantic-index-line')
  await expect(semantic.getByLabel('Semantic index: Not configured')).toBeVisible()
  await expect(semantic).toContainText('No ChromaDB server answers here')
  await expect(page.getByText('Down', { exact: true })).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('an index last written hours ago is idle, not down', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/search', {
    handlers: searchHandlers({
      status: 'healthy',
      document_count: 140,
      // Hours before the harness's pinned clock: the old rule called anything past 5 minutes "Down".
      last_indexed_at: '2020-01-01T00:00:00Z',
    }),
    ready,
  })
  await networkQuiet(page, api)

  await expect(ready(page)).toHaveText('6 entity types · semantic: ready')
  const semantic = page.getByTestId('semantic-index-line')
  await expect(semantic.getByLabel('Semantic index: Ready')).toBeVisible()
  await expect(semantic).toContainText('140 docs · last indexed')
  await expect(page.getByText('Down', { exact: true })).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

// ── D-0013 / D-0015 (GPT-6 exploratory, E2E-0015, E2E-0017): every number on /search names its population ──

/** `/search` with these inventory counts, this semantic index, and this answer to `/search/global`. */
function countedWorld(
  counts: Record<string, number>,
  indexStatus: Record<string, unknown>,
  global: (url: URL) => Record<string, unknown> = (url) => ({
    items: [], total: 0, query: url.searchParams.get('q') ?? '', search_type: 'keyword',
    entity_counts: counts, page: 1, size: 25, pages: 0,
  }),
): ApiHandlers {
  return [
    ...SEARCH_LOAD_READS,
    ...LAYOUT,
    ['/api/v1/search/index-status', () => indexStatus],
    ['/api/v1/search/entity-counts', () => counts],
    ['/api/v1/search/global', ({ url }) => global(url)],
  ]
}

const verdict = (page: Page) => page.getByRole('region', { name: 'Index verdict' })
const scopeChip = (page: Page, name: string) =>
  page.getByRole('radiogroup', { name: 'Entity scope' }).getByRole('radio', { name: new RegExp(`^${name}`) })

test('D-0013: a Flaky search that finds a test never shows "Flaky 0" beside it', async ({ page }) => {
  // The project has a flaky history but no open quarantine request: entity-counts says flaky_test 0.
  const counts = { test_case: 84, test_run: 39, suite: 5, defect: 0, flaky_test: 0, release: 28 }
  const flakyHit = {
    entity_type: 'flaky_test', entity_id: 'p1:fp-gpt6', title: 'test_gpt6_alternate',
    subtitle: '56% failure rate over 9 runs', project_id: null, project_name: null,
    navigation_url: '/flaky', relevance_score: 0.6, match_reasons: ['Matched test name'], metadata: {},
  }
  const { api, errors } = await openRollout(page, '/search?mode=keyword&scope=flaky&q=GPT6', {
    handlers: countedWorld(counts, { status: 'not_configured', document_count: 0, last_indexed_at: null }, (url) => ({
      items: [flakyHit], total: 1, query: url.searchParams.get('q') ?? '', search_type: 'keyword',
      entity_counts: { test_case: 0, test_run: 0, suite: 0, defect: 0, flaky_test: 1, release: 0 },
      page: 1, size: 25, pages: 1,
    })),
    ready: (p) => p.getByText('test_gpt6_alternate').first(),
  })
  await networkQuiet(page, api)
  // The narrowed scope shows the inventory, and Flaky has none of its own: no number, a reason.
  await expect(scopeChip(page, 'Flaky')).toHaveText('Flaky')
  await expect(scopeChip(page, 'Flaky')).toHaveAttribute('title', /run history/)
  // All with the query: every chip is the per-type match count, Flaky's 1 included.
  await scopeChip(page, 'All').click()
  await expect(scopeChip(page, 'Flaky')).toHaveText(/^Flaky\s*1$/)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
