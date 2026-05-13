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
import { render, screen } from '@testing-library/react'
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

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeProject: { id: 'p1', name: 'GoogleSearch' },
      activeProjectId: 'p1',
    }),
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
})
