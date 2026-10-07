/**
 * Tests for SuitesPage — the page that powers /suites.
 *
 * Regression context: the page rendered "No suites yet" for every project
 * even when test_runs were ingested, because the live-stream persistence
 * path was skipping finalize_run (test_suites stayed empty in Postgres).
 * These tests pin the page's rendering contract on the data shape the
 * /api/v1/suites endpoint returns so a UI regression on top of a working
 * backend fix doesn't sneak in.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import SuitesPage from './SuitesPage'
import type { TestSuite, TestSuiteListResponse } from '@/types/suites'

// ── Hooks/stores we don't want to exercise in a unit test ────────────────────

const mockUseSuites = vi.fn()
vi.mock('@/hooks/useSuites', () => ({
  useSuites: () => mockUseSuites(),
  refreshSuites: vi.fn(),
}))

// Mutable store state — each test can override via ``setStore`` before
// rendering. Keeps the existing single-project tests working (defaults
// match the original fixed mock) while letting the new All-Projects
// tests flip the state without re-mocking the module.
const mockStoreState = {
  activeProject: { id: 'p1', name: 'GoogleSearch' } as { id: string; name: string } | null,
  activeProjectId: 'p1' as string,
  projects: [{ id: 'p1', name: 'GoogleSearch' }] as Array<{ id: string; name: string }>,
}
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (s: typeof mockStoreState) => unknown) =>
    selector(mockStoreState),
}))

// The run columns' read (UX redesign P4): per-suite-name aggregates from
// `/api/v1/test-management/suites`. Scripted per test; `enabled` recorded.
const mockUseSuiteAggregates = vi.fn()
vi.mock('./suite/useSuiteAggregates', () => ({
  useSuiteAggregates: (enabled: boolean) => mockUseSuiteAggregates(enabled),
}))

vi.mock('@/services/suitesService', () => ({
  suitesService: {
    create: vi.fn(),
    setDefault: vi.fn(),
    remove: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({
    canAccessManagement: true,
    hasRole: () => true,
  }),
}))

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeSuite(overrides: Partial<TestSuite> = {}): TestSuite {
  return {
    id: 'suite-' + Math.random().toString(36).slice(2, 8),
    project_id: 'p1',
    name: 'default-suite',
    description: null,
    is_default: false,
    tags: null,
    test_case_count: 0,
    created_at: '2026-05-13T00:00:00Z',
    updated_at: '2026-05-13T00:00:00Z',
    ...overrides,
  }
}

function renderPage(state: ReturnType<typeof mockUseSuites>) {
  mockUseSuites.mockReturnValue(state)
  return render(
    <MemoryRouter>
      <SuitesPage />
    </MemoryRouter>,
  )
}

function aggregates(rows: Array<Record<string, unknown>> = [], error: unknown = undefined) {
  const byName = new Map(rows.map((row) => [row.suite_name as string, row]))
  return { byName, error, isLoading: false, loaded: true }
}

describe('SuitesPage', () => {
  beforeEach(() => {
    mockUseSuites.mockReset()
    mockUseSuiteAggregates.mockReset()
    mockUseSuiteAggregates.mockReturnValue(aggregates())
    // Reset store state to single-project default so each test starts
    // from the same baseline.
    mockStoreState.activeProject = { id: 'p1', name: 'GoogleSearch' }
    mockStoreState.activeProjectId = 'p1'
    mockStoreState.projects = [{ id: 'p1', name: 'GoogleSearch' }]
  })

  it('renders a loading state while suites are being fetched', () => {
    renderPage({ data: undefined, isLoading: true, error: undefined })
    // LoadingSpinner uses role="status" via @testing-library/jest-dom defaults.
    expect(document.querySelector('.animate-spin, [role="status"]')).toBeTruthy()
  })

  it('renders the empty state when the API returns zero suites', () => {
    const empty: TestSuiteListResponse = { items: [], total: 0 }
    renderPage({ data: empty, isLoading: false, error: undefined })

    expect(screen.getByText(/no suites yet/i)).toBeInTheDocument()
  })

  it('renders one row per suite with the test_case_count from the API', () => {
    const items: TestSuiteListResponse = {
      items: [
        makeSuite({ name: 'auth-api', test_case_count: 10 }),
        makeSuite({ name: 'orders-ui', test_case_count: 10 }),
        makeSuite({
          name: 'All Tests',
          is_default: true,
          test_case_count: 4,
        }),
      ],
      total: 3,
    }
    renderPage({ data: items, isLoading: false, error: undefined })

    expect(screen.getByText('auth-api')).toBeInTheDocument()
    expect(screen.getByText('orders-ui')).toBeInTheDocument()
    expect(screen.getByText('All Tests')).toBeInTheDocument()
    // test_case_count is rendered for every row, so 10 appears twice and 4 once.
    expect(screen.getAllByText('10')).toHaveLength(2)
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('renders the error state when the API rejects', () => {
    renderPage({
      data: undefined,
      isLoading: false,
      error: new Error('boom'),
    })
    expect(screen.getByText(/failed to load suites/i)).toBeInTheDocument()
  })

  // ── All-Projects mode (Phase I last follow-up) ─────────────────────────

  it('hides the New-suite button in All-Projects mode when user has no accessible projects', () => {
    mockStoreState.activeProject = null
    mockStoreState.activeProjectId = 'all'
    mockStoreState.projects = []
    renderPage({ data: { items: [], total: 0 }, isLoading: false, error: undefined })

    // No projects + All-Projects mode → button must NOT render. Otherwise
    // we'd offer a create flow that immediately fails with "no project".
    expect(screen.queryByRole('button', { name: /New suite/i })).toBeNull()
  })

  it('shows the New-suite button in All-Projects mode when the user has projects', () => {
    mockStoreState.activeProject = null
    mockStoreState.activeProjectId = 'all'
    mockStoreState.projects = [
      { id: 'p1', name: 'GoogleSearch' },
      { id: 'p2', name: 'Checkout' },
    ]
    renderPage({ data: { items: [], total: 0 }, isLoading: false, error: undefined })

    expect(screen.getByRole('button', { name: /New suite/i })).toBeInTheDocument()
  })

  it('All-Projects mode: create dialog requires a project pick before submit', () => {
    mockStoreState.activeProject = null
    mockStoreState.activeProjectId = 'all'
    mockStoreState.projects = [
      { id: 'p1', name: 'GoogleSearch' },
      { id: 'p2', name: 'Checkout' },
    ]
    renderPage({ data: { items: [], total: 0 }, isLoading: false, error: undefined })

    fireEvent.click(screen.getByRole('button', { name: /New suite/i }))

    // The project dropdown is rendered with a "Select a project…" prompt
    // option, and both member projects show up.
    expect(screen.getByText('Select a project…')).toBeInTheDocument()
    expect(screen.getAllByText('GoogleSearch').length).toBeGreaterThan(0)
    expect(screen.getByText('Checkout')).toBeInTheDocument()

    // Even with a non-empty name, the Create button stays disabled until
    // a project is picked. Otherwise we'd be sending project_id="" to
    // the backend and getting an opaque 422.
    fireEvent.change(screen.getByLabelText(/Name/i), { target: { value: 'Smoke' } })
    const submit = screen.getByRole('button', { name: /Create suite/i })
    expect(submit).toBeDisabled()
  })

  it('All-Projects mode: picked project_id is forwarded to the create payload', async () => {
    mockStoreState.activeProject = null
    mockStoreState.activeProjectId = 'all'
    mockStoreState.projects = [
      { id: 'p1', name: 'GoogleSearch' },
      { id: 'p2', name: 'Checkout' },
    ]
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.create as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 's-new', name: 'Smoke', project_id: 'p2',
    })

    renderPage({ data: { items: [], total: 0 }, isLoading: false, error: undefined })

    fireEvent.click(screen.getByRole('button', { name: /New suite/i }))
    fireEvent.change(screen.getByLabelText(/Project/i), { target: { value: 'p2' } })
    fireEvent.change(screen.getByLabelText(/Name/i), { target: { value: 'Smoke' } })
    fireEvent.click(screen.getByRole('button', { name: /Create suite/i }))

    await waitFor(() => {
      expect(suitesService.create).toHaveBeenCalledWith(
        expect.objectContaining({ project_id: 'p2', name: 'Smoke' }),
      )
    })
  })
})

// ── UX redesign P4: the list gains the suites' run columns ─────────────────

describe('SuitesPage — run columns (P4)', () => {
  const TWO_HOURS_AGO = () => new Date(Date.now() - 2 * 3_600_000).toISOString()

  function auth(overrides: Record<string, unknown> = {}) {
    return {
      suite_name: 'auth-api',
      test_count: 4,
      passed_count: 3,
      failed_count: 1,
      last_run_at: TWO_HOURS_AGO(),
      last_run_id: 'run-42',
      pass_rate: 75,
      run_count: 12,
      total_executions: 480,
      owner_full_name: 'Dana Lead',
      owner_email: 'dana@example.test',
      owner_is_fallback: false,
      ...overrides,
    }
  }

  const SUITES: TestSuiteListResponse = {
    items: [
      makeSuite({ id: 's-auth', name: 'auth-api', test_case_count: 4 }),
      makeSuite({ id: 's-all', name: 'All Tests', is_default: true, test_case_count: 9 }),
    ],
    total: 2,
  }

  beforeEach(() => {
    mockUseSuites.mockReset()
    mockUseSuiteAggregates.mockReset()
    mockStoreState.activeProject = { id: 'p1', name: 'GoogleSearch' }
    mockStoreState.activeProjectId = 'p1'
    mockStoreState.projects = [{ id: 'p1', name: 'GoogleSearch' }]
  })

  const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement
  const cell = (row: HTMLElement, col: string) => row.querySelector(`[data-col="${col}"]`) as HTMLElement

  it('one project: Pass rate, Last run, Executions, Failing and Owner, after Test cases', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth()]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    expect(mockUseSuiteAggregates).toHaveBeenCalledWith(true)
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Name', 'Description', 'Test cases', 'Pass rate', 'Last run', 'Executions', 'Failing', 'Owner', ''])
    const row = rowOf('auth-api')
    expect(cell(row, 'pass-rate')).toHaveTextContent('75.0%')
    expect(cell(row, 'executions')).toHaveTextContent('480')
    expect(cell(row, 'failing')).toHaveTextContent('1')
    expect(cell(row, 'owner')).toHaveTextContent('Dana Lead')
    expect(cell(row, 'owner')).not.toHaveTextContent('(project default)')
    const lastRun = within(cell(row, 'last-run')).getByRole('link', { name: '2h ago' })
    expect(lastRun).toHaveAttribute('href', '/runs/run-42')
  })

  it('the table is the primary content, under a compact header with the help topic', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth()]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    const primaries = document.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    expect(within(primaries[0] as HTMLElement).getByRole('table')).toBeInTheDocument()
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Test Suites' })).toHaveAttribute('data-help-topic', 'concepts')
  })

  it('a suite no run reports by name (the default catch-all): every run cell a dash, never 0 %', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth()]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    const row = rowOf('All Tests')
    // Pass rate, last run, executions, failing: "no run"; owner: none resolved.
    const runDashes = within(row).getAllByTitle('No run has reported this suite')
    expect(runDashes).toHaveLength(4)
    for (const dash of runDashes) expect(dash).toHaveTextContent('—')
    expect(within(row).getByTitle('No owner resolved')).toHaveTextContent('—')
    expect(within(row).queryByText(/%$/)).toBeNull()
    expect(within(row).queryByText('0')).toBeNull()
  })

  it("a suite with no owner of its own shows the project's default lead, marked as such", () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth({ owner_full_name: null, owner_email: 'lead@example.test', owner_is_fallback: true })]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    const owner = cell(rowOf('auth-api'), 'owner')
    expect(owner).toHaveTextContent('lead@example.test (project default)')
    expect(owner.querySelector('[title]')).toHaveAttribute('title', "No owner set on this suite: the project's default QA lead")
  })

  it('no test has a latest result yet: no pass rate (a dash), and zero failing', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth({ pass_rate: null, failed_count: 0, last_run_at: null, last_run_id: null })]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    const row = rowOf('auth-api')
    expect(cell(row, 'pass-rate')).toHaveTextContent('—')
    expect(cell(row, 'last-run')).toHaveTextContent('—')
    expect(cell(row, 'failing')).toHaveTextContent('0')
  })

  it('the aggregates read failed: the page says the run columns are empty because of it', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([], new Error('boom')))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    expect(document.querySelector('[data-aggregates-note]')).toHaveTextContent(/Run columns could not be loaded/)
  })

  it('All-Projects: no run columns (a name could join the wrong project), nothing asked, and why', () => {
    mockStoreState.activeProject = null
    mockStoreState.activeProjectId = 'all'
    mockUseSuiteAggregates.mockReturnValue(aggregates())
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    expect(mockUseSuiteAggregates).toHaveBeenCalledWith(false)
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Name', 'Description', 'Test cases', ''])
    expect(document.querySelector('[data-aggregates-note]')).toHaveTextContent(
      "Select a project to see each suite's pass rate, last run, executions, failing tests and owner.",
    )
  })

  it('no link on the page points at the retired /coverage/suite', () => {
    mockUseSuiteAggregates.mockReturnValue(aggregates([auth()]))
    renderPage({ data: SUITES, isLoading: false, error: undefined })
    const hrefs = Array.from(document.querySelectorAll('a'), (a) => a.getAttribute('href') ?? '')
    expect(hrefs.filter((href) => href.includes('/coverage/suite'))).toEqual([])
  })
})
