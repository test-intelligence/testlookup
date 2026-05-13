import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'
import type { GlobalSearchResponse, GlobalSearchResult, IndexStatus } from '@/types/search'

// Per-test mock handles so we can swap return values without re-defining
// the whole searchService shape each time.
const mockGlobalSearch = vi.fn()
const mockGetIndexStatus = vi.fn()

vi.mock('@/services/searchService', () => ({
  searchService: {
    search:         vi.fn().mockResolvedValue({ items: [], total: 0 }),
    globalSearch:   (...args: unknown[]) => mockGlobalSearch(...args),
    // SearchPage calls ``getIndexStatus`` on mount to render the index health
    // pill; the mock has to expose it or the page throws "is not a function".
    getIndexStatus: () => mockGetIndexStatus(),
    reindex:        vi.fn().mockResolvedValue({ task_id: 't', status: 'queued' }),
    similar:        vi.fn().mockResolvedValue({ items: [], total: 0, query: '' }),
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
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus())
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
})
