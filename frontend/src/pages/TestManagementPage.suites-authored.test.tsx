/**
 * D-59 (product findings 2026-11-21): authored cases are counted apart from the
 * tests that ran. A Test Cases › Suites card reads "N tests · M authored cases";
 * the pass rate is the catalogue's, over the tests that ran only; a suite of
 * authored cases only reads "M authored cases · not run yet" with no passed /
 * failed counts, no pass rate and no last run.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { testManagementService } from '@/services/testManagementService'
import { useAuthStore } from '@/store/authStore'
import TestManagementPage from './TestManagementPage'

vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', activeProject: { id: 'project-1', name: 'Project One' } }
  return {
    ALL_PROJECTS_ID: '__ALL__',
    useProjectStore: (selector: (s: typeof state) => unknown) => selector(state),
  }
})
vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: () => false }))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useTestCases: () => ({ data: { items: [], total: 0, page: 1, size: 25, pages: 0 }, error: undefined, isLoading: false, mutate: vi.fn() }),
  useAuditLog: () => ({ data: { items: [], total: 0, page: 1, size: 5, pages: 0 } }),
  useTestPlans: () => ({ data: { items: [], total: 0, page: 1, size: 10, pages: 0 }, isLoading: false, mutate: vi.fn() }),
  useUsers: () => ({ data: [] }),
}))
vi.mock('@/hooks/useSuites', () => ({
  useSuites: () => ({
    data: {
      items: [
        { id: 'suite-1', project_id: 'project-1', name: 'Checkout', description: null, is_default: false, tags: null, test_case_count: 12, created_at: '2026-08-01T00:00:00Z', updated_at: null },
        { id: 'suite-7', project_id: 'project-1', name: 'Payments', description: null, is_default: false, tags: null, test_case_count: 3, created_at: '2026-10-20T00:00:00Z', updated_at: null },
        { id: 'suite-8', project_id: 'project-1', name: 'Search', description: null, is_default: false, tags: null, test_case_count: 7, created_at: '2026-10-20T00:00:00Z', updated_at: null },
      ],
      total: 3,
    },
  }),
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [] }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      listSuites: vi.fn(),
      listReviewsForRun: vi.fn(),
      getSuiteCases: vi.fn(async () => ({ items: [], total: 0, page: 1, size: 25, pages: 0 })),
      getSuiteDeleted: vi.fn(async () => []),
      getSuiteChanges: vi.fn(async () => []),
    },
  }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

const CHECKOUT = {
  suite_name: 'Checkout', test_count: 10, authored_count: 2, passed_count: 9, failed_count: 1,
  last_run_at: '2026-10-01T00:00:00Z', last_run_id: 'run-1', pass_rate: 90,
  owner_full_name: 'Priya Lead', owner_is_fallback: false,
}
const SEARCH = {
  suite_name: 'Search', test_count: 7, authored_count: 0, passed_count: 7, failed_count: 0,
  last_run_at: '2026-10-01T00:00:00Z', last_run_id: 'run-1', pass_rate: 100,
  owner_full_name: 'Priya Lead', owner_is_fallback: false,
}
const PAYMENTS = {
  suite_name: 'Payments', test_count: 0, authored_count: 3, passed_count: 0, failed_count: 0,
  last_run_at: null, last_run_id: null, pass_rate: null, run_count: 0, total_executions: 0,
  owner_full_name: 'Priya Lead', owner_is_fallback: false,
}

function renderAt(url: string) {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/test-management" element={<TestManagementPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const card = (name: string) => document.getElementById(`suite-card-${name}`) as HTMLElement
const counts = (name: string) => card(name).querySelector('[data-suite-counts]') as HTMLElement

beforeEach(() => {
  useAuthStore.setState({ user: { id: 'u-lead', role: 'QA_LEAD' } as never })
  Element.prototype.scrollIntoView = vi.fn()
  vi.mocked(testManagementService.listSuites).mockResolvedValue([CHECKOUT, SEARCH, PAYMENTS] as never)
  vi.mocked(testManagementService.listReviewsForRun).mockResolvedValue([])
})
afterEach(() => { useAuthStore.setState({ user: null }) })

describe('Test Cases › Suites: authored cases apart from run tests (D-59)', () => {
  it('a suite with both reads "N tests · M authored cases" and keeps the pass rate of the tests that ran', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('3 suites')
    expect(counts('Checkout')).toHaveTextContent('10 tests · 2 authored cases')
    expect(card('Checkout')).toHaveTextContent('9 passed')
    expect(card('Checkout')).toHaveTextContent('90.0% pass rate')
  })

  it('a suite without authored cases names none', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('3 suites')
    expect(counts('Search').textContent).toBe('7 tests')
  })

  it('a suite of authored cases only has not run: no passed / failed, no pass rate, no last run', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('3 suites')
    const payments = card('Payments')
    expect(counts('Payments')).toHaveTextContent('3 authored cases · not run yet')
    expect(payments).not.toHaveTextContent(/passed|failed|pass rate|Last run|0 tests/)
    expect(within(payments).queryByText(/AI verdict/)).toBeNull()
  })
})
