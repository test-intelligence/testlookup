import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import SuiteCasesPage from './SuiteCasesPage'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import type { SuiteDetailResponse, SuiteDetailTestCase } from '@/types/analytics'
import type { CanonicalTestCase } from '@/types/suites'

// Mock the hooks so the page renders against scripted state without
// needing an SWR cache or HTTP fetcher. Same pattern as
// ``TestCasePage.test.tsx`` / ``CanonicalDetailPage.test.tsx``.
vi.mock('@/hooks/useSuites', () => ({
  useSuite: vi.fn(),
  useSuites: vi.fn(),
  useSuiteTestCases: vi.fn(),
  refreshSuites: vi.fn(),
}))

// The window's analytics (KPIs, per-test columns, runs) and, for an empty
// catalog only, the unscoped run-level probe. Answered per call so a test can
// see the arguments each read was made with.
const mockUseSuiteDetail = vi.fn()
vi.mock('@/hooks/useMetrics', () => ({
  useSuiteDetail: (...args: unknown[]) => mockUseSuiteDetail(...args),
}))

// The Charts tab's panel is the former `/coverage/suite` body, tested in
// `SuiteDetailPage.test.tsx`; here, only that the tab mounts it (lazily) with
// the page's suite and window.
vi.mock('./SuiteDetailPage', () => ({
  SuiteChartsPanel: ({ suiteName, days }: { suiteName: string; days: number }) => (
    <div data-testid="suite-charts" data-suite={suiteName} data-days={days} />
  ),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({
    hasRole: () => true,
    isAdmin: false,
    canManageUsers: false,
    canAccessManagement: false,
  })),
}))

vi.mock('@/services/suitesService', () => ({
  suitesService: {
    linkCanonicalToSuite: vi.fn(),
    bulkLinkCanonicals: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const SUITE_A = {
  id: 'suite-a',
  project_id: 'proj-1',
  name: 'Login Suite',
  description: 'Smoke',
  tags: null,
  is_default: false,
  test_case_count: 2,
  created_at: '2026-05-15T10:00:00Z',
  updated_at: null,
}

const SUITE_B = {
  id: 'suite-b',
  project_id: 'proj-1',
  name: 'Regression Suite',
  description: null,
  tags: null,
  is_default: true,
  test_case_count: 0,
  created_at: '2026-05-15T10:00:00Z',
  updated_at: null,
}

function _case(id: string, name: string) {
  return {
    id,
    project_id: 'proj-1',
    test_suite_id: 'suite-a',
    test_suite_name: 'Login Suite',
    test_fingerprint: `fp-${id}`,
    test_name: name,
    class_name: 'auth.LoginTest',
    status: 'active' as const,
    source: 'execution' as const,
    first_seen_run_id: null,
    last_seen_run_id: null,
    last_seen_test_case_id: null,
    deleted_at_run_id: null,
    managed_test_case_id: null,
    review_tag: null,
    tags: null,
    run_count: null,
    created_at: '2026-05-15T10:00:00Z',
    updated_at: null,
  }
}

function _stat(fingerprint: string, overrides: Partial<SuiteDetailTestCase> = {}): SuiteDetailTestCase {
  return {
    test_fingerprint: fingerprint,
    test_name: 'x',
    class_name: 'auth.LoginTest',
    total_executions: 5,
    passed: 4,
    failed: 1,
    skipped: 0,
    pass_rate: 80,
    avg_duration_ms: 1500,
    last_status: 'FAILED',
    last_error: 'AssertionError: expected 200, got 500',
    last_run_at: '2026-05-15T10:00:00Z',
    is_flaky: true,
    ...overrides,
  }
}

/** c1 ran in the window (flaky, last failed); c2 did not. */
const DETAIL: SuiteDetailResponse = {
  summary: { unique_tests: 1, total_executions: 5, passed: 4, failed: 1, pass_rate: 80, avg_duration_ms: 1500 },
  test_cases: [_stat('fp-c1')],
  recent_runs: [
    { test_run_id: 'run-1', build_number: 'b41', run_date: '2026-05-14T10:00:00Z', passed: 9, failed: 1, skipped: 0, pass_rate: 90 },
    { test_run_id: 'run-2', build_number: 'b42', run_date: '2026-05-15T10:00:00Z', passed: 8, failed: 2, skipped: 0, pass_rate: 80 },
  ],
}

const EMPTY_DETAIL: SuiteDetailResponse = {
  summary: { unique_tests: 0, total_executions: 0, passed: 0, failed: 0, pass_rate: 0, avg_duration_ms: null },
  test_cases: [],
  recent_runs: [],
}

/** The current URL, for the tab / window assertions. */
function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderPage(url = '/suites/suite-a') {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/suites" element={<div>Suites List</div>} />
        <Route
          path="/suites/:suiteId"
          element={
            <>
              <SuiteCasesPage />
              <LocationProbe />
            </>
          }
        />
        <Route
          path="/canonical-test-cases/:canonicalId"
          element={<div>Canonical Detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

const location = () => screen.getByTestId('location').textContent ?? ''

async function scriptSuite(cases: CanonicalTestCase[] = [_case('c1', 'test_login'), _case('c2', 'test_logout')]) {
  const { useSuite, useSuites, useSuiteTestCases } = await import('@/hooks/useSuites')
  ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({
    data: SUITE_A,
    isLoading: false,
    error: null,
  })
  ;(useSuites as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [SUITE_A, SUITE_B], total: 2 },
  })
  ;(useSuiteTestCases as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: cases, total: cases.length },
    isLoading: false,
  })
}

/** The window's analytics for a named suite; nothing for a null name (no request). */
function scriptDetail(detail: SuiteDetailResponse | undefined, extra: { isLoading?: boolean; error?: unknown } = {}) {
  mockUseSuiteDetail.mockImplementation((name: string | null) =>
    name ? { data: detail, isLoading: extra.isLoading ?? false, error: extra.error ?? null } : { data: undefined, isLoading: false, error: null },
  )
}

beforeEach(() => {
  mockUseSuiteDetail.mockReset()
  scriptDetail(DETAIL)
  act(() => useTimeWindowStore.getState().setDays(30))
})

describe('SuiteCasesPage bulk move', () => {
  beforeEach(async () => {
    await scriptSuite()
  })

  it('bulk action bar is hidden until a row is selected', () => {
    renderPage()
    expect(screen.queryByRole('region', { name: /Bulk actions/i })).toBeNull()
  })

  it('per-row checkbox reveals the action bar with the right count', () => {
    renderPage()
    const c1Checkbox = screen.getByLabelText('Select test_login')
    fireEvent.click(c1Checkbox)
    expect(screen.getByRole('region', { name: /Bulk actions/i })).toBeInTheDocument()
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })

  it('select-all checkbox toggles every row', () => {
    renderPage()
    const selectAll = screen.getByLabelText('Select all')
    fireEvent.click(selectAll)
    // Now both per-row boxes should be checked + counter at 2.
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    // And the header checkbox should report "Deselect all" after flipping.
    expect(screen.getByLabelText('Deselect all')).toBeInTheDocument()
  })

  it('Clear button collapses the action bar', () => {
    renderPage()
    fireEvent.click(screen.getByLabelText('Select test_login'))
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Clear/i }))
    expect(screen.queryByRole('region', { name: /Bulk actions/i })).toBeNull()
  })

  it('bulk Move opens the modal and posts the selected ids to the service', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.bulkLinkCanonicals as ReturnType<typeof vi.fn>).mockResolvedValue({
      moved: 2,
      skipped_already_in_target: 0,
      missing_ids: [],
    })

    renderPage()
    fireEvent.click(screen.getByLabelText('Select all'))
    // Open the bulk modal via the action-bar's "Move" button. There's
    // also a per-row Move button — disambiguate by the count suffix on
    // the bar's button label is the same text "Move", so use the bar's
    // region as the scope.
    const bar = screen.getByRole('region', { name: /Bulk actions/i })
    const barButton = bar.querySelector('button')
    if (barButton === null) throw new Error('Bulk actions bar has no button')
    fireEvent.click(barButton)

    // The bulk modal heading reflects the selection count.
    expect(
      await screen.findByRole('heading', { name: /Move 2 test cases/i }),
    ).toBeInTheDocument()

    // Submit moves everything to the default candidate (suite-b is the
    // first non-self target the modal pre-selects).
    fireEvent.click(screen.getByRole('button', { name: /^Move 2$/ }))

    await waitFor(() => {
      expect(suitesService.bulkLinkCanonicals).toHaveBeenCalledWith(
        'suite-b',
        expect.arrayContaining(['c1', 'c2']),
      )
    })
  })

  it('the per-row Move opens the single-case modal and links that case', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.linkCanonicalToSuite as ReturnType<typeof vi.fn>).mockResolvedValue({})
    renderPage()
    const row = screen.getByRole('link', { name: 'test_logout' }).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /Move/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Move test case' })
    fireEvent.click(within(dialog).getByRole('button', { name: /^Move$/ }))
    await waitFor(() => expect(suitesService.linkCanonicalToSuite).toHaveBeenCalledWith('c2', 'suite-b'))
  })

  it('the bulk bar belongs to the Tests tab: another tab hides it, Tests brings it back', () => {
    renderPage()
    fireEvent.click(screen.getByLabelText('Select test_login'))
    fireEvent.click(screen.getByRole('tab', { name: /^Runs/ }))
    expect(screen.queryByRole('region', { name: /Bulk actions/i })).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: /^Tests/ }))
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })
})

// P2 item 7: the "Last seen" column showed a run-id prefix under a header that
// read as a date. The row has no timestamp, so the cell is a link to that run
// labelled as a run, under a header that claims no date.
describe('SuiteCasesPage latest-run column', () => {
  beforeEach(async () => {
    await scriptSuite([
      { ..._case('c1', 'test_login'), last_seen_run_id: '1a2b3c4d-0000-4000-8000-000000000001' },
      _case('c2', 'test_logout'),
    ])
  })

  it('no column of its own (P4: the table ran past its card at 1280 px): the row\'s Run action names the run', () => {
    renderPage()
    expect(screen.queryByRole('columnheader', { name: 'Latest run' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: /Last seen/i })).toBeNull()
  })

  it('renders the run id as a link into that run, labelled as a run', () => {
    renderPage()
    const link = screen.getByRole('link', { name: 'run 1a2b3c4d' })
    expect(link.getAttribute('href')).toMatch(/^\/runs\/1a2b3c4d-0000-4000-8000-000000000001(\/tests\/.+)?$/)
  })

  it('shows a dash, not a link, when the case has no run', () => {
    renderPage()
    expect(screen.getAllByRole('link', { name: /^run / })).toHaveLength(1)
  })
})

// ── UX redesign P4: one page for one suite ───────────────────────────────────

describe('SuiteCasesPage — the suite page template (P4)', () => {
  beforeEach(async () => {
    await scriptSuite()
  })

  it('one compact header with the help topic, the three tabs in it, Tests selected by default', () => {
    renderPage()
    expect(document.querySelectorAll('[data-page-header]')).toHaveLength(1)
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('heading', { level: 1, name: 'Login Suite' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Help: Login Suite' })).toHaveAttribute('data-help-topic', 'concepts')
    const tablist = screen.getByRole('tablist', { name: 'Suite sections' })
    expect(within(tablist).getAllByRole('tab').map((t) => t.getAttribute('data-tab'))).toEqual(['tests', 'runs', 'charts'])
    expect(within(tablist).getByRole('tab', { name: /^Tests/ })).toHaveAttribute('aria-selected', 'true')
    // The counts in the labels: two cases, two runs.
    expect(within(tablist).getByRole('tab', { name: /^Tests/ })).toHaveTextContent('Tests2')
    expect(within(tablist).getByRole('tab', { name: /^Runs/ })).toHaveTextContent('Runs2')
    expect(screen.getByRole('tabpanel', { name: 'Tests' })).toBeInTheDocument()
  })

  it('header → toolbar (the window) → KPI strip → the one primary table, in that order', () => {
    renderPage()
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const toolbar = document.querySelector('[data-toolbar]') as HTMLElement
    const strip = document.querySelector('[data-kpi-strip]') as HTMLElement
    const primaries = document.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(within(toolbar).getByRole('radiogroup', { name: 'Time window' })).toBeInTheDocument()
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(header, toolbar)).toBe(true)
    expect(follows(toolbar, strip)).toBe(true)
    expect(follows(strip, primary)).toBe(true)
    // The primary content is the catalog table, inside the Tests panel.
    expect(within(primary).getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('tabpanel', { name: 'Tests' })).toContainElement(primary)
    // The page's only tablist is the header's (no second tab bar, no disclosure, above it).
    expect(screen.getAllByRole('tablist')).toHaveLength(1)
    expect(document.querySelector('[data-disclosure]')).toBeNull()
  })

  it('the KPI strip: five tiles from the window summary', () => {
    renderPage()
    const strip = document.querySelector('[data-kpi-strip]') as HTMLElement
    const tiles = Array.from(strip.querySelectorAll('[data-metric-card]'), (tile) => tile.textContent)
    expect(tiles).toEqual(['Tests run1', 'Executions5', 'Pass rate80.0%', 'Failed1', 'Avg duration1.5s'])
  })

  it('no run in the window: no KPI strip (zeros would read as a measured 0 %)', () => {
    scriptDetail(EMPTY_DETAIL)
    renderPage()
    expect(document.querySelector('[data-kpi-strip]')).toBeNull()
    // The catalog is still the page.
    expect(screen.getByRole('link', { name: 'test_login' })).toBeInTheDocument()
  })

  it('the Tests table: each test with its pass rate, executions, duration, latest result, flaky flag and last error', () => {
    renderPage()
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual([
      '', 'Test', 'Class', 'Pass rate', 'Executions', 'Avg duration', 'Last result', 'Status', '',
    ])
    const row = document.querySelector('[data-test-row="fp-c1"]') as HTMLElement
    expect(within(row).getByText('80.0%')).toBeInTheDocument()
    expect(within(row).getByTitle('4 passed · 1 failed · 0 skipped')).toBeInTheDocument()
    expect(within(row).getByText('5')).toBeInTheDocument()
    expect(within(row).getByText('1.5s')).toBeInTheDocument()
    expect(within(row).getByText('FAILED')).toBeInTheDocument()
    expect(within(row).getByText('Flaky')).toBeInTheDocument()
    expect(within(row).getByText('AssertionError: expected 200, got 500')).toBeInTheDocument()
  })

  it('a test the window has no row for: dashes that say why, never 0 %', () => {
    renderPage()
    const row = document.querySelector('[data-test-row="fp-c2"]') as HTMLElement
    const dashes = row.querySelectorAll('[data-no-run]')
    expect(dashes).toHaveLength(4)
    for (const dash of dashes) expect(dash).toHaveAttribute('title', 'Not run in the last 30 days')
    expect(within(row).queryByText(/%$/)).toBeNull()
    expect(within(row).queryByText('Flaky')).toBeNull()
  })

  it('a test whose every execution was skipped: no pass rate (a dash), its executions still counted', () => {
    scriptDetail({ ...DETAIL, test_cases: [_stat('fp-c1', { pass_rate: null as unknown as number, passed: 0, failed: 0, skipped: 3, total_executions: 3, last_status: 'SKIPPED', is_flaky: false, last_error: null })] })
    renderPage()
    const row = document.querySelector('[data-test-row="fp-c1"]') as HTMLElement
    expect(within(row).getByTitle('No evaluated run: every execution was skipped')).toHaveTextContent('—')
    expect(within(row).queryByText(/%$/)).toBeNull()
    expect(within(row).getByText('3')).toBeInTheDocument()
    expect(within(row).getByText('SKIPPED')).toBeInTheDocument()
  })

  it('the analytics read failed: the Tests tab says the run columns are empty because of it', () => {
    scriptDetail(undefined, { error: new Error('boom') })
    renderPage()
    expect(document.querySelector('[data-stats-note]')).toHaveTextContent(/Run statistics could not be loaded/)
  })

  it('at the analytics row cap, the Tests tab says the run columns cover only part of the catalog', () => {
    const many = Array.from({ length: 200 }, (_, i) => _stat(`fp-other-${i}`))
    scriptDetail({ ...DETAIL, test_cases: many })
    renderPage()
    expect(document.querySelector('[data-stats-note]')).toHaveTextContent(
      'Run statistics cover the 200 tests with the most failures in the last 30 days.',
    )
  })

  it('below the cap, no note', () => {
    renderPage()
    expect(document.querySelector('[data-stats-note]')).toBeNull()
  })

  it('?tab=runs selects Runs: the recent runs are the primary content, each linking to its run', () => {
    renderPage('/suites/suite-a?tab=runs')
    expect(screen.getByRole('tab', { name: /^Runs/ })).toHaveAttribute('aria-selected', 'true')
    const panel = screen.getByRole('tabpanel', { name: 'Runs' })
    const primary = panel.querySelector('[data-primary]') as HTMLElement
    expect(primary).toHaveAttribute('data-suite-recent-runs')
    expect(within(panel).getByRole('heading', { name: 'Recent Runs (2)' })).toBeInTheDocument()
    const links = within(panel).getAllByRole('link', { name: 'View run →' }).map((a) => a.getAttribute('href'))
    expect(links).toEqual(['/runs/run-1', '/runs/run-2'])
    // The catalog is not rendered under another tab.
    expect(document.querySelector('[data-suite-tests]')).toBeNull()
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
  })

  it('Runs with nothing in the window: says so', () => {
    scriptDetail(EMPTY_DETAIL)
    renderPage('/suites/suite-a?tab=runs')
    expect(screen.getByText('No runs in this window')).toBeInTheDocument()
    expect(screen.getByText('No run of this suite in the last 30 days.')).toBeInTheDocument()
  })

  it('?tab=charts selects Charts: the former /coverage/suite charts, for this suite and window', async () => {
    renderPage('/suites/suite-a?tab=charts')
    expect(screen.getByRole('tab', { name: 'Charts' })).toHaveAttribute('aria-selected', 'true')
    const charts = await screen.findByTestId('suite-charts')
    expect(charts).toHaveAttribute('data-suite', 'Login Suite')
    expect(charts).toHaveAttribute('data-days', '30')
    expect(screen.getByRole('tabpanel', { name: 'Charts' })).toContainElement(charts)
    expect(document.querySelector('[data-suite-tests]')).toBeNull()
  })

  it('an unknown ?tab= falls back to Tests', () => {
    renderPage('/suites/suite-a?tab=history')
    expect(screen.getByRole('tab', { name: /^Tests/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('choosing a tab writes ?tab=; choosing Tests again clears it', () => {
    renderPage()
    fireEvent.click(screen.getByRole('tab', { name: 'Charts' }))
    expect(location()).toBe('/suites/suite-a?tab=charts')
    fireEvent.click(screen.getByRole('tab', { name: /^Runs/ }))
    expect(location()).toBe('/suites/suite-a?tab=runs')
    fireEvent.click(screen.getByRole('tab', { name: /^Tests/ }))
    expect(location()).toBe('/suites/suite-a')
  })

  it('the window picker drives the window read (the KPIs, the run columns, the runs)', () => {
    // The window read: the one made without options (the unscoped probe is the other).
    const lastWindowRead = () => {
      const reads = mockUseSuiteDetail.mock.calls.filter((call) => call.length === 2)
      return reads[reads.length - 1]
    }
    renderPage()
    expect(lastWindowRead()).toEqual(['Login Suite', 30])
    fireEvent.click(screen.getByRole('radio', { name: '7d' }))
    expect(lastWindowRead()).toEqual(['Login Suite', 7])
    expect(useTimeWindowStore.getState().days).toBe(7)
    expect(screen.getByTitle('4 passed · 1 failed · 0 skipped')).toBeInTheDocument()
  })

  it("an old link's ?days=90 becomes the window, and leaves the URL", async () => {
    renderPage('/suites/suite-a?tab=charts&days=90')
    await waitFor(() => expect(location()).toBe('/suites/suite-a?tab=charts'))
    expect(useTimeWindowStore.getState().days).toBe(90)
    expect(screen.getByRole('radio', { name: '90d' })).toHaveAttribute('aria-checked', 'true')
    // Never a read at the store's old window first.
    expect(mockUseSuiteDetail.mock.calls.filter(([name]) => name).every(([, d]) => d === 90)).toBe(true)
    expect(await screen.findByTestId('suite-charts')).toHaveAttribute('data-days', '90')
  })

  it('an invalid ?days=365 is dropped: the page keeps its window, nothing asks for 365', async () => {
    renderPage('/suites/suite-a?days=365')
    await waitFor(() => expect(location()).toBe('/suites/suite-a'))
    expect(useTimeWindowStore.getState().days).toBe(30)
    expect(mockUseSuiteDetail.mock.calls.some(([, d]) => d === 365)).toBe(false)
  })

  it('a populated catalog asks no unscoped run-level probe', () => {
    renderPage()
    const unscoped = mockUseSuiteDetail.mock.calls.filter(([, , options]) => options)
    expect(unscoped.every(([name]) => name === null)).toBe(true)
  })

  it('nothing on the page links to the retired /coverage/suite', () => {
    renderPage()
    const hrefs = Array.from(document.querySelectorAll('a'), (a) => a.getAttribute('href') ?? '')
    expect(hrefs.filter((href) => href.includes('/coverage/suite'))).toEqual([])
    expect(screen.queryByRole('link', { name: /Open analytics/i })).toBeNull()
  })

  it('the ⋯ menu keeps the way back to every suite', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /more actions/i }))
    expect(screen.getByRole('menuitem', { name: /All suites/ })).toHaveAttribute('href', '/suites')
  })
})

describe('SuiteCasesPage — an empty catalog', () => {
  beforeEach(async () => {
    await scriptSuite([])
  })

  it('runs landed but no per-test rows: the warning, from an UNSCOPED probe, and its Charts tab button', () => {
    mockUseSuiteDetail.mockImplementation((name: string | null, _days: number, options?: { releaseScoped?: boolean }) => {
      if (!name) return { data: undefined, isLoading: false, error: null }
      if (options?.releaseScoped === false) {
        return { data: { ...EMPTY_DETAIL, summary: { ...EMPTY_DETAIL.summary, unique_tests: 12, total_executions: 40 } }, isLoading: false, error: null }
      }
      return { data: EMPTY_DETAIL, isLoading: false, error: null }
    })
    renderPage()
    expect(mockUseSuiteDetail).toHaveBeenCalledWith('Login Suite', 30, { releaseScoped: false })
    expect(screen.getByText(/12 tests reported by recent runs, but per-test rows are missing/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'open the Charts tab' }))
    expect(screen.getByRole('tab', { name: 'Charts' })).toHaveAttribute('aria-selected', 'true')
    expect(location()).toBe('/suites/suite-a?tab=charts')
  })

  it('nothing ever ran: the empty state', () => {
    scriptDetail(EMPTY_DETAIL)
    renderPage()
    expect(screen.getByText('No test cases in this suite')).toBeInTheDocument()
  })
})
