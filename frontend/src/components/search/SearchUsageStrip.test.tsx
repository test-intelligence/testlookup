/**
 * W12 SRCH-05 (contract §2.5, test 6): the usage strip's three tiles, the
 * "not measured" captions, and no strip and no request for a QA engineer —
 * the latter through the real /search page.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SearchUsageStrip from './SearchUsageStrip'
import SearchPage from '@/pages/SearchPage'
import { useAuthStore } from '@/store/authStore'
import type { SearchUsage } from '@/types/search'

const mockUsage = vi.fn()

vi.mock('@/services/searchService', () => ({
  searchService: {
    search: vi.fn(),
    globalSearch: vi.fn().mockResolvedValue({ items: [], total: 0, query: '', search_type: 'keyword', entity_counts: {}, page: 1, size: 25, pages: 0 }),
    getIndexStatus: vi.fn().mockResolvedValue({ status: 'healthy', document_count: 1, last_indexed_at: null }),
    getEntityCounts: vi.fn().mockResolvedValue({ test_case: 0, test_run: 0, suite: 0, defect: 0, flaky_test: 0, release: 0 }),
    triggerReindex: vi.fn(),
    findSimilar: vi.fn(),
    recent: vi.fn().mockResolvedValue({ items: [], max: 20, retention_days: 30 }),
    clearRecent: vi.fn(),
    suggested: vi.fn().mockResolvedValue({ project_id: 'p1', window_days: 7, failing_tests: [], open_defects: [], active_release: null }),
    usage: (...args: unknown[]) => mockUsage(...args),
  },
}))
vi.mock('@/services/savedViewsService', () => ({ listSavedViews: vi.fn().mockResolvedValue([]) }))
vi.mock('@/services/http', () => ({ getData: vi.fn(), postData: vi.fn(), putData: vi.fn(), patchData: vi.fn(), deleteData: vi.fn() }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: unknown) => unknown) => selector({
    activeProjectId: 'p1', activeProject: { id: 'p1', name: 'Payments' }, projects: [{ id: 'p1', name: 'Payments' }], setActiveProject: vi.fn(),
  })),
}))

const MEASURED: SearchUsage = {
  project_id: 'p1', days: 1, since: '2026-10-24T00:00:00Z', measured: true, searches: 3, zero_result_searches: 1,
  zero_result_rate: 1 / 3, p50_latency_ms: 40, p95_latency_ms: 120, fallback_searches: 0, retention_days: 30,
}

function signInAs(role: string) {
  useAuthStore.setState({ user: { id: 'u1', username: 'u', email: 'u@example.com', role } as never })
}

beforeEach(() => {
  mockUsage.mockReset()
  mockUsage.mockResolvedValue(MEASURED)
})

describe('SearchUsageStrip', () => {
  it('three small MetricCards: searches today, the zero-result rate, the p95', () => {
    render(<SearchUsageStrip usage={MEASURED} loading={false} failed={false} />)
    const strip = document.querySelector('[data-search-usage]') as HTMLElement
    const tiles = Array.from(strip.querySelectorAll('[data-metric-card="stat"]'))
    expect(tiles).toHaveLength(3)
    expect(within(strip.querySelector('[data-usage-tile="searches"]') as HTMLElement).getByText('3')).toBeInTheDocument()
    expect(strip).toHaveTextContent('UTC day · kept 30 days')
    expect(strip.querySelector('[data-usage-tile="zeroRate"]')).toHaveTextContent('33 %')
    expect(strip).toHaveTextContent('1 of 3 found nothing')
    expect(strip.querySelector('[data-usage-tile="p95"]')).toHaveTextContent('120 ms')
    expect(strip).toHaveTextContent('p50 40 ms')
  })

  it('no search today: "Not measured yet" under every tile, never 0 %', () => {
    render(<SearchUsageStrip usage={{ ...MEASURED, measured: false, searches: 0, zero_result_searches: 0, zero_result_rate: null, p50_latency_ms: null, p95_latency_ms: null }} loading={false} failed={false} />)
    expect(screen.getAllByText('Not measured yet: no search ran today.')).toHaveLength(3)
    expect(screen.queryByText(/0 %/)).toBeNull()
  })

  it('a QA lead sees the strip on /search (one usage read); a QA engineer sees none and sends nothing', async () => {
    signInAs('QA_LEAD')
    const lead = render(
      <SWRConfig value={{ provider: () => new Map() }}><MemoryRouter initialEntries={['/search']}><SearchPage /></MemoryRouter></SWRConfig>,
    )
    await waitFor(() => expect(document.querySelector('[data-search-usage]')).toHaveTextContent('33 %'))
    expect(mockUsage).toHaveBeenCalledTimes(1)
    expect(mockUsage).toHaveBeenCalledWith('p1', 1)
    lead.unmount()

    mockUsage.mockClear()
    signInAs('QA_ENGINEER')
    render(
      <SWRConfig value={{ provider: () => new Map() }}><MemoryRouter initialEntries={['/search']}><SearchPage /></MemoryRouter></SWRConfig>,
    )
    await screen.findByTestId('searchable-data-summary')
    expect(document.querySelector('[data-search-usage]')).toBeNull()
    expect(mockUsage).not.toHaveBeenCalled()
    useAuthStore.setState({ user: null })
  })
})
