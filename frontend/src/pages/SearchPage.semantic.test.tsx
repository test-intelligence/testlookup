/**
 * W12 SRCH-01 — Hybrid and Semantic on /search (contract §2.1, test 10).
 *
 * The modes cover test results only (GET /api/v1/search): they are offered on
 * Tests unless the index says `semantic_available: false` (then disabled, with
 * the server's reason as the title); choosing one on another scope moves the
 * scope to Tests, and another scope moves the mode back to Keyword, each with
 * one line. A submitted page-1 search is recorded, page 2 is not. A fallback
 * is said by the server's sentence, keyed off `fallback_reason`; the rows are
 * the keyword rows and link to the result.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'
import { MODE_SWITCH_LINE, SCOPE_SWITCH_LINE } from '@/components/search/searchModel'
import type { IndexStatus, SearchResponse, SearchResult } from '@/types/search'

const mockSearch = vi.fn()
const mockGlobalSearch = vi.fn()
const mockGetIndexStatus = vi.fn()

vi.mock('@/services/searchService', () => ({
  searchService: {
    search: (...args: unknown[]) => mockSearch(...args),
    globalSearch: (...args: unknown[]) => mockGlobalSearch(...args),
    getIndexStatus: (...args: unknown[]) => mockGetIndexStatus(...args),
    getEntityCounts: vi.fn().mockResolvedValue({ test_case: 3, test_run: 1, suite: 1, defect: 0, flaky_test: 0, release: 0 }),
    triggerReindex: vi.fn(),
    findSimilar: vi.fn(),
    recent: vi.fn().mockResolvedValue({ items: [], max: 20, retention_days: 30 }),
    clearRecent: vi.fn(),
    suggested: vi.fn().mockResolvedValue({ project_id: 'proj-1', window_days: 7, failing_tests: [], open_defects: [], active_release: null }),
    usage: vi.fn(),
  },
}))
vi.mock('@/services/savedViewsService', () => ({ listSavedViews: vi.fn().mockResolvedValue([]) }))
vi.mock('@/services/http', () => ({ getData: vi.fn(), postData: vi.fn(), putData: vi.fn(), patchData: vi.fn(), deleteData: vi.fn() }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: unknown) => unknown) => selector({
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'One' },
    projects: [{ id: 'proj-1', name: 'One' }],
    setActiveProject: vi.fn(),
  })),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

function indexStatus(overrides: Partial<IndexStatus> = {}): IndexStatus {
  return { status: 'healthy', document_count: 120, last_indexed_at: null, semantic_available: true, ...overrides }
}

function semanticRow(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    test_case_id: 't',
    test_run_id: 'r',
    test_name: 'payment times out at checkout',
    suite_name: 'Payments',
    status: 'FAILED',
    failure_count: 3,
    last_run_date: '2026-10-23T10:00:00Z',
    relevance_score: 0.82,
    ...overrides,
  }
}

function semanticAnswer(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    items: [semanticRow()],
    total: 1,
    page: 1,
    size: 25,
    pages: 1,
    query: 'payment timeout',
    search_type: 'semantic',
    requested_search_type: 'semantic',
    fallback_reason: null,
    fallback_message: null,
    result_status: 'partial',
    counts_are_exact: false,
    ...overrides,
  }
}

function Where() {
  const location = useLocation()
  return <output aria-label="Location">{`${location.pathname}${location.search}`}</output>
}

function renderAt(url: string) {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/search" element={<><SearchPage /><Where /></>} />
          <Route path="/runs/:runId/tests/:testId" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const location = () => screen.getByLabelText('Location').textContent ?? ''

beforeEach(() => {
  localStorage.clear()
  mockSearch.mockReset()
  mockGlobalSearch.mockReset()
  mockGetIndexStatus.mockReset()
  mockGetIndexStatus.mockResolvedValue(indexStatus())
  mockSearch.mockResolvedValue(semanticAnswer())
  mockGlobalSearch.mockResolvedValue({
    items: [], total: 0, query: '', search_type: 'keyword', entity_counts: {}, page: 1, size: 25, pages: 0,
  })
})

describe('SearchPage — the modes by scope', () => {
  it('offers Hybrid and Semantic on Tests while the index says semantic is available', async () => {
    renderAt('/search?scope=tests')
    await waitFor(() => expect(mockGetIndexStatus).toHaveBeenCalledWith('proj-1'))
    for (const name of ['Hybrid', 'Semantic']) {
      const chip = await screen.findByRole('radio', { name })
      expect(chip).toBeEnabled()
      expect(chip).not.toHaveAttribute('title')
    }
  })

  it('disables them, with the server\'s reason as the title, when semantic_available is false', async () => {
    const reason = 'Needs the semantic index: no ChromaDB server answers here.'
    mockGetIndexStatus.mockResolvedValue(indexStatus({
      status: 'not_configured', semantic_available: false, semantic_reason: 'not_configured', semantic_reason_text: reason,
    }))
    renderAt('/search?scope=tests')
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Semantic' })).toBeDisabled())
    expect(screen.getByRole('radio', { name: 'Semantic' })).toHaveAttribute('title', reason)
    expect(screen.getByRole('radio', { name: 'Hybrid' })).toHaveAttribute('title', reason)
    expect(screen.getByRole('radio', { name: 'Keyword' })).toBeEnabled()
    fireEvent.click(screen.getByRole('radio', { name: 'Semantic' }))
    expect(screen.getByRole('radio', { name: 'Keyword' })).toBeChecked()
    expect(mockSearch).not.toHaveBeenCalled()
  })

  it('Semantic chosen on All moves the scope to Tests and says so; another scope moves it back to Keyword', async () => {
    renderAt('/search?q=payment%20timeout&scope=all')
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('radio', { name: 'Semantic' }))
    expect(screen.getByRole('radio', { name: /^Tests/ })).toBeChecked()
    expect(screen.getByText(SCOPE_SWITCH_LINE)).toHaveAttribute('role', 'status')
    await waitFor(() => expect(mockSearch).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(location()).toContain('scope=tests'))

    fireEvent.click(screen.getByRole('radio', { name: /^Runs/ }))
    expect(screen.getByRole('radio', { name: 'Keyword' })).toBeChecked()
    expect(screen.queryByText(SCOPE_SWITCH_LINE)).toBeNull()
    expect(screen.getByText(MODE_SWITCH_LINE)).toBeInTheDocument()
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenLastCalledWith(expect.objectContaining({ entity_types: ['test_run'] })))

    // The line goes at the next mode / scope change.
    fireEvent.click(screen.getByRole('radio', { name: /^All/ }))
    expect(screen.queryByText(MODE_SWITCH_LINE)).toBeNull()
  })
})

describe('SearchPage — a semantic search', () => {
  it('calls GET /search with the mode and record on a page-1 search, without record on page 2', async () => {
    mockSearch.mockResolvedValue(semanticAnswer({ total: 30, pages: 2 }))
    renderAt('/search?scope=tests&mode=semantic')
    await screen.findByRole('radio', { name: 'Semantic' })

    const input = screen.getByRole('textbox', { name: 'Search across the workspace' })
    fireEvent.change(input, { target: { value: 'payment timeout' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith({
      q: 'payment timeout', project_id: 'proj-1', search_type: 'semantic', page: 1, size: 25, record: true,
    }))
    // The empty-box browse went to the keyword path, unrecorded.
    expect(mockGlobalSearch).toHaveBeenCalledWith(expect.objectContaining({ q: '', entity_types: ['test_case'] }))
    expect(mockGlobalSearch.mock.calls[0][0]).not.toHaveProperty('record')

    fireEvent.click(await screen.findByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(mockSearch).toHaveBeenLastCalledWith({
      q: 'payment timeout', project_id: 'proj-1', search_type: 'semantic', page: 2, size: 25,
    }))
  })

  it('rows are the result rows: name, suite · status, and a link to /runs/r/tests/t; the counts are lower bounds', async () => {
    renderAt('/search?q=payment%20timeout&scope=tests&mode=hybrid')
    const row = await screen.findByRole('button', { name: /payment times out at checkout/ })
    expect(row).toHaveTextContent('Payments · FAILED')
    expect(row).toHaveTextContent('rel 0.82')
    expect(screen.getByText('semantic', { exact: true }).tagName).toBe('CODE')
    expect(screen.getByText(/Search sampled the most relevant matches\. Counts shown are lower bounds\./)).toBeInTheDocument()
    expect(screen.getByText('1+')).toBeInTheDocument()
    expect(mockSearch).toHaveBeenCalledWith(expect.objectContaining({ search_type: 'hybrid' }))

    fireEvent.click(row)
    expect(location()).toBe('/runs/r/tests/t')
  })

  it.each([
    ['semantic_unavailable', 'Semantic index unavailable — showing keyword results.', 'ui-warning'],
    ['no_semantic_match', 'No semantic matches — showing keyword results.', 'ui-info'],
    ['no_text', 'Semantic search needs words to compare — showing keyword results for the filters.', 'ui-info'],
  ])('a %s fallback shows the server\'s sentence above the keyword rows', async (reason, message, ink) => {
    mockSearch.mockResolvedValue(semanticAnswer({
      search_type: 'keyword', fallback_reason: reason, fallback_message: message,
      result_status: 'complete', counts_are_exact: true,
      items: [semanticRow({ test_name: 'keyword row', relevance_score: 1 })],
    }))
    renderAt('/search?q=payment%20timeout&scope=tests&mode=semantic')

    const banner = await screen.findByText(message)
    expect(banner).toHaveAttribute('data-search-fallback', reason)
    expect(banner.className).toContain(`--${ink}-ink`)
    expect(screen.getByRole('button', { name: /keyword row/ })).toBeInTheDocument()
    // The header names what answered.
    expect(screen.getByText('keyword', { exact: true }).tagName).toBe('CODE')
  })

  it('no fallback_reason, no banner — whatever the count', async () => {
    mockSearch.mockResolvedValue(semanticAnswer({ items: [], total: 0, fallback_reason: null, fallback_message: 'stale text' }))
    renderAt('/search?q=nothing&scope=tests&mode=semantic')
    await screen.findByText('No results')
    expect(document.querySelector('[data-search-fallback]')).toBeNull()
    expect(screen.queryByText('stale text')).toBeNull()
  })

  it('a search that fails says so and shows no rows', async () => {
    mockSearch.mockRejectedValue(new Error('boom'))
    renderAt('/search?q=x&scope=tests&mode=semantic')
    await waitFor(() => expect(mockSearch).toHaveBeenCalled())
    const results = screen.queryByRole('heading', { name: /^Results/ })
    expect(results).toBeNull()
    expect(within(document.body).queryByText('boom')).toBeNull()
  })
})
