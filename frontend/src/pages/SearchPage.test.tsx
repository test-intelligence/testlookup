import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'
import type { GlobalSearchResponse, GlobalSearchResult, IndexStatus } from '@/types/search'

// Per-test mock handles so we can swap return values without re-defining
// the whole searchService shape each time.
const mockGlobalSearch = vi.fn()
const mockGetIndexStatus = vi.fn()
const mockGetEntityCounts = vi.fn()

vi.mock('@/services/searchService', () => ({
  searchService: {
    search:           vi.fn().mockResolvedValue({ items: [], total: 0 }),
    globalSearch:     (...args: unknown[]) => mockGlobalSearch(...args),
    // SearchPage calls ``getIndexStatus`` on mount to render the index health
    // pill; the mock has to expose it or the page throws "is not a function".
    getIndexStatus:   () => mockGetIndexStatus(),
    // Project-scoped totals — drives the chip + Index Health rows when
    // no search query is active. Must exist on the mock or mount throws.
    getEntityCounts:  (...args: unknown[]) => mockGetEntityCounts(...args),
    reindex:          vi.fn().mockResolvedValue({ task_id: 't', status: 'queued' }),
    similar:          vi.fn().mockResolvedValue({ items: [], total: 0, query: '' }),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string }) => unknown) =>
    selector({ activeProjectId: 'proj-1' })),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

function makeIndexStatus(overrides: Partial<IndexStatus> = {}): IndexStatus {
  return {
    status: 'healthy',
    document_count: 0,
    last_indexed_at: null,
    ...overrides,
  }
}

function makeResult(overrides: Partial<GlobalSearchResult> = {}): GlobalSearchResult {
  return {
    entity_type: 'test_case',
    entity_id: '47981c63-b57d-4e96-9513-a2f903e39e03',
    title: 'dashboardLoads',
    subtitle: 'com.example.SmokeTests · PASSED',
    project_id: 'proj-1',
    project_name: 'GoogleSearch',
    navigation_url:
      '/runs/8c47be5a-82cb-4eb7-ba52-f9d0085be96a/tests/47981c63-b57d-4e96-9513-a2f903e39e03',
    relevance_score: 0.8,
    match_reasons: ['Matched test name'],
    metadata: { status: 'PASSED', suite_name: 'com.example.SmokeTests' },
    ...overrides,
  }
}

function makeResponse(items: GlobalSearchResult[]): GlobalSearchResponse {
  return {
    items,
    total: items.length,
    query: 'loads',
    search_type: 'hybrid',
    entity_counts: { test_case: items.length, test_run: 0, suite: 0, defect: 0, flaky_test: 0, release: 0 },
    page: 1,
    size: 25,
    pages: 1,
  }
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/search" element={<SearchPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SearchPage', () => {
  beforeEach(() => {
    mockGlobalSearch.mockReset()
    mockGetIndexStatus.mockReset()
    mockGetEntityCounts.mockReset()
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus())
    // Default: no project-scoped totals available — covers the existing
    // tests' chip-count expectations. Specific tests override per-case.
    mockGetEntityCounts.mockResolvedValue({
      test_case: 0, test_run: 0, suite: 0, defect: 0, flaky_test: 0, release: 0,
    })
    // Default: empty browse response. SearchPage now auto-runs on mount
    // for every scope (including ``all``) — tests that don't care about
    // the response shape still need a resolvable Promise so the page
    // doesn't crash on ``response.items.length``.
    mockGlobalSearch.mockResolvedValue({
      items: [], total: 0, query: '', search_type: 'hybrid',
      entity_counts: {}, page: 1, size: 25, pages: 0,
    } as unknown as GlobalSearchResponse)
  })

  /**
   * "Queries today" must not invent a number.
   *
   * The tile was fed ``recents.length * 24`` — the count of searches in *this
   * browser's* localStorage (``tl.search.recent``), multiplied by an arbitrary
   * 24 — and rendered through a compact number formatter as a platform metric.
   *
   * Measured on the live deployment with three seeded recent searches:
   *
   *     INDEX FRESHNESS    —static
   *     LATENCY P95        —ms
   *     QUERIES TODAY      72   no data      <-- 3 x 24
   *     ZERO-RESULT RATE   —target ≤ 5%
   *
   * The tile displayed a number while its own sub-label said "no data", and it
   * was the only one of the four that did not degrade honestly — the other
   * three already render an em dash when their metric is unavailable.
   *
   * There is no query-volume metric in the backend (the source called it "a P2
   * backend ask"), so the honest rendering is the em dash its neighbours use.
   * When that endpoint lands, this test should be updated to assert the real
   * value flows through — not deleted.
   */
  it('renders no query-volume number, because no query-volume metric exists', async () => {
    localStorage.setItem(
      'tl.search.recent',
      JSON.stringify(
        [1, 2, 3].map((i) => ({
          id: `r${i}`, query: `q${i}`, mode: 'hybrid', scope: 'all', resultCount: i, ts: Date.now(),
        })),
      ),
    )
    renderAt('/search')

    const label = await screen.findByText(/Queries today/i)
    const tile = label.closest('div')?.parentElement ?? label.parentElement
    const text = tile?.textContent ?? ''
    expect(text, 'the Queries today tile did not render at all').not.toBe('')
    expect(
      text,
      "the tile shows a fabricated count derived from this browser's recent-search list",
    ).not.toMatch(/\d/)
    expect(text).toContain('—')
  })

  it('renders the search workflow strip and search controls', async () => {
    renderAt('/search')

    // The workflow ribbon is identified by aria-label ("Search workflow"),
    // but its visible heading reads "Retrieval workflow" — match either.
    expect(
      (await screen.findAllByText(/(Search|Retrieval) workflow/i)).length,
    ).toBeGreaterThan(0)
    // Hero search input is the stable signal that controls rendered.
    expect(screen.getByPlaceholderText(/Search tests, runs, suites/i)).toBeInTheDocument()
  })

  it('renders result rows when the API returns items for a query', async () => {
    mockGlobalSearch.mockResolvedValue(
      makeResponse([
        makeResult({ title: 'dashboardLoads' }),
        makeResult({
          entity_id: '9e67126d-5859-4be7-97e6-c4ea815fe6e7',
          title: 'order_history_loads',
          subtitle: 'orders-ui · PASSED',
          navigation_url: '/runs/abc/tests/9e67126d',
          metadata: { status: 'PASSED', suite_name: 'orders-ui' },
        }),
      ]),
    )

    renderAt('/search?q=loads&mode=hybrid&scope=all')

    expect(await screen.findByText('dashboardLoads')).toBeInTheDocument()
    expect(await screen.findByText('order_history_loads')).toBeInTheDocument()
    expect(mockGlobalSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'loads', page: 1, size: 25 }),
    )
  })

  it('shows the Down badge in Index Health when /index-status reports unavailable', async () => {
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus({ status: 'unavailable' }))

    renderAt('/search')

    const badges = await screen.findAllByLabelText(/index: Down/i)
    expect(badges.length).toBeGreaterThan(0)
  })

  it('shows project-scoped entity totals in the Index Health rows before any query is run', async () => {
    // The bug this test pins: chips and Index Health rows previously
    // showed 0 unconditionally before the user typed anything, because
    // the page only read counts from the search response. The /search
    // page now backfills from /api/v1/search/entity-counts at mount.
    mockGetEntityCounts.mockResolvedValue({
      test_case: 47,
      test_run:  20,
      suite:     5,
      defect:    0,
      flaky_test: 1,
      release:   3,
    })

    renderAt('/search')

    // Wait for entity-counts to land. The numbers are rendered inside
    // the Index Health card next to each entity label. Use a regex on
    // the formatted number so locale-grouped values match too.
    expect(await screen.findByText(/^47 items/i)).toBeInTheDocument()
    expect(screen.getByText(/^20 items/i)).toBeInTheDocument()
    expect(screen.getByText(/^5 items/i)).toBeInTheDocument()
    expect(screen.getByText(/^3 items/i)).toBeInTheDocument()

    // The service is called with the active project from the store
    // (proj-1 in the mock above).
    expect(mockGetEntityCounts).toHaveBeenCalledWith('proj-1')
  })

  it('scope=all with empty query browses the API and renders results (regression: 2026-05-16)', async () => {
    // Pre-fix bug (commit 7be8193): ``runSearch`` short-circuited with
    // ``setResponse(null); return`` when the query was empty AND scope
    // was 'all', while every other scope hit the API in browse mode.
    // The result was an asymmetric landing experience — ``/search`` and
    // ``/search?scope=all`` showed an empty page on first load (with no
    // recents/saved/suggested to populate the fallback grid), while
    // ``/search?scope=tests`` and ``/search?scope=suites`` populated
    // immediately. The fix: empty queries browse for every scope so
    // ``All`` mirrors the other chips.
    mockGlobalSearch.mockResolvedValue(
      makeResponse([
        makeResult({ title: 'most_recent_login_test' }),
        makeResult({
          entity_id: 'b1eaae4a-2bd5-4d2a-8b4e-90f1b07d4ad5',
          entity_type: 'test_run',
          title: 'build-1042',
          subtitle: 'main · PASSED',
          navigation_url: '/runs/b1eaae4a-2bd5-4d2a-8b4e-90f1b07d4ad5',
          relevance_score: 0.7,
          metadata: { status: 'PASSED' },
        }),
      ]),
    )

    renderAt('/search?mode=hybrid&scope=all')

    // Both rows from the browse response render — proves the page hit
    // the API instead of short-circuiting to an empty grid.
    expect(await screen.findByText('most_recent_login_test')).toBeInTheDocument()
    expect(await screen.findByText('build-1042')).toBeInTheDocument()

    // Service was called with an empty query and no entity_types filter
    // (the hallmark of a scope=all browse).
    expect(mockGlobalSearch).toHaveBeenCalled()
    const callArgs = mockGlobalSearch.mock.calls[0][0] as {
      q: string
      entity_types?: string[]
    }
    expect(callArgs.q).toBe('')
    expect(callArgs.entity_types).toBeUndefined()
  })

  it('chip counts stay on project totals when scope is narrowed (no jumping)', async () => {
    // The bug this test pins (2026-05-15): clicking through the scope
    // chips made the Tests count jump 84 ↔ 50 because the chip used the
    // (capped) match count for the scoped type and the project total
    // for the rest. After the fix, chips always show project totals
    // unless scope='all' AND a query is active — keeping the meaning
    // stable: chip = "you have N items of this type in your project."
    mockGetEntityCounts.mockResolvedValue({
      test_case: 84, test_run: 39, suite: 5, defect: 0, flaky_test: 0, release: 28,
    })
    // The browse response (scope=suites) returns only 6 suites — but the
    // chip for Tests should still read 84, not 0 (it wasn't searched).
    mockGlobalSearch.mockResolvedValue({
      items: [], total: 6,
      query: '', search_type: 'hybrid',
      entity_counts: { suite: 6 } as Record<string, number>,
      page: 1, size: 25, pages: 1,
    } as unknown as GlobalSearchResponse)

    renderAt('/search?mode=hybrid&scope=suites')

    // Tests chip stays on the project total (84) instead of falling
    // through to 0 or to the capped match count for the previous scope.
    await screen.findByText(/^84$/, { exact: false })
    // Runs chip stays on 39 (the project total), not 0.
    expect(screen.getByText(/^39$/, { exact: false })).toBeInTheDocument()
  })
})
