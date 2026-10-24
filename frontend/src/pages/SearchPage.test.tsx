import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import {
  MemoryRouter, Route, Routes, useLocation, useNavigate,
} from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import toast from 'react-hot-toast'

import SearchPage from './SearchPage'
import { SCOPE_SWITCH_LINE } from '@/components/search/searchModel'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
import type { GlobalSearchResponse, GlobalSearchResult, IndexStatus } from '@/types/search'
import type { SavedView } from '@/services/savedViewsService'
import { useReleaseStore } from '@/store/releaseStore'

// Per-test mock handles so we can swap return values without re-defining
// the whole searchService shape each time.
const mockGlobalSearch = vi.fn()
const mockGetIndexStatus = vi.fn()
const mockGetEntityCounts = vi.fn()
// W12: the semantic path, the server-side recents, suggestions and usage, the
// saved searches (saved_views rows) and their writes.
const mockSearch = vi.fn()
const mockRecent = vi.fn()
const mockClearRecent = vi.fn()
const mockSuggested = vi.fn()
const mockUsage = vi.fn()
const mockListSavedViews = vi.fn()
const mockPostData = vi.fn()
const { mockProjectState, mockSetActiveProject } = vi.hoisted(() => ({
  mockSetActiveProject: vi.fn(),
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'One' },
    projects: [{ id: 'proj-1', name: 'One' }, { id: 'proj-2', name: 'Two' }],
  },
}))

vi.mock('@/services/searchService', () => ({
  searchService: {
    search:           (...args: unknown[]) => mockSearch(...args),
    globalSearch:     (...args: unknown[]) => mockGlobalSearch(...args),
    // SearchPage calls ``getIndexStatus`` on mount to render the index health
    // pill; the mock has to expose it or the page throws "is not a function".
    getIndexStatus:   (...args: unknown[]) => mockGetIndexStatus(...args),
    // Project-scoped totals — drives the chip + Index Health rows when
    // no search query is active. Must exist on the mock or mount throws.
    getEntityCounts:  (...args: unknown[]) => mockGetEntityCounts(...args),
    reindex:          vi.fn().mockResolvedValue({ task_id: 't', status: 'queued' }),
    similar:          vi.fn().mockResolvedValue({ items: [], total: 0, query: '' }),
    triggerReindex:   vi.fn().mockResolvedValue({ task_id: 't', status: 'queued' }),
    findSimilar:      vi.fn().mockResolvedValue({ items: [], total: 0, query: '', semantic_status: 'ok' }),
    recent:           (...args: unknown[]) => mockRecent(...args),
    clearRecent:      (...args: unknown[]) => mockClearRecent(...args),
    suggested:        (...args: unknown[]) => mockSuggested(...args),
    usage:            (...args: unknown[]) => mockUsage(...args),
  },
}))

// Saved searches are saved_views rows (page 'search'); the writes go through
// the shared http helpers with suppressToast.
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: (...args: unknown[]) => mockListSavedViews(...args),
}))
vi.mock('@/services/http', () => ({
  getData: vi.fn(),
  postData: (...args: unknown[]) => mockPostData(...args),
  putData: vi.fn(),
  patchData: vi.fn(),
  deleteData: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState & { setActiveProject: typeof mockSetActiveProject }) => unknown) =>
    selector({ ...mockProjectState, setActiveProject: mockSetActiveProject })),
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
    search_type: 'keyword',
    entity_counts: { test_case: items.length, test_run: 0, suite: 0, defect: 0, flaky_test: 0, release: 0 },
    page: 1,
    size: 25,
    pages: 1,
  }
}

function makeSavedView(overrides: Partial<SavedView> = {}): SavedView {
  return {
    id: 'sv-1',
    user_id: 'someone-else',
    project_id: 'proj-1',
    name: 'Saved login',
    description: null,
    page: 'search',
    filters: { kind: 'search', q: 'login', scope: 'tests', mode: 'keyword' },
    is_shared: false,
    is_default: false,
    created_at: '2026-10-24T10:00:00Z',
    updated_at: null,
    ...overrides,
  }
}

function SearchNavigationHarness() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      {/* W12: was `mode=hybrid&scope=runs` (read as keyword before Hybrid
          existed); a hybrid link now moves the scope to Tests. */}
      <button type="button" onClick={() => navigate('/search?q=second&mode=keyword&scope=runs')}>
        Navigate to legacy search
      </button>
      <button type="button" onClick={() => navigate(-1)}>Go back</button>
      <output aria-label="Current search params">{location.search}</output>
      <SearchPage />
    </>
  )
}

function searchTree(url: string, withNavigationHarness = false) {
  // A fresh SWR cache per render: the page's reads (index status, recents,
  // saved searches, suggestions) never carry over from another test.
  return (
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/search"
            element={withNavigationHarness ? <SearchNavigationHarness /> : <SearchPage />}
          />
        </Routes>
      </MemoryRouter>
    </SWRConfig>
  )
}

function renderAt(url: string, withNavigationHarness = false) {
  return render(searchTree(url, withNavigationHarness))
}

describe('SearchPage', () => {
  beforeEach(() => {
    localStorage.clear()
    mockProjectState.activeProjectId = 'proj-1'
    mockGlobalSearch.mockReset()
    mockGetIndexStatus.mockReset()
    mockGetEntityCounts.mockReset()
    mockSetActiveProject.mockReset()
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
      items: [], total: 0, query: '', search_type: 'keyword',
      entity_counts: {}, page: 1, size: 25, pages: 0,
    } as unknown as GlobalSearchResponse)
    // W12 defaults: nothing recent, nothing saved, nothing suggested, no usage.
    mockSearch.mockReset()
    mockRecent.mockReset()
    mockClearRecent.mockReset()
    mockSuggested.mockReset()
    mockUsage.mockReset()
    mockListSavedViews.mockReset()
    mockPostData.mockReset()
    mockSearch.mockResolvedValue({
      items: [], total: 0, page: 1, size: 25, pages: 0, query: '', search_type: 'semantic',
    })
    mockRecent.mockResolvedValue({ items: [], max: 20, retention_days: 30 })
    mockClearRecent.mockResolvedValue(undefined)
    mockSuggested.mockResolvedValue({
      project_id: 'proj-1', window_days: 7, failing_tests: [], open_defects: [], active_release: null,
    })
    mockUsage.mockResolvedValue({
      project_id: 'proj-1', days: 1, since: '2026-10-24T00:00:00Z', measured: false,
      searches: 0, zero_result_searches: 0, retention_days: 30,
    })
    mockListSavedViews.mockResolvedValue([])
    mockPostData.mockImplementation(async (_url: string, body: Record<string, unknown>) => makeSavedView({ ...body, id: 'sv-new' } as Partial<SavedView>))
  })

  /**
   * The index verdict keeps only tiles with a real metric behind them.
   *
   * "Queries today" was once fed ``recents.length * 24`` (this browser's
   * recent-search count times 24); after that was fixed it, "Latency p95" and
   * "Zero-result rate" all rendered a permanent "—" because the backend has no
   * query-volume, latency or zero-result metric. UX P2 deletes the three null
   * tiles; the items-indexed headline and index freshness are real and stay.
   * When such a metric lands, add its tile back with the real value.
   */
  it('renders the real index verdict (items indexed, freshness) and none of the null tiles', async () => {
    localStorage.setItem(
      'tl.search.recent',
      JSON.stringify(
        [1, 2, 3].map((i) => ({
          id: `r${i}`, query: `q${i}`, mode: 'hybrid', scope: 'all', resultCount: i, ts: Date.now(),
        })),
      ),
    )
    mockGetEntityCounts.mockResolvedValue({
      test_case: 47, test_run: 20, suite: 5, defect: 0, flaky_test: 1, release: 3,
    })
    renderAt('/search')

    const verdict = await screen.findByRole('region', { name: 'Index verdict' })
    await waitFor(() => {
      expect(verdict).toHaveTextContent('76 items indexed across 6 entity types')
    })
    expect(verdict).toHaveTextContent(/Index freshness/i)
    for (const gone of [/Queries today/i, /Zero-result rate/i, /Latency p95/i, /target ≤ 5%/, /no data/i]) {
      expect(screen.queryByText(gone)).toBeNull()
    }
  })

  // UX redesign P6: the page template's header, not a page-made heading.
  it('renders the template header: one compact h1, the search help topic, the old crumb as its subtitle', async () => {
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus({
      document_count: 76,
      last_indexed_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    }))
    renderAt('/search')

    const header = await waitFor(() => {
      const el = document.querySelector('[data-page-header]') as HTMLElement
      expect(el).toHaveTextContent('76 items indexed')
      return el
    })
    expectTemplateHeader('Search', '/search')
    expect(header).toHaveTextContent('Find tests, runs, suites, defects, releases across One · 76 items indexed · fresh 5m ago')
    // The search box is the hero below the header, not a header action.
    expect(header.querySelector('input')).toBeNull()
  })

  // UX P2: the "Retrieval workflow" ribbon showed four invented stages
  // ("Ranking: recency, ownership, severity weighting" — there is no ranker).
  it('renders the search controls and no decorative workflow ribbon', async () => {
    renderAt('/search')

    expect(await screen.findByRole('textbox', { name: 'Search across the workspace' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Search' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Search workflow' })).toBeNull()
    expect(screen.queryByText(/(Search|Retrieval) workflow/i)).toBeNull()
    expect(screen.queryByText(/Drill into result/i)).toBeNull()
  })

  // UX P2: every one of these was a toast-only stub or a claim the backend
  // did not back. W12 (deliberate rewrite, contract §6.5): the saved search
  // comes from the server now, and the filter syntax is real (SRCH-04) — so
  // `status:failed` is absent only while no search with it ran.
  it('renders no stub control, no provenance footer and no invented query syntax', async () => {
    mockListSavedViews.mockResolvedValue([makeSavedView()])
    renderAt('/search')

    await screen.findByText('Saved login')
    for (const name of [/^Saved searches$/i, /Index settings/i, /Manage/i, /Ranking trail/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    expect(screen.queryByText(/Ranking trail/i)).toBeNull()
    expect(screen.queryByText('Query syntax')).toBeNull()
    expect(screen.queryByText(/Suggested for you/i)).toBeNull()
    expect(screen.queryByText(/candidates/i)).toBeNull()
    expect(screen.queryByText(/status:failed/)).toBeNull()
    expect(screen.queryByText(/page size 25/)).toBeNull()
  })

  it('says exactly which fields global search matches, in the lede and the placeholder', async () => {
    renderAt('/search')

    const input = await screen.findByRole('textbox', { name: 'Search across the workspace' })
    const placeholder = input.getAttribute('placeholder') ?? ''
    // The old placeholder advertised a field:value syntax the backend never parsed.
    expect(placeholder).not.toMatch(/status:|owner:|last 7d|commit|PR/)
    expect(placeholder).toMatch(/test names/)
    expect(placeholder).toMatch(/build numbers, branches, jobs/)
    expect(screen.getByTestId('search-matched-fields')).toHaveTextContent(
      'Case-insensitive substring match on test names, suite names, error messages and tags; '
      + 'run build numbers, branches, jobs and tags; defect Jira IDs; flaky test names; and '
      + 'release names and versions.',
    )
    expect(screen.queryByText(/field:value/)).toBeNull()
  })

  // W12 (deliberate rewrite, §6.5): the card renders from the server's list
  // (listSavedViews, page 'search'), not from this browser's storage.
  it('shows the Saved searches card only when the server holds saved searches', async () => {
    renderAt('/search')
    await screen.findByText(/Your queries will appear here/i)
    await waitFor(() => expect(mockListSavedViews).toHaveBeenCalledWith('proj-1', 'search'))
    // No card, and no empty state pointing at a bookmark button that does not exist.
    expect(screen.queryByText('Saved searches')).toBeNull()
    expect(screen.queryByText(/Save a query by clicking/i)).toBeNull()
  })

  it('renders the Saved searches card from listSavedViews, ⌘1 on the first row', async () => {
    mockListSavedViews.mockResolvedValue([
      makeSavedView({ id: 'a', name: 'Checkout failures', filters: { kind: 'search', q: 'checkout status:failed', scope: 'tests', mode: 'keyword' } }),
      makeSavedView({ id: 'b', name: 'Not a search', filters: { kind: 'report_view', page: 'trends' } }),
    ])
    renderAt('/search')

    const card = await screen.findByTestId('saved-searches')
    expect(within(card).getByRole('heading', { name: 'Saved searches' })).toBeInTheDocument()
    expect(within(card).getByText('Checkout failures')).toBeInTheDocument()
    expect(within(card).getByText('⌘1')).toBeInTheDocument()
    expect(within(card).queryByText('Not a search')).toBeNull()
  })

  // W12 (deliberate rewrite, §6.5): Hybrid and Semantic exist on the Tests
  // scope, so a deep link that asks for one on All loads Tests, says so in one
  // line, and the URL is rewritten.
  it('a semantic mode deep-linked with another scope loads on Tests, with the line that says so', async () => {
    renderAt('/search?mode=hybrid&scope=all', true)

    expect(await screen.findByRole('radio', { name: 'Hybrid' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Keyword' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: /^Tests/ })).toBeChecked()
    expect(screen.getByText(SCOPE_SWITCH_LINE)).toHaveAttribute('role', 'status')
    await waitFor(() => {
      expect(screen.getByLabelText('Current search params')).toHaveTextContent('mode=hybrid')
      expect(screen.getByLabelText('Current search params')).toHaveTextContent('scope=tests')
    })
  })

  // W12 (deliberate rewrite, §6.5): a page-1 search with words is recorded
  // (`record: true`: a recent entry and a usage row, written by the server).
  it('synchronizes same-route navigation and browser history with the latest search', async () => {
    renderAt('/search?q=first&mode=keyword&scope=tests', true)

    await waitFor(() => {
      expect(mockGlobalSearch).toHaveBeenLastCalledWith({
        q: 'first', project_id: 'proj-1', entity_types: ['test_case'], page: 1, size: 25, record: true,
      })
    })

    fireEvent.click(screen.getByRole('button', { name: 'Navigate to legacy search' }))

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Search across the workspace' }))
        .toHaveValue('second')
      expect(screen.getByRole('radio', { name: /^Runs/ })).toBeChecked()
      expect(screen.getByRole('radio', { name: 'Keyword' })).toBeChecked()
      expect(screen.getByLabelText('Current search params'))
        .toHaveTextContent('mode=keyword')
      expect(mockGlobalSearch).toHaveBeenLastCalledWith({
        q: 'second', project_id: 'proj-1', entity_types: ['test_run'], page: 1, size: 25, record: true,
      })
    })

    fireEvent.click(screen.getByRole('button', { name: 'Go back' }))

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Search across the workspace' }))
        .toHaveValue('first')
      expect(screen.getByRole('radio', { name: /^Tests/ })).toBeChecked()
      expect(mockGlobalSearch).toHaveBeenLastCalledWith({
        q: 'first', project_id: 'proj-1', entity_types: ['test_case'], page: 1, size: 25, record: true,
      })
    })
  })

  // W12 (deliberate rewrite, §6.5): the one-time move of this browser's saved
  // searches to the server — one POST per entry the server does not hold, each
  // removed from the key as it lands, the key deleted when empty — and the
  // local recents dropped unread. A recent from the server replays as it was.
  it('moves the browser\'s saved searches to the server once and drops the local recents', async () => {
    localStorage.setItem('tl.search.recent', JSON.stringify([{
      id: 'legacy-recent', query: 'legacy checkout', mode: 'hybrid', scope: 'all',
      resultCount: 7, ts: Date.now(),
    }]))
    localStorage.setItem('tl.search.saved', JSON.stringify([
      {
        id: 'legacy-saved', label: 'Legacy saved', query: 'saved checkout', mode: 'semantic',
        scope: 'tests', resultCount: 3, slot: 1,
      },
      { id: 'on-server', label: 'Login', query: 'Login', mode: 'keyword', scope: 'tests', slot: 2 },
    ]))
    mockListSavedViews.mockResolvedValue([makeSavedView()])
    mockRecent.mockResolvedValue({
      items: [{ q: 'server checkout', scope: 'all', mode: 'keyword', project_id: 'proj-1', result_count: 7, counts_exact: true, ts: new Date().toISOString() }],
      max: 20,
      retention_days: 30,
    })

    renderAt('/search', true)

    await waitFor(() => expect(mockPostData).toHaveBeenCalledTimes(1))
    expect(mockPostData).toHaveBeenCalledWith('/api/v1/saved-views', {
      project_id: 'proj-1',
      name: 'Legacy saved',
      page: 'search',
      filters: { kind: 'search', q: 'saved checkout', scope: 'tests', mode: 'semantic' },
      is_shared: false,
    }, { suppressToast: true })
    await waitFor(() => expect(localStorage.getItem('tl.search.saved')).toBeNull())
    expect(localStorage.getItem('tl.search.recent')).toBeNull()
    // The list is read again once the move posted something.
    await waitFor(() => expect(mockListSavedViews).toHaveBeenCalledTimes(2))

    const recentButton = await screen.findByRole('button', { name: /server checkout/i })
    fireEvent.click(recentButton)

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Search across the workspace' }))
        .toHaveValue('server checkout')
      expect(screen.getByLabelText('Current search params'))
        .toHaveTextContent('mode=keyword')
      expect(mockGlobalSearch).toHaveBeenLastCalledWith({
        q: 'server checkout', project_id: 'proj-1', entity_types: undefined, page: 1, size: 25, record: true,
      })
    })
    // Once per load: nothing more is posted.
    expect(mockPostData).toHaveBeenCalledTimes(1)
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

    // W12: was `mode=hybrid`, which read as keyword before Hybrid existed; a
    // hybrid link now runs on Tests through GET /search (SearchPage.semantic.test).
    renderAt('/search?q=loads&mode=keyword&scope=all')

    expect(await screen.findByText('dashboardLoads')).toBeInTheDocument()
    expect(await screen.findByText('order_history_loads')).toBeInTheDocument()
    // Assert after result rows settle so this cannot pass against the
    // pre-response fallback and then regress when `search_type` is applied.
    // The results header names the retrieval the API reports it ran.
    expect(screen.getByText('keyword', { exact: true }).tagName).toBe('CODE')
    expect(mockGlobalSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'loads', page: 1, size: 25 }),
    )
  })

  it('labels capped or failed search results as lower bounds', async () => {
    mockGlobalSearch.mockResolvedValue({
      ...makeResponse([makeResult()]),
      total: 50,
      counts_are_exact: false,
      result_status: 'partial',
      failed_entity_types: ['defect'],
    })

    renderAt('/search?q=loads&scope=all')

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Some search sources were unavailable: defect. Counts shown are lower bounds.',
    )
    expect(screen.getByText('50+')).toBeInTheDocument()
    expect(screen.getByText(/of at least 50/)).toBeInTheDocument()
  })

  it('selects a result project before opening its project-scoped link', async () => {
    mockGlobalSearch.mockResolvedValue(
      makeResponse([makeResult({ project_id: 'proj-2', title: 'cross-project suite' })]),
    )

    renderAt('/search?q=suite&scope=all')
    fireEvent.click(await screen.findByRole('button', { name: /cross-project suite/i }))

    expect(mockSetActiveProject).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'proj-2' }),
    )
  })

  // HON-08: the rail ("Index health") copied ONE vector-index status onto all
  // six entity rows, so a ChromaDB outage -- or no ChromaDB at all, the
  // `make dev` default -- marked Tests, Runs, Suites, Defects, Flaky and
  // Releases "Down" although global search is a SQL keyword match over each.
  const ENTITY_LABELS = ['Tests', 'Runs', 'Suites', 'Defects', 'Flaky', 'Releases']

  async function railCard() {
    const summary = await screen.findByTestId('searchable-data-summary')
    return { summary, semantic: screen.getByTestId('semantic-index-line') }
  }

  it.each([
    ['unavailable', 'Unavailable', /ChromaDB answered with an error/],
    ['not_configured', 'Not configured', /No ChromaDB server answers here/],
  ] as const)('with the semantic index %s, the six entities stay keyword-searchable and nothing reads "Down"', async (status, label, detail) => {
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus({ status: status as IndexStatus['status'] }))

    renderAt('/search')

    const { summary, semantic } = await railCard()
    await waitFor(() => expect(summary).toHaveTextContent(`6 entity types · semantic: ${label.toLowerCase()}`))
    for (const entity of ENTITY_LABELS) {
      expect(screen.getByLabelText(`${entity}: keyword search`)).toHaveTextContent('Keyword')
    }
    expect(within(semantic).getByLabelText(`Semantic index: ${label}`)).toBeInTheDocument()
    expect(semantic).toHaveTextContent(detail)
    expect(screen.queryByText('Down')).toBeNull()
    expect(screen.queryByLabelText(/index: Down/i)).toBeNull()
  })

  it('an index last written hours ago is idle, not down: still Ready, with its count and time', async () => {
    mockGetIndexStatus.mockResolvedValue(makeIndexStatus({
      status: 'healthy',
      document_count: 1234,
      last_indexed_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    }))

    renderAt('/search')

    const { summary, semantic } = await railCard()
    await waitFor(() => expect(summary).toHaveTextContent('6 entity types · semantic: ready'))
    expect(within(semantic).getByLabelText('Semantic index: Ready')).toBeInTheDocument()
    expect(semantic).toHaveTextContent(/1,234 docs · last indexed 3h ago/)
    expect(screen.queryByText('Down')).toBeNull()
  })

  it('shows project-scoped entity totals in the Searchable data rows before any query is run', async () => {
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
    expect(await screen.findByText(/^47 rows · keyword match/i)).toBeInTheDocument()
    expect(screen.getByText(/^20 rows · keyword match/i)).toBeInTheDocument()
    expect(screen.getByText(/^5 rows · keyword match/i)).toBeInTheDocument()
    expect(screen.getByText(/^3 rows · keyword match/i)).toBeInTheDocument()

    // The service is called with the active project from the store
    // (proj-1 in the mock above).
    expect(mockGetEntityCounts).toHaveBeenCalledWith('proj-1')
    expect(mockGetIndexStatus).toHaveBeenCalledWith('proj-1')
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

    // W12: was `mode=hybrid` (read as keyword then); a hybrid link now moves
    // the scope to Tests, and this case is about the All browse.
    renderAt('/search?mode=keyword&scope=all')

    // Both rows from the browse response render — proves the page hit
    // the API instead of short-circuiting to an empty grid.
    expect(await screen.findByText('most_recent_login_test')).toBeInTheDocument()
    expect(await screen.findByText('build-1042')).toBeInTheDocument()

    // Service was called with an empty query and no entity_types filter
    // (the hallmark of a scope=all browse).
    expect(mockGlobalSearch).toHaveBeenCalled()
    const callArgs = mockGlobalSearch.mock.calls[0][0] as {
      q: string
      project_id?: string
      entity_types?: string[]
    }
    expect(callArgs.q).toBe('')
    expect(callArgs.project_id).toBe('proj-1')
    expect(callArgs.entity_types).toBeUndefined()
  })

  it('refreshes result rows with the newly active project', async () => {
    const view = renderAt('/search?mode=hybrid&scope=all')
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenCalledTimes(1))

    mockGlobalSearch.mockClear()
    mockProjectState.activeProjectId = 'proj-2'
    view.rerender(searchTree('/search?mode=hybrid&scope=all'))

    await waitFor(() => expect(mockGlobalSearch).toHaveBeenCalledTimes(1))
    expect(mockGlobalSearch).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 'proj-2',
    }))
  })

  it('does not let the previous project response overwrite refreshed rows', async () => {
    let resolveFirst!: (response: GlobalSearchResponse) => void
    const firstResponse = new Promise<GlobalSearchResponse>((resolve) => {
      resolveFirst = resolve
    })
    mockGlobalSearch
      .mockReturnValueOnce(firstResponse)
      .mockResolvedValueOnce(makeResponse([makeResult({ title: 'project-two-row' })]))

    const view = renderAt('/search?mode=hybrid&scope=all')
    await waitFor(() => expect(mockGlobalSearch).toHaveBeenCalledTimes(1))

    mockProjectState.activeProjectId = 'proj-2'
    view.rerender(searchTree('/search?mode=hybrid&scope=all'))
    expect(await screen.findByText('project-two-row')).toBeInTheDocument()

    await act(async () => {
      resolveFirst(makeResponse([makeResult({ title: 'stale-project-one-row' })]))
      await firstResponse
    })
    expect(screen.queryByText('stale-project-one-row')).not.toBeInTheDocument()
    expect(screen.getByText('project-two-row')).toBeInTheDocument()
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
      query: '', search_type: 'keyword',
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

  // W12 SRCH-03 (fix after G-E's pass): a search that found nothing is still
  // worth saving (a query to re-run later), so No results offers Save search
  // too; a browse (no words) offers nothing to save.
  it('a search that found nothing can be saved from the No results card; a browse cannot', async () => {
    renderAt('/search?q=checkout%20timeout&scope=tests')
    const card = (await screen.findByRole('heading', { name: 'No results' })).parentElement?.parentElement as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: 'Save search' }))
    const form = within(card).getByRole('region', { name: 'Save this search' })
    expect(within(form).getByLabelText('Name')).toHaveValue('checkout timeout')
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockPostData).toHaveBeenCalledWith('/api/v1/saved-views', {
      project_id: 'proj-1',
      name: 'checkout timeout',
      page: 'search',
      filters: { kind: 'search', q: 'checkout timeout', scope: 'tests', mode: 'keyword' },
      is_shared: false,
    }, { suppressToast: true }))
    expect(toast.success).toHaveBeenCalledWith('Saved “checkout timeout”.')
  })

  it('a browse with an empty box offers no Save search', async () => {
    renderAt('/search?scope=all')
    await screen.findByRole('heading', { name: 'No results' })
    expect(screen.queryByRole('button', { name: 'Save search' })).toBeNull()
  })

  /**
   * W27b PAN-05 (Y3): Clear history asks inside the Recent searches card, right
   * under its header, never with the browser's prompt. The first click clears
   * nothing; the strip's Clear is the only control that does; Cancel and Escape
   * keep every recent. W12 (deliberate rewrite, §6.5): the recents are the
   * server's (GET /search/recent), and the strip's Clear sends ONE
   * DELETE /search/recent (`clearRecent`); Cancel and Escape send nothing.
   */
  describe('Clear history asks in the recent searches card', () => {
    const RECENTS = {
      items: [
        { q: 'checkout flaky', mode: 'keyword', scope: 'all', project_id: 'proj-1', result_count: 4, counts_exact: true, ts: new Date().toISOString() },
        { q: 'login timeout', mode: 'keyword', scope: 'all', project_id: 'proj-1', result_count: 2, counts_exact: true, ts: new Date().toISOString() },
      ],
      max: 20,
      retention_days: 30,
    }
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null
    beforeEach(() => vi.mocked(toast.success).mockClear())

    async function openRecents() {
      mockRecent.mockResolvedValue(RECENTS)
      renderAt('/search')
      await screen.findByRole('button', { name: /checkout flaky/i })
      // Load-time reads settle before the count below.
      await waitFor(() => expect(mockGlobalSearch).toHaveBeenCalled())
    }

    it('asks under the card header; the strip\'s Clear clears, once, on the server', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      await openRecents()
      const searches = mockGlobalSearch.mock.calls.length

      fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
      const asked = strip() as HTMLElement
      expect(asked).not.toBeNull()
      expect(asked.querySelector('p')?.textContent).toBe("Clear all 2 recent searches? This can't be undone.")
      // Inside the Recent searches card, after its header row and before the first recent.
      const header = screen.getByRole('heading', { name: 'Recent searches' }).parentElement as HTMLElement
      const card = header.parentElement as HTMLElement
      expect(card.contains(asked)).toBe(true)
      expect(header.compareDocumentPosition(asked) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      const firstRecent = screen.getByRole('button', { name: /checkout flaky/i })
      expect(asked.compareDocumentPosition(firstRecent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      // The first click cleared nothing.
      expect(mockClearRecent).not.toHaveBeenCalled()
      expect(toast.success).not.toHaveBeenCalled()

      fireEvent.click(within(asked).getByRole('button', { name: 'Clear' }))
      expect(strip()).toBeNull()
      await waitFor(() => expect(screen.getByText('Your queries will appear here.')).toBeInTheDocument())
      expect(mockClearRecent).toHaveBeenCalledTimes(1)
      expect(toast.success).toHaveBeenCalledTimes(1)
      expect(toast.success).toHaveBeenCalledWith('Recent searches cleared')
      expect(mockGlobalSearch.mock.calls.length, 'clearing runs no search').toBe(searches)
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('Cancel and Escape keep every recent', async () => {
      await openRecents()
      fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
      expect(strip()).toBeNull()

      fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
      fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
      expect(strip()).toBeNull()

      expect(mockClearRecent).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: /login timeout/i })).toBeInTheDocument()
      expect(toast.success).not.toHaveBeenCalled()
    })

    it('a refused clear keeps the list and says the server\'s sentence in the card', async () => {
      mockClearRecent.mockRejectedValue({ response: { status: 503, data: { detail: 'Could not clear recent searches: the cache is unavailable.' } } })
      await openRecents()
      fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Clear' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Could not clear recent searches: the cache is unavailable.')
      expect(screen.getByRole('button', { name: /checkout flaky/i })).toBeInTheDocument()
      expect(toast.success).not.toHaveBeenCalled()
    })
  })
})

describe('SearchPage — declaring that it ignores the release filter', () => {
  /**
   * Search deliberately spans every release, and the release picker is visible
   * in the TopBar on this page. Without saying so, a reader with 2.4.0 selected
   * takes these results as 2.4.0's — and the one result they were hunting for,
   * last run in 2.3.0, reads as proof the test is gone. That is the exact
   * failure the backend's scope note describes.
   *
   * The reason is taken from the API rather than written here, so there is one
   * sentence rather than two that drift.
   */
  const NOTE =
    'Search spans every release in the project by design — a test you are ' +
    'looking for may have last run in a different one.'

  function withResults(scope?: { release: 'not_applicable'; note: string }) {
    mockGlobalSearch.mockResolvedValue({
      items: [makeResult()],
      total: 1,
      query: 'login',
      search_type: 'keyword',
      entity_counts: {},
      page: 1,
      size: 25,
      pages: 1,
      ...(scope ? { scope } : {}),
    } as unknown as GlobalSearchResponse)
  }

  beforeEach(() => {
    useReleaseStore.setState({
      activeReleaseId: 'rel-1',
      scopedProjectId: 'proj-1',
    })
  })

  it('shows the all-releases badge when a release is selected', async () => {
    withResults({ release: 'not_applicable', note: NOTE })
    renderAt('/search?q=login')

    expect(await screen.findByText('All releases')).toBeTruthy()
  })

  it('carries the API note into the badge, rather than its own copy', async () => {
    withResults({ release: 'not_applicable', note: NOTE })
    renderAt('/search?q=login')

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toContain(NOTE)
  })

  it('says nothing when no release is selected', async () => {
    // The control, and the badge's whole design: with no release chosen there
    // is no discrepancy to explain, and the page looks as it did before the
    // release axis existed.
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    withResults({ release: 'not_applicable', note: NOTE })
    renderAt('/search?q=login')

    await screen.findByText(/Results/i)
    expect(screen.queryByText('All releases')).toBeNull()
  })

  it('still renders the badge when the API sends no note', async () => {
    // An older backend, or a cached response from before this shipped. The
    // discrepancy is real either way; the badge falls back to its own generic
    // tooltip rather than disappearing.
    withResults(undefined)
    renderAt('/search?q=login')

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toContain('Not filtered by the selected release')
  })
})
