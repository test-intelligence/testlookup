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

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const COUNTS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }
const ENTITIES = ['Tests', 'Runs', 'Suites', 'Defects', 'Flaky', 'Releases']

function searchHandlers(indexStatus: Record<string, unknown>): ApiHandlers {
  return [
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
