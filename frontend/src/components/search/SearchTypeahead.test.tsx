/**
 * W12 SRCH-03 (contract §2.2, test 10): the top-bar typeahead over the REAL
 * read (`useSearchTypeahead`) and a mocked service, with fake timers — no
 * request before 250 ms or under 2 characters, one after; a newer query
 * supersedes an older answer; a click selects the match's project and opens
 * it; the last row runs the full search; a navigation closes it; closed, it
 * renders nothing and asks nothing. (Escape, Enter and the blur live in the
 * top bar: `TopBar.test.tsx`.)
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SearchTypeahead, { TYPEAHEAD_EMPTY, TYPEAHEAD_ERROR, TYPEAHEAD_SEARCHING } from './SearchTypeahead'
import { fullSearchHref } from './searchModel'
import type { GlobalSearchResponse, GlobalSearchResult } from '@/types/search'

const mockGlobalSearch = vi.fn()
const mockSetActiveProject = vi.fn()
const projectState = vi.hoisted(() => ({
  activeProjectId: 'p1' as string | null,
  projects: [{ id: 'p1', name: 'Payments' }, { id: 'p2', name: 'Checkout' }],
}))

vi.mock('@/services/searchService', () => ({
  searchService: { globalSearch: (...args: unknown[]) => mockGlobalSearch(...args) },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: unknown) => unknown) => selector({ ...projectState, setActiveProject: mockSetActiveProject })),
}))

function row(n: number, overrides: Partial<GlobalSearchResult> = {}): GlobalSearchResult {
  return {
    entity_type: 'test_case', entity_id: `tc-${n}`, title: `checkout test ${n}`, subtitle: `Checkout · FAILED`,
    project_id: 'p2', project_name: 'Checkout', navigation_url: `/runs/r${n}/tests/tc-${n}`, relevance_score: 0.9,
    match_reasons: [], metadata: {}, ...overrides,
  }
}

function answer(items: GlobalSearchResult[]): GlobalSearchResponse {
  return { items, total: items.length, query: 'q', search_type: 'keyword', entity_counts: {}, page: 1, size: 5, pages: 1 }
}

/** The top bar's wiring, reduced: typing opens the list; `onDone` clears and closes. */
function Harness() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  return (
    <div className="relative">
      <input aria-label="Top search" value={q} onChange={(e) => { setQ(e.target.value); setOpen(true) }} />
      <button type="button" onClick={() => navigate('/elsewhere')}>Go elsewhere</button>
      <output aria-label="Location">{`${location.pathname}${location.search}`}</output>
      <output aria-label="Open">{String(open)}</output>
      <SearchTypeahead query={q} open={open} onClose={() => setOpen(false)} onDone={() => { setQ(''); setOpen(false) }} />
    </div>
  )
}

function renderHarness() {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="*" element={<Harness />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const input = () => screen.getByLabelText('Top search')
const type = (value: string) => fireEvent.change(input(), { target: { value } })
/** Move the fake clock, then let the read's promise and React's render settle. */
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  mockGlobalSearch.mockReset()
  mockSetActiveProject.mockReset()
  projectState.activeProjectId = 'p1'
  mockGlobalSearch.mockResolvedValue(answer([row(1), row(2)]))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SearchTypeahead — the read', () => {
  it('under 2 characters: no list and no request', async () => {
    renderHarness()
    type('c')
    await advance(1_000)
    expect(mockGlobalSearch).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('no request before 250 ms after the last keystroke; ONE after, size 15 (five distinct rows), never recorded', async () => {
    renderHarness()
    type('ch')
    await advance(100)
    type('che')
    await advance(200)
    expect(mockGlobalSearch).not.toHaveBeenCalled()
    expect(screen.getByRole('listbox', { name: 'Matches' })).toBeInTheDocument()
    expect(screen.getByText(TYPEAHEAD_SEARCHING)).toBeInTheDocument()
    await advance(60)
    expect(mockGlobalSearch).toHaveBeenCalledTimes(1)
    expect(mockGlobalSearch).toHaveBeenCalledWith({ q: 'che', project_id: 'p1', size: 15 }, { suppressToast: true })
    expect(screen.getByText('checkout test 1')).toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(3)
  })

  it('shows DISTINCT entities: five runs of one test + two others are three rows, the first of each kept', async () => {
    const SAME = 'test_checkout_completes'
    mockGlobalSearch.mockResolvedValue(answer([
      row(1, { title: SAME, subtitle: 'E2E Suite · FAILED', navigation_url: '/runs/r1/tests/tc-1' }),
      row(2, { title: SAME, subtitle: 'E2E Suite · PASSED' }),
      row(3, { entity_type: 'test_run', title: 'build-1042', subtitle: 'main · FAILED', navigation_url: '/runs/r3' }),
      row(4, { title: SAME, subtitle: 'E2E Suite · PASSED' }),
      row(5, { title: SAME, subtitle: 'E2E Suite · FAILED' }),
      row(6, { title: SAME, subtitle: 'E2E Suite · PASSED' }),
      // The same title as another KIND is another entity.
      row(7, { entity_type: 'suite', title: SAME, subtitle: '3 tests', navigation_url: '/suites/x' }),
    ]))
    renderHarness()
    type('check')
    await advance(260)
    const rows = Array.from(document.querySelectorAll('[data-typeahead-row]')) as HTMLElement[]
    expect(rows.map((r) => [r.getAttribute('data-typeahead-row'), r.querySelector('[title]')?.textContent])).toEqual([
      ['test_case', SAME], ['test_run', 'build-1042'], ['suite', SAME],
    ])
    // The first row of the test, in the server's order, with the count of runs it stands for.
    expect(rows[0]).toHaveTextContent('E2E Suite · FAILED · 5 runs')
    expect(screen.getByText('E2E Suite · FAILED', { exact: true })).toBeInTheDocument()
    expect(rows[1].querySelector('[data-typeahead-count]')).toBeNull()
    expect(screen.getAllByRole('option')).toHaveLength(4)
    fireEvent.click(rows[0])
    expect(screen.getByLabelText('Location')).toHaveTextContent('/runs/r1/tests/tc-1')
  })

  it('keeps five distinct rows out of more candidates, and marks a capped count with +', async () => {
    mockGlobalSearch.mockResolvedValue({
      ...answer([1, 1, 2, 3, 4, 5, 6, 7].map((n, i) => row(n, { entity_id: `e-${i}` }))),
      total: 40,
    })
    renderHarness()
    type('check')
    await advance(260)
    const rows = Array.from(document.querySelectorAll('[data-typeahead-row]')) as HTMLElement[]
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('· 2+ runs')
  })

  it('in All Projects the read carries no project', async () => {
    projectState.activeProjectId = '__ALL__'
    renderHarness()
    type('check')
    await advance(260)
    expect(mockGlobalSearch).toHaveBeenCalledWith({ q: 'check', size: 15 }, { suppressToast: true })
  })

  it('an answer for an older query is never shown', async () => {
    let resolveOld!: (value: GlobalSearchResponse) => void
    mockGlobalSearch
      .mockImplementationOnce(() => new Promise<GlobalSearchResponse>((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(answer([row(9, { title: 'checkout NEW' })]))
    renderHarness()
    type('check')
    await advance(260)
    type('checkout')
    await advance(260)
    expect(screen.getByText('checkout NEW')).toBeInTheDocument()
    await act(async () => { resolveOld(answer([row(1, { title: 'checkout OLD' })])) })
    await advance(10)
    expect(screen.queryByText('checkout OLD')).toBeNull()
    expect(screen.getByText('checkout NEW')).toBeInTheDocument()
  })

  it('while the text is still changing, the previous answer is not shown', async () => {
    renderHarness()
    type('check')
    await advance(260)
    expect(screen.getByText('checkout test 1')).toBeInTheDocument()
    type('checkout')
    await advance(100)
    expect(screen.queryByText('checkout test 1')).toBeNull()
    expect(screen.getByText(TYPEAHEAD_SEARCHING)).toBeInTheDocument()
  })

  it('states: no matches, could not load', async () => {
    mockGlobalSearch.mockResolvedValueOnce(answer([])).mockRejectedValueOnce(new Error('500'))
    renderHarness()
    type('zz')
    await advance(260)
    expect(screen.getByText(TYPEAHEAD_EMPTY)).toBeInTheDocument()
    type('zzz')
    await advance(260)
    expect(screen.getByText(TYPEAHEAD_ERROR)).toBeInTheDocument()
  })
})

describe('SearchTypeahead — choosing', () => {
  it('a match selects its project and opens it; the input is cleared', async () => {
    renderHarness()
    type('checkout')
    await advance(260)
    fireEvent.click(screen.getByRole('option', { name: /checkout test 2/ }))
    expect(mockSetActiveProject).toHaveBeenCalledWith({ id: 'p2', name: 'Checkout' })
    expect(screen.getByLabelText('Location')).toHaveTextContent('/runs/r2/tests/tc-2')
    expect(input()).toHaveValue('')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('the last row runs the full search, exactly as Enter does', async () => {
    renderHarness()
    type('  check out ')
    await advance(260)
    fireEvent.click(screen.getByRole('option', { name: /Search everything for “check out”/ }))
    expect(screen.getByLabelText('Location')).toHaveTextContent('/search?q=check%20out')
    expect(fullSearchHref('  check out ')).toBe('/search?q=check%20out')
  })

  it('a navigation closes it (and drops the read)', async () => {
    renderHarness()
    type('checkout')
    await advance(260)
    screen.getByText('checkout test 1')
    fireEvent.click(screen.getByRole('button', { name: 'Go elsewhere' }))
    expect(screen.getByLabelText('Open')).toHaveTextContent('false')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('titles and subtitles are text, never markup', async () => {
    mockGlobalSearch.mockResolvedValue(answer([row(1, { title: '<img src=x onerror=alert(1)>', subtitle: '<b>x</b>' })]))
    renderHarness()
    type('img')
    await advance(260)
    const option = screen.getByRole('option', { name: /<img src=x onerror=alert\(1\)>/ })
    expect(option.querySelector('img')).toBeNull()
    expect(option.querySelector('b')).toBeNull()
  })
})

describe('SearchTypeahead — closed', () => {
  it('renders nothing and asks nothing', async () => {
    const { container } = render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <MemoryRouter><SearchTypeahead query="checkout" open={false} onClose={vi.fn()} onDone={vi.fn()} /></MemoryRouter>
      </SWRConfig>,
    )
    await advance(1_000)
    expect(container).toBeEmptyDOMElement()
    expect(mockGlobalSearch).not.toHaveBeenCalled()
  })
})
