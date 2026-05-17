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
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

describe('SuitesPage', () => {
  beforeEach(() => {
    mockUseSuites.mockReset()
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
