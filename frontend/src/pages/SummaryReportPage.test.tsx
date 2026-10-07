/**
 * SummaryReportPage tests — pin the contract for the consolidated
 * per-project Summary Report:
 *
 *   - "Pick a single project" empty state when ALL_PROJECTS is active.
 *   - "No executions in this window" empty state when totals are zero.
 *   - KPI tiles + per-suite table render when the API returns data.
 *   - Window chip + mode toggle re-fetch the data.
 *   - Export PDF (in the header's ⋯ since UX redesign P3) hits the service
 *     and triggers a download.
 *   - The P3 page template: one merged KPI row, results by suite + the
 *     per-suite table first, every export in ⋯.
 *
 * Data path: ``summaryReportService.get`` (mocked) feeds
 * ``useSummaryReport``; the page reads from ``useProjectStore`` for
 * scope.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SummaryReportPage from './SummaryReportPage'
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'
import { useReleaseStore } from '@/store/releaseStore'
import type { EnvelopeMeta } from '@/lib/viz/contracts'

const mockGet = vi.fn()
const mockDownloadPdf = vi.fn()
const mockDownloadXlsx = vi.fn()
// VIZ-607: the export buttons ask first; by default the report is small and downloads now.
const SMALL = { delivery: 'download', estimated_tests: 10, export: null, dispatched: null }
const mockRequestExport = vi.fn(async (..._args: unknown[]) => SMALL)
const mockListExports = vi.fn(async (..._args: unknown[]) => [] as unknown[])

// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: {
    get: (...args: unknown[]) => mockGet(...args),
    downloadPdf: (...args: unknown[]) => mockDownloadPdf(...args),
    downloadXlsx: (...args: unknown[]) => mockDownloadXlsx(...args),
    requestExport: (...args: unknown[]) => mockRequestExport(...args),
    listExports: (...args: unknown[]) => mockListExports(...args),
  },
}))

// Default project mock: a single active project (so the all-projects gate
// doesn't fire). Individual tests override this via vi.mocked(...).mockReturnValueOnce.
const mockProjectStore = vi.fn((selector: (s: {
  activeProjectId: string | null
  activeProject: null | { id: string; name: string }
}) => unknown) => selector({
  activeProjectId: 'p1',
  activeProject: { id: 'p1', name: 'GoogleProject' },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: unknown) => mockProjectStore(selector as never),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

// The catalogue's trend request (K7) held loading, and the top bar's cached
// release list. The trend's own behaviour is `SummaryCatalogue.test.tsx`'s.
const trendsCalls = vi.hoisted(() => [] as number[])
vi.mock('@/components/reports/catalogue/useTrendsSeries', () => ({
  useTrendsSeries: (days: number) => {
    trendsCalls.push(days)
    return { status: 'loading' }
  },
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))

function makeReport(overrides: Partial<SummaryReport> = {}): SummaryReport {
  return {
    project_id: 'p1',
    project_name: 'GoogleProject',
    mode: 'window',
    window_days: 7,
    generated_at: '2026-05-16T00:00:00+00:00',
    period_start: '2026-05-09T00:00:00+00:00',
    period_end: '2026-05-16T00:00:00+00:00',
    totals: {
      total_test_cases: 200,
      passed: 180,
      failed: 15,
      skipped: 3,
      broken: 2,
      evaluated: 197,
      pass_rate_pct: 90.0,
      fail_rate_pct: 7.5,
      skip_rate_pct: 1.5,
      broken_rate_pct: 1.0,
      weighted_pass_rate_pct: 91.4,
    },
    run_count: 4,
    runs_per_day: 0.57,
    avg_duration_ms: 12_345,
    latest_run_at: '2026-05-15T00:00:00+00:00',
    flaky_test_count: 3,
    flaky_rate_pct: 1.5,
    suites: [
      {
        suite_name: 'checkout-api',
        total: 120, passed: 102, failed: 13, skipped: 3, broken: 2,
        pass_rate_pct: 85.0, weighted_pass_rate_pct: 87.2,
        last_run_at: '2026-05-15T00:00:00+00:00',
      },
      {
        suite_name: 'auth-api',
        total: 80, passed: 78, failed: 2, skipped: 0, broken: 0,
        pass_rate_pct: 97.5, weighted_pass_rate_pct: 97.5,
        last_run_at: '2026-05-15T00:00:00+00:00',
      },
    ],
    top_failing_tests: [
      { suite_name: 'checkout-api', class_name: 'CheckoutTests', test_name: 'test_pay', failures: 8 },
    ],
    ...overrides,
  }
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/reports/summary']}>
        <SummaryReportPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

/** The header's ⋯ items, in order (UX redesign P3: every export is in the overflow menu). */
const EXPORT_ITEMS = [
  'Export PDF',
  'Export Excel',
  'Export PDF in background',
  'Export Excel in background',
  'Analysis report (1d)',
  'Analysis report (7d)',
]

/** Open the header's ⋯ once the report has data (the exports are enabled then), and pick `name`. */
async function chooseExport(name: string) {
  await screen.findByText('Total tests')
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  const item = screen.getByRole('menuitem', { name })
  expect(item).toBeEnabled()
  fireEvent.click(item)
}

const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('SummaryReportPage', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockDownloadPdf.mockReset()
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    // Clean state so each test starts from documented defaults. The
    // window is now in the global Zustand store (key
    // ``testlookup-time-window``); aggregation mode is still page-local.
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    try { localStorage.removeItem('summary-report.mode') } catch { /* ignore */ }
    // Reset the in-memory store too — its constructor reads from
    // localStorage once at module load, so clearing the key isn't enough
    // between tests.
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('defaults to the shared window + latest-per-suite aggregation on first visit', async () => {
    // Regression for 2026-05-18 bug report: fresh users were landing on
    // the window-mode view and seeing Suite totals scaled by run count
    // (5 runs × 100 tests = 500), which reads as duplicate rows. Latest
    // mode shows one snapshot per suite — what users actually expect on
    // "show me where things stand right now". The window itself comes
    // from the shared store default (``DEFAULT_TIME_WINDOW_DAYS``, 7d as
    // of the v2 migration), not the old hardcoded 24h.
    mockGet.mockResolvedValue(makeReport({ mode: 'latest' }))

    renderPage()

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({
          days: DEFAULT_TIME_WINDOW_DAYS,
          mode: 'latest' as SummaryReportMode,
        }),
      )
    })
  })

  it('restores the last-picked window from the shared store on mount', async () => {
    useTimeWindowStore.setState({ days: 30 })
    try { localStorage.setItem('summary-report.mode', 'latest') } catch { /* ignore */ }
    mockGet.mockResolvedValue(makeReport({ mode: 'latest', window_days: 30 }))

    renderPage()

    await waitFor(() => {
      // First call after mount uses the persisted values, not the defaults.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: 30, mode: 'latest' as SummaryReportMode }),
      )
    })
  })

  it('persists the user picked window so the next visit restores it', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()
    await waitFor(() => expect(mockGet).toHaveBeenCalled())

    fireEvent.click(screen.getByText('7d'))
    await waitFor(() => {
      expect(useTimeWindowStore.getState().days).toBe(7)
    })

    fireEvent.click(screen.getByRole('radio', { name: /Latest run per suite/i }))
    await waitFor(() => {
      expect(localStorage.getItem('summary-report.mode')).toBe('latest')
    })
  })

  it('snaps a non-supported global window to the nearest allowed option', async () => {
    // 14d isn't in Summary Report's option set [1,7,30,90]; the nearest
    // supported value is 7. The page should not refuse to render — it
    // should silently fall back to the closest.
    useTimeWindowStore.setState({ days: 14 })
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    await waitFor(() => {
      // Page's allowed set = [1,7,30,90]; nearest to 14 is 7.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: 7 }),
      )
    })
  })

  it('shows the all-projects empty state when no specific project is active', () => {
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: '__ALL__',
      activeProject: null,
    }))

    renderPage()

    expect(screen.getByText(/Pick a single project/i)).toBeInTheDocument()
    // The KPI grid and per-suite table must NOT render in this state — pin
    // those instead of asserting on the fetcher, which the SWR hook may
    // still fire (the backend short-circuits to an empty envelope).
    expect(screen.queryByText('Total tests')).not.toBeInTheDocument()
    expect(screen.queryByText('Per-suite breakdown')).not.toBeInTheDocument()
  })

  it('shows the no-data empty state when totals are zero', async () => {
    mockGet.mockResolvedValue(makeReport({
      totals: {
        total_test_cases: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
        evaluated: 0, pass_rate_pct: 0, fail_rate_pct: 0,
        skip_rate_pct: 0, broken_rate_pct: 0, weighted_pass_rate_pct: 0,
      },
      suites: [],
      top_failing_tests: [],
      run_count: 0,
    }))

    renderPage()

    expect(await screen.findByText(/No executions in this window/i)).toBeInTheDocument()
  })

  it('renders the no-data empty state (no crash) when the envelope has null totals', async () => {
    // The backend can short-circuit to an empty envelope where ``totals``
    // is absent entirely (not just zeroed) — the same shape the
    // all-projects gate alludes to. The page guards ``totals == null``
    // before dereferencing the KPI fields, so a missing-totals payload
    // must fall through to the empty state rather than throwing on a
    // ``totals.total_test_cases`` read. Regression for the guard that
    // narrows ``totals`` in place of the old non-null assertions.
    mockGet.mockResolvedValue(makeReport({
      totals: null as unknown as SummaryReport['totals'],
      suites: [],
      top_failing_tests: [],
      run_count: 0,
    }))

    renderPage()

    expect(await screen.findByText(/No executions in this window/i)).toBeInTheDocument()
    // KPI grid must not render — proves we never hit the dereference path.
    expect(screen.queryByText('Total tests')).not.toBeInTheDocument()
  })

  it('renders KPI tiles, per-suite table, and top failing rows on happy path', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    // Headline KPI labels render.
    expect(await screen.findByText('Total tests')).toBeInTheDocument()
    // "Pass %" appears twice (KPI label + table header) — both are real and
    // both must be present, so assert on the multiple match instead of
    // single-element retrieval.
    expect(screen.getAllByText('Pass %').length).toBeGreaterThanOrEqual(2)
    // Per-suite row labels appear in the suite table; ``checkout-api``
    // also appears in the "Top failing tests" Suite column so use
    // getAllByText. Two matches = one per section.
    expect(screen.getAllByText('checkout-api').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('auth-api')).toBeInTheDocument()
    // Top failing row:
    expect(screen.getByText('test_pay')).toBeInTheDocument()
  })

  it('renders each suite name as a link to /coverage/suite?name=...&days=...', async () => {
    // Window was bumped to 30d in localStorage previously; we want a
    // deterministic value so the href assertion is stable.
    useTimeWindowStore.setState({ days: 7 })
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    const link = await screen.findByRole('link', { name: 'auth-api' })
    expect(link).toHaveAttribute(
      'href',
      '/coverage/suite?name=auth-api&days=7',
    )
  })

  it('re-fetches when the user picks a different window or aggregation mode', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    const initialCalls = mockGet.mock.calls.length

    // Pick a window that is NOT the shared default — clicking the default is
    // a no-op, so it would assert nothing. Derived from the constant rather
    // than hard-coded: this previously clicked '30d' as "a different window",
    // and became vacuous the day 30d BECAME the default.
    const windowOptions = [1, 7, 30, 90]
    const nonDefault = windowOptions.find(d => d !== DEFAULT_TIME_WINDOW_DAYS) as number
    // The page labels 1 as "24h", not "1d" — mirror its own rule.
    fireEvent.click(screen.getByText(nonDefault === 1 ? '24h' : `${nonDefault}d`))

    await waitFor(() => {
      // Default mode is ``latest``; a window change must preserve whatever
      // mode is currently active rather than resetting it.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: nonDefault, mode: 'latest' as SummaryReportMode }),
      )
      expect(mockGet.mock.calls.length).toBeGreaterThan(initialCalls)
    })

    const beforeMode = mockGet.mock.calls.length
    // Switch FROM the default 'latest' TO 'window' to exercise the toggle.
    fireEvent.click(screen.getByRole('radio', { name: /All runs in window/i }))

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'window' as SummaryReportMode }),
      )
      expect(mockGet.mock.calls.length).toBeGreaterThan(beforeMode)
    })
  })

  it('triggers a PDF download when the export button is clicked', async () => {
    mockGet.mockResolvedValue(makeReport())
    mockDownloadPdf.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }))

    // Stub the URL / DOM bits the page touches to drive the browser download.
    const origCreateUrl = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()

    renderPage()

    // The export is enabled once the report has data.
    await chooseExport('Export PDF')

    await waitFor(() => {
      // Default window comes from the shared store
      // (``DEFAULT_TIME_WINDOW_DAYS``, 7d as of the v2 migration) and the
      // default aggregation mode is ``latest`` post-2026-05-18 (bug fix:
      // ``window`` mode produces run-count-scaled totals that read as
      // duplicates). Pin both so a future bump of either default doesn't
      // silently regress.
      expect(mockDownloadPdf).toHaveBeenCalledWith({
        project_id: 'p1',
        days: DEFAULT_TIME_WINDOW_DAYS,
        mode: 'latest',
      })
    })

    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevoke
  })
})

describe('SummaryReportPage — Export Excel (VIZ-607)', () => {
  it('downloads the workbook with exactly the scope the PDF uses, named .xlsx', async () => {
    mockGet.mockResolvedValue(makeReport())
    mockDownloadPdf.mockClear()
    mockDownloadXlsx.mockResolvedValue(new Blob(['PK']))
    const origCreateUrl = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toMatch(/\.xlsx$/)
    })

    renderPage()
    await chooseExport('Export Excel')

    await waitFor(() =>
      expect(mockDownloadXlsx).toHaveBeenCalledWith({ project_id: 'p1', days: DEFAULT_TIME_WINDOW_DAYS, mode: 'latest' }),
    )
    expect(mockDownloadPdf).not.toHaveBeenCalled()
    await waitFor(() => expect(click).toHaveBeenCalled())

    click.mockRestore()
    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevoke
  })
})

describe('SummaryReportPage — background export (VIZ-607)', () => {
  it('sends a large report to the background instead of downloading it', async () => {
    mockGet.mockResolvedValue(makeReport())
    mockDownloadPdf.mockClear()
    mockRequestExport.mockClear()
    mockRequestExport.mockResolvedValueOnce({
      delivery: 'background', estimated_tests: 900_000, dispatched: true, export: null,
    } as never)

    renderPage()
    await chooseExport('Export PDF')

    await waitFor(() => expect(mockRequestExport).toHaveBeenCalledTimes(1))
    expect(mockRequestExport.mock.calls[0][0]).toMatchObject({ project_id: 'p1', format: 'pdf', background: false })
    expect(mockDownloadPdf).not.toHaveBeenCalled()
    // The panel re-reads the reader's exports when one is queued.
    await waitFor(() => expect(mockListExports).toHaveBeenCalledWith('p1'))
  })

  it('"Export Excel in background" asks for the background even for a small report', async () => {
    // P3: the "In background" checkbox became one ⋯ item per format.
    mockGet.mockResolvedValue(makeReport())
    mockRequestExport.mockClear()

    renderPage()
    await chooseExport('Export Excel in background')

    await waitFor(() => expect(mockRequestExport).toHaveBeenCalledTimes(1))
    expect(mockRequestExport.mock.calls[0][0]).toMatchObject({ format: 'xlsx', background: true })
  })

  it('"Export PDF in background" too; the plain items do not', async () => {
    mockGet.mockResolvedValue(makeReport())
    mockRequestExport.mockClear()

    renderPage()
    await chooseExport('Export PDF in background')
    await waitFor(() => expect(mockRequestExport).toHaveBeenCalledTimes(1))
    expect(mockRequestExport.mock.calls[0][0]).toMatchObject({ format: 'pdf', background: true })
  })
})

// ── Release scope: the badge and the PDF ────────────────────────────────────
//
// The backend now scopes the WHOLE report (totals, suites, steps, PDF) to the
// selected release and says which releases it applied in `meta.scope.releases`.
// The old badge ("…across all releases") became false the moment that shipped,
// and the PDF request still had no release — so the screen and the exported
// sign-off document disagreed. These pin both.

const RELEASE_ID = '22222222-2222-4222-8222-222222222221'

function makeMeta(releases: EnvelopeMeta['scope']['releases'], overrides: Partial<EnvelopeMeta> = {}): EnvelopeMeta {
  return {
    schema_version: 2,
    scope: {
      projects: [{ id: 'p1', name: 'GoogleProject' }],
      releases,
      suites: [],
      window: { from: '2026-08-20', to: '2026-09-19', days: 30, timezone: 'UTC' },
    },
    totals: { matched_runs: 4, total_runs: 10, matched_executions: 200, total_executions: 500 },
    pass_rate_basis: 'unique_tests',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-09-19T10:42:07Z',
    as_of: '2026-09-19T10:42:07Z',
    ...overrides,
  }
}

describe('SummaryReportPage — release scope badge and PDF', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockDownloadPdf.mockReset()
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
  })

  it('no release: shows no release badge at all', async () => {
    mockGet.mockResolvedValue(makeReport({ meta: makeMeta([]) }))
    renderPage()
    expect(await screen.findByText('Total tests')).toBeInTheDocument()

    expect(screen.queryByTestId('summary-scope-badge')).not.toBeInTheDocument()
    expect(screen.queryByText('All releases')).not.toBeInTheDocument()
  })

  it('one release applied: names it and says the PDF matches', async () => {
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport({
      meta: makeMeta([{ id: RELEASE_ID, name: '2026.09', status: 'in_progress' }]),
    }))
    renderPage()

    const badge = await screen.findByTestId('summary-scope-badge')
    expect(badge).toHaveTextContent('Release: 2026.09')
    expect(badge.getAttribute('title')).toBe(
      'Filtered to release 2026.09. This report and its PDF export both cover only this release within the selected time window.',
    )
    // The false claim is gone.
    expect(screen.queryByText('All releases')).not.toBeInTheDocument()
  })

  it('prefers the server meta over client state (names what was APPLIED)', async () => {
    // Client asked for one release; the server says it applied a different
    // name — the badge reports the server.
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport({
      meta: makeMeta([{ id: RELEASE_ID, name: 'server-name', status: 'released' }]),
    }))
    renderPage()
    expect(await screen.findByTestId('summary-scope-badge')).toHaveTextContent('Release: server-name')
  })

  it('unattributed: names the sentinel release the server applied', async () => {
    useReleaseStore.getState().setActiveRelease('unattributed', 'p1')
    mockGet.mockResolvedValue(makeReport({
      meta: makeMeta([{ id: 'unattributed', name: 'Unattributed', status: 'unattributed' }]),
    }))
    renderPage()

    const badge = await screen.findByTestId('summary-scope-badge')
    expect(badge).toHaveTextContent('Release: Unattributed')
    expect(badge.getAttribute('title')).toContain('PDF export both cover only this release')
  })

  it('release selected but the server applied none: says "All releases"', async () => {
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport({ meta: makeMeta([]) }))
    renderPage()

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toBe(
      'Not filtered by the selected release. This report and its PDF export cover the selected time window across all releases.',
    )
    expect(screen.queryByTestId('summary-scope-badge')).not.toBeInTheDocument()
  })

  it('meta missing (older backend / cached payload): claims only a request, never a scope', async () => {
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport())
    renderPage()

    const badge = await screen.findByTestId('summary-scope-badge')
    expect(badge).toHaveTextContent('Release scope unconfirmed')
    expect(badge).not.toHaveTextContent(/Release:/)
    expect(screen.queryByText('All releases')).not.toBeInTheDocument()
  })

  it('meta missing and no release: shows nothing', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    expect(await screen.findByText('Total tests')).toBeInTheDocument()
    expect(screen.queryByTestId('summary-scope-badge')).not.toBeInTheDocument()
    expect(screen.queryByText('All releases')).not.toBeInTheDocument()
  })

  it('renders a hostile release name as literal text, never as markup', async () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b>'
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport({
      meta: makeMeta([{ id: RELEASE_ID, name: hostile, status: 'in_progress' }]),
    }))
    renderPage()

    const badge = await screen.findByTestId('summary-scope-badge')
    expect(badge).toHaveTextContent(`Release: ${hostile}`)
    expect(badge.querySelector('img')).toBeNull()
    expect(badge.querySelector('b')).toBeNull()
    expect(badge.getAttribute('title')).toContain(hostile)
  })

  it('exports the PDF with the same release the screen was requested with', async () => {
    useReleaseStore.getState().setActiveRelease(RELEASE_ID, 'p1')
    mockGet.mockResolvedValue(makeReport({
      meta: makeMeta([{ id: RELEASE_ID, name: '2026.09', status: 'in_progress' }]),
    }))
    mockDownloadPdf.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }))
    const origCreateUrl = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()

    renderPage()
    await chooseExport('Export PDF')

    await waitFor(() => expect(mockDownloadPdf).toHaveBeenCalled())
    const screenArgs = mockGet.mock.calls[mockGet.mock.calls.length - 1][0]
    expect(mockDownloadPdf.mock.calls[0][0]).toEqual({ ...screenArgs, project_id: 'p1' })
    expect(mockDownloadPdf.mock.calls[0][0].release_id).toBe(RELEASE_ID)

    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevoke
  })
})

describe('SummaryReportPage — VIZ-106 responsive edits and metric tokens', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('scrolls both tables inside their own card instead of clipping their columns', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    const tables = screen.getAllByRole('table')
    expect(tables).toHaveLength(2)
    for (const table of tables) {
      const wrapper = table.parentElement as HTMLElement
      expect(wrapper.className).toMatch(/\boverflow-x-auto\b/)
      expect(wrapper.className).not.toMatch(/\boverflow-hidden\b/)
    }
  })

  it('lets a keyboard reach each table’s sideways scroll: a named, focusable region with a visible focus ring (R2-6)', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    // axe `scrollable-region-focusable`: a scroller a keyboard cannot focus cannot be scrolled without a mouse.
    for (const [name, header] of [
      ['Per-suite breakdown table', 'Suite'],
      ['Top failing tests table', 'Test'],
    ] as const) {
      const region = screen.getByRole('region', { name })
      expect(region.tabIndex).toBe(0)
      expect(region.className).toMatch(/\boverflow-x-auto\b/)
      // The ring shows on keyboard focus only (no outline is suppressed without a replacement).
      expect(region.className.split(/\s+/)).toEqual(
        expect.arrayContaining(['focus-visible:ring-2', 'focus-visible:ring-[var(--color-accent)]']),
      )
      expect(within(region).getByRole('table')).toBeInTheDocument()
      expect(within(region).getAllByRole('columnheader')[0].textContent).toMatch(new RegExp(`^${header}`))
    }
  })

})

// ── UX redesign P3 (`02-design-spec.md` §2, §5 "Reports › Summary"): header
// (Views · ⋯ with every export) · one toolbar · ONE merged KPI row · results
// by suite with the per-suite table under it as the primary content. ────────
describe('SummaryReportPage — the page template (P3)', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    try { localStorage.removeItem('summary-report.mode') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('puts results by suite and the per-suite table first, under the KPI row', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('test_pay')
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    const primary = document.querySelector('[data-primary]') as HTMLElement
    // The suite chart (the stand-in or the real frame: the same heading) and the table under it.
    const chart = within(primary).getByRole('heading', { level: 2, name: 'Results by suite' })
    const table = within(primary).getByRole('region', { name: 'Per-suite breakdown table' })
    expect(follows(chart, table)).toBe(true)
    // Above it: the header, the toolbar, the KPI row. Below it: the top failing tests.
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const toolbar = document.querySelector('[data-summary-toolbar]') as HTMLElement
    const kpis = screen.getByRole('region', { name: 'Summary KPIs' })
    expect(follows(header, toolbar)).toBe(true)
    expect(follows(toolbar, kpis)).toBe(true)
    expect(follows(kpis, primary)).toBe(true)
    expect(follows(primary, screen.getByRole('heading', { name: /^Top failing tests/ }))).toBe(true)
    // Nothing tabbed or collapsed comes before it.
    for (const later of document.querySelectorAll('[role="tablist"], [data-disclosure]')) {
      expect(follows(primary, later)).toBe(true)
    }
  })

  it('merges the six tiles and the counts strip into ONE row of five, dropping no number', async () => {
    mockGet.mockResolvedValue(makeReport({
      totals: { ...makeReport().totals, pass_rate_basis_label: 'per unique test' },
    }))
    renderPage()
    const row = await screen.findByRole('region', { name: 'Summary KPIs' })
    const tiles = Array.from(row.querySelectorAll('[data-metric-card]'), (tile) => tile.textContent)
    expect(tiles).toEqual([
      'Total tests200197 evaluated · 3 skipped (1.5%)',
      'Pass %90.0%weighted 91.4%180 passed · per unique test',
      'Fail %7.5%15 failed',
      'Broken %1.0%2 broken',
      'Flaky31.5% of total',
    ])
    // The counts strip is gone.
    expect(screen.queryByText('Evaluated')).toBeNull()
  })

  it('keeps the counts strip\'s footer on screen: the run facts are the header\'s subtitle', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('Total tests')
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).getByText(
      `Runs in window: 4 · Avg / day: 0.57 · Avg duration: 12,345 ms · Latest run: ${new Date('2026-05-15T00:00:00+00:00').toLocaleString()}`,
    )).toBeInTheDocument()
    // And when the report was generated, at the toolbar's end.
    const toolbar = document.querySelector('[data-summary-toolbar]') as HTMLElement
    expect(toolbar).toHaveTextContent(`Generated ${new Date('2026-05-16T00:00:00+00:00').toLocaleString()}`)
  })

  it('no runs per day in latest mode (the server sends none): the subtitle leaves it out', async () => {
    mockGet.mockResolvedValue(makeReport({ mode: 'latest', runs_per_day: null }))
    renderPage()
    await screen.findByText('Total tests')
    const subtitle = (document.querySelector('[data-page-header]') as HTMLElement).querySelector('p') as HTMLElement
    expect(subtitle.textContent).toMatch(/^Runs in window: 4 · Avg duration: 12,345 ms · Latest run: /)
  })

  it('header: the help topic, Views, and every export in ⋯', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('Total tests')
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).getByRole('button', { name: 'Help: Summary Report' })).toHaveAttribute('data-help-topic', 'reports')
    expect(within(header).queryByRole('button', { name: /Export|Analysis report/ })).toBeNull()
    expect(within(header).queryByRole('checkbox')).toBeNull()
    fireEvent.click(within(header).getByRole('button', { name: 'More actions' }))
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(EXPORT_ITEMS)
  })

  it('no data: the report exports are disabled, the analysis reports are not', async () => {
    mockGet.mockResolvedValue(makeReport({
      totals: { ...makeReport().totals, total_test_cases: 0 },
      suites: [],
      top_failing_tests: [],
    }))
    renderPage()
    await screen.findByText(/No executions in this window/i)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    for (const name of EXPORT_ITEMS.slice(0, 4)) expect(screen.getByRole('menuitem', { name })).toBeDisabled()
    for (const name of EXPORT_ITEMS.slice(4)) expect(screen.getByRole('menuitem', { name })).toBeEnabled()
  })

  it('one toolbar: the global WindowPicker with this report\'s windows, then the aggregation', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await screen.findByText('Total tests')
    const toolbar = document.querySelector('[data-summary-toolbar]') as HTMLElement
    const picker = within(toolbar).getByRole('radiogroup', { name: 'Time window' })
    expect(within(picker).getAllByRole('radio').map((r) => r.textContent)).toEqual(['24h', '7d', '30d', '90d'])
    expect(within(toolbar).getByRole('radiogroup', { name: 'Aggregation mode' })).toBeInTheDocument()
    // What each aggregation counts is its tooltip (§2: explanatory text is never a paragraph).
    expect(within(toolbar).getByRole('radio', { name: 'Latest run per suite' })).toHaveAttribute('title', expect.stringMatching(/most recent run counts/))
  })
})

describe('SummaryReportPage — the catalogue sections (VIZ-408)', () => {
  beforeEach(() => {
    mockGet.mockReset()
    trendsCalls.length = 0
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    try { localStorage.removeItem('summary-report.mode') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  /** Different suite counts per Aggregation mode, as the server sends them. */
  function servePerMode() {
    mockGet.mockImplementation(async (params: { mode: SummaryReportMode }) =>
      params.mode === 'latest'
        ? makeReport({
            mode: 'latest',
            suites: makeReport().suites.map((row) => (row.suite_name === 'checkout-api' ? { ...row, passed: 60, failed: 21 } : row)),
          })
        : makeReport({ mode: 'window' }),
    )
  }

  /** The suite bars' data table: suite -> its cells. */
  const suiteRow = (suite: string) => {
    const frame = screen.getByRole('heading', { level: 2, name: 'Results by suite' }).closest('[data-chart-frame]') as HTMLElement
    if (!within(frame).queryByRole('table', { name: /data table/i })) {
      fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    }
    const table = within(frame).getByRole('table', { name: /data table/i })
    return Array.from(
      (within(table).getByRole('rowheader', { name: suite }).parentElement as HTMLElement).querySelectorAll('td'),
      (td) => td.textContent,
    ).join(' ')
  }

  const sectionIds = () =>
    Array.from(document.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))

  /** Wait for the section chunk: until it lands the page's stand-in carries the same heading. */
  const sectionsLoaded = () =>
    waitFor(() => expect(document.querySelector('[data-catalogue-section="summary-suites"]')).not.toBeNull(), { timeout: 10_000 })

  it('the suite bars above the per-suite table they summarise; no donut; the trend collapsed and not asked', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await sectionsLoaded()
    // P3: the suite bars alone. The donut is gone (the KPI row has its counts), the
    // trend is the closed disclosure's, and "Failures by test" was deleted (§5).
    expect(sectionIds()).toEqual(['summary-suites'])
    expect(screen.queryByRole('heading', { name: 'Status breakdown' })).toBeNull()
    const primary = document.querySelector('[data-primary]') as HTMLElement
    expect(within(primary).getByRole('heading', { level: 2, name: 'Results by suite' })).toBeInTheDocument()
    const perSuite = screen.getByRole('heading', { name: /Per-suite breakdown/ })
    const trend = screen.getByRole('button', { name: /^Trend/ })
    expect(trend).toHaveAttribute('aria-expanded', 'false')
    expect(follows(perSuite, trend)).toBe(true)
    expect(follows(trend, screen.getByRole('heading', { name: /^Top failing tests/ }))).toBe(true)
    // Closed: the trend's one request is not made.
    expect(trendsCalls).toEqual([])
  })

  it('the donut’s four counts are the KPI row’s, from the same totals', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    const row = await screen.findByRole('region', { name: 'Summary KPIs' })
    await sectionsLoaded()
    expect(row).toHaveTextContent('180 passed')
    expect(row).toHaveTextContent('15 failed')
    expect(row).toHaveTextContent('2 broken')
    expect(row).toHaveTextContent('3 skipped')
  })

  it('opening "Trend" mounts the trend, which asks on the page’s window; closing it unmounts it', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await sectionsLoaded()
    const toggle = screen.getByRole('button', { name: /^Trend/ })
    expect(toggle).toHaveTextContent(`pass rate per day · ${DEFAULT_TIME_WINDOW_DAYS}d`)
    fireEvent.click(toggle)
    await waitFor(() => expect(document.querySelector('[data-catalogue-section="summary-trend"]')).not.toBeNull(), {
      timeout: 10_000,
    })
    const disclosure = toggle.closest('[data-disclosure]') as HTMLElement
    expect(within(disclosure).getByRole('heading', { level: 2, name: 'Pass rate trend' })).toBeInTheDocument()
    expect(trendsCalls[trendsCalls.length - 1]).toBe(DEFAULT_TIME_WINDOW_DAYS)
    fireEvent.click(toggle)
    expect(document.querySelector('[data-catalogue-section="summary-trend"]')).toBeNull()
  })

  it('the suite bars follow the Aggregation toggle (the latest counts in latest mode, then the window’s)', async () => {
    servePerMode()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Results by suite' }, { timeout: 10_000 })
    await sectionsLoaded()
    await waitFor(() => expect(suiteRow('checkout-api')).toMatch(/^60 21 /))

    fireEvent.click(screen.getByRole('radio', { name: /All runs in window/i }))
    await waitFor(() => expect(suiteRow('checkout-api')).toMatch(/^102 13 /))
  })

  it('no failing-test chart at all (P3): the Top failing tests table carries every row of it', async () => {
    mockGet.mockResolvedValue(makeReport())
    renderPage()
    await sectionsLoaded()
    expect(document.querySelector('[data-catalogue-section="summary-top-failing"]')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Failures by test' })).toBeNull()
    const table = screen.getByRole('region', { name: 'Top failing tests table' })
    expect(within(table).getByRole('row', { name: /test_pay checkout-api CheckoutTests 8/ })).toBeInTheDocument()
  })

  it('a window with no failures says so in place of the table', async () => {
    mockGet.mockResolvedValue(makeReport({ top_failing_tests: [] }))
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Results by suite' }, { timeout: 10_000 })
    expect(screen.getByText('No failures in this window.')).toBeInTheDocument()
  })
})
