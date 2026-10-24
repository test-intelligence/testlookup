/**
 * W12 SRCH-04 — the filter chips on /search (contract §2.3, test 7).
 *
 * After a search answers, the applied filters (`parsed.filters`) are chips in
 * the command bar, as React text; each × removes that token from the query and
 * re-runs it. Every `parsed.notes` line is shown verbatim. The Syntax help is a
 * closed Disclosure in the page that lists the eight keys. A plain query has no
 * chips row at all.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SearchPage from './SearchPage'
import type { GlobalSearchResponse, SearchParsed } from '@/types/search'

const mockGlobalSearch = vi.fn()

vi.mock('@/services/searchService', () => ({
  searchService: {
    search: vi.fn(),
    globalSearch: (...args: unknown[]) => mockGlobalSearch(...args),
    getIndexStatus: vi.fn().mockResolvedValue({ status: 'healthy', document_count: 10, last_indexed_at: null }),
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

const Q = 'status:failed suite:"API Regression" checkout since:7d foo:bar'

const PARSED: SearchParsed = {
  text: 'checkout foo:bar',
  filters: [
    { key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 },
    { key: 'suite', value: 'API Regression', raw: 'suite:"API Regression"', start: 14, end: 36 },
    { key: 'since', value: '7d', raw: 'since:7d', start: 46, end: 54 },
  ],
  ignored_tokens: [
    { token: 'foo:bar', key: 'foo', reason: 'unknown_key', message: 'unknown filter foo: searched as text', start: 55, end: 62 },
  ],
  unsupported_by: { defect: ['suite'] },
  searched_entity_types: ['test_case', 'test_run', 'suite', 'flaky_test', 'release'],
  release_unresolved: null,
  notes: [
    'unknown filter foo: searched as text',
    'suite: is not supported for Defects, so no defects were searched.',
  ],
}

function answer(parsed?: SearchParsed): GlobalSearchResponse {
  return {
    items: [{
      entity_type: 'test_case', entity_id: 'tc-1', title: 'checkout total', subtitle: 'API Regression · FAILED',
      project_id: 'proj-1', project_name: 'One', navigation_url: '/runs/r/tests/tc-1', relevance_score: 0.9,
      match_reasons: [], metadata: {},
    }],
    total: 1, query: 'q', search_type: 'keyword', entity_counts: { test_case: 1 }, page: 1, size: 25, pages: 1,
    ...(parsed ? { parsed } : {}),
  }
}

function renderAt(url: string) {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const chipsRow = () => document.querySelector('[data-search-chips]') as HTMLElement | null

beforeEach(() => {
  localStorage.clear()
  mockGlobalSearch.mockReset()
  mockGlobalSearch.mockResolvedValue(answer(PARSED))
})

describe('SearchPage — filter chips', () => {
  it('renders one chip per applied filter, as text, inside the command bar, and the notes verbatim', async () => {
    renderAt(`/search?q=${encodeURIComponent(Q)}&scope=all`)
    await screen.findByText('checkout total')

    const row = chipsRow() as HTMLElement
    expect(row).not.toBeNull()
    expect(screen.getByRole('region', { name: 'Search' }).contains(row)).toBe(true)
    expect(within(row).getByText('Filters')).toBeInTheDocument()
    const chips = Array.from(row.querySelectorAll('[data-search-chip]')).map((chip) => chip.textContent)
    expect(chips).toEqual(['status: failed', 'suite: API Regression', 'since: 7d'])
    expect(row.querySelector('[data-search-chip="suite"]')).toHaveAttribute('title', 'suite:"API Regression"')

    const notes = Array.from(row.querySelectorAll('[data-search-notes] li'))
    expect(notes.map((li) => li.textContent)).toEqual(PARSED.notes)
    // An ignored token reads muted; an unsupported filter reads in warning ink.
    expect(notes[0].className).toContain('--color-text-muted')
    expect(notes[1].className).toContain('--ui-warning-ink')
  })

  it('× removes that filter from the query and re-runs it', async () => {
    renderAt(`/search?q=${encodeURIComponent(Q)}&scope=all`)
    await screen.findByText('checkout total')
    expect(mockGlobalSearch).toHaveBeenLastCalledWith(expect.objectContaining({ q: Q, record: true }))

    fireEvent.click(screen.getByRole('button', { name: 'Remove since: 7d' }))
    const rewritten = 'status:failed suite:"API Regression" checkout foo:bar'
    expect(screen.getByRole('textbox', { name: 'Search across the workspace' })).toHaveValue(rewritten)
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenLastCalledWith(expect.objectContaining({ q: rewritten, record: true })))

    fireEvent.click(screen.getByRole('button', { name: 'Remove suite: API Regression' }))
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'status:failed checkout foo:bar' }),
    ))
  })

  it('the Syntax help is closed by default and lists the eight keys when opened', async () => {
    renderAt(`/search?q=${encodeURIComponent(Q)}&scope=all`)
    await screen.findByText('checkout total')

    const toggle = within(chipsRow() as HTMLElement).getByRole('button', { name: /Syntax/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(document.querySelector('[data-search-syntax]')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()

    fireEvent.click(toggle)
    const keys = Array.from(document.querySelectorAll('[data-syntax-key]')).map((row) => row.getAttribute('data-syntax-key'))
    expect(keys).toEqual(['status', 'suite', 'branch', 'env', 'type', 'since', 'release', 'owner'])
    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'Key' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Applies to' })).toBeInTheDocument()
  })

  it('a hostile filter value is text, never markup', async () => {
    mockGlobalSearch.mockResolvedValue(answer({
      ...PARSED,
      filters: [{ key: 'owner', value: '<img src=x onerror=alert(1)>', raw: 'owner:<img src=x onerror=alert(1)>', start: 0, end: 34 }],
      ignored_tokens: [],
      notes: ['No release named "<b>9.9</b>" in your projects.'],
    }))
    renderAt('/search?q=owner%3A%3Cimg&scope=all')
    await screen.findByText('checkout total')
    const row = chipsRow() as HTMLElement
    expect(row.querySelector('img')).toBeNull()
    expect(row.querySelector('b')).toBeNull()
    expect(within(row).getByText('owner: <img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(within(row).getByText('No release named "<b>9.9</b>" in your projects.')).toBeInTheDocument()
  })

  it('a plain query (no filter, no note) has no chips row', async () => {
    mockGlobalSearch.mockResolvedValue(answer({
      text: 'checkout', filters: [], ignored_tokens: [], unsupported_by: {}, searched_entity_types: ['test_case'], notes: [],
    }))
    renderAt('/search?q=checkout&scope=all')
    await screen.findByText('checkout total')
    expect(chipsRow()).toBeNull()
  })

  it('an answer without `parsed` (an older server) renders no chips', async () => {
    mockGlobalSearch.mockResolvedValue(answer())
    renderAt('/search?q=checkout&scope=all')
    await screen.findByText('checkout total')
    expect(chipsRow()).toBeNull()
  })
})
