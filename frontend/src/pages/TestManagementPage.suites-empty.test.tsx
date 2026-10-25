/**
 * W13 TCL-07: a first-class suite no result reports yet is listed on the
 * Test Cases › Suites tab at once, as an `empty` row of
 * `GET /test-management/suites` (one project, no release filter).
 *
 * Its card: the name linking to `/suites/<suite_id>`, an **Empty** badge, the
 * line "Waiting for its first run: no result reports this suite yet.", the
 * owner line as on every card (with **change** for a QA lead) — and no counts,
 * no pass rate, no AI-verdict row, no expand (nothing to list).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
        { id: 'suite-1', project_id: 'project-1', name: 'Checkout', description: null, is_default: false, tags: null, test_case_count: 4, created_at: '2026-08-01T00:00:00Z', updated_at: null },
        { id: 'suite-9', project_id: 'project-1', name: 'Checkout API', description: null, is_default: false, tags: null, test_case_count: 0, created_at: '2026-10-20T00:00:00Z', updated_at: null },
      ],
      total: 2,
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

const AGGREGATED = {
  suite_name: 'Checkout', test_count: 4, passed_count: 3, failed_count: 1,
  last_run_at: '2026-10-01T00:00:00Z', last_run_id: 'run-1', pass_rate: 75,
  owner_full_name: 'Priya Lead', owner_is_fallback: false,
}
const EMPTY = {
  suite_name: 'Checkout API', test_count: 0, passed_count: 0, failed_count: 0,
  last_run_at: null, last_run_id: null, pass_rate: null, run_count: 0, total_executions: 0,
  owner_user_id: 'u-lead', owner_full_name: 'Priya Lead', owner_is_fallback: false,
  empty: true, suite_id: 'suite-9',
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

beforeEach(() => {
  useAuthStore.setState({ user: { id: 'u-lead', role: 'QA_LEAD' } as never })
  // The deep link scrolls its card into view (jsdom has no layout).
  Element.prototype.scrollIntoView = vi.fn()
  vi.mocked(testManagementService.listSuites).mockResolvedValue([AGGREGATED, EMPTY] as never)
  vi.mocked(testManagementService.listReviewsForRun).mockResolvedValue([])
  vi.mocked(testManagementService.getSuiteCases).mockClear()
})
afterEach(() => { useAuthStore.setState({ user: null }) })

describe('Test Cases › Suites: a suite before its first run (TCL-07)', () => {
  it('lists the empty suite with its badge, the waiting line and its owner, and no counts', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('2 suites')
    const empty = card('Checkout API')
    expect(empty).toHaveAttribute('data-suite-empty', 'true')
    expect(within(empty).getByRole('link', { name: 'Checkout API' })).toHaveAttribute('href', '/suites/suite-9')
    expect(within(empty).getByText('Empty')).toBeInTheDocument()
    expect(within(empty).getByText('Waiting for its first run: no result reports this suite yet.')).toBeInTheDocument()
    expect(within(empty).getByText('Priya Lead')).toBeInTheDocument()
    expect(within(empty).getByRole('button', { name: 'change' })).toBeInTheDocument()
    // No counts, no pass rate, no AI verdict, no review badge, no trend link.
    expect(empty).not.toHaveTextContent(/passed|failed|pass rate|tests|executions/)
    expect(within(empty).queryByText(/AI verdict/)).toBeNull()
    expect(within(empty).queryByText('Pending review')).toBeNull()
    expect(within(empty).queryByRole('link', { name: 'Trend →' })).toBeNull()
  })

  it('an aggregated card is unchanged beside it: counts, review badge, no Empty badge', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('2 suites')
    const aggregated = card('Checkout')
    expect(aggregated).not.toHaveAttribute('data-suite-empty')
    expect(aggregated).toHaveTextContent('3 passed')
    expect(aggregated).toHaveTextContent('75.0% pass rate')
    expect(within(aggregated).queryByText('Empty')).toBeNull()
    expect(within(aggregated).getByText('Pending review')).toBeInTheDocument()
  })

  it('the empty card does not expand: a click asks for no cases', async () => {
    renderAt('/test-management?tab=suites')
    await screen.findByText('2 suites')
    fireEvent.click(within(card('Checkout API')).getByText('Waiting for its first run: no result reports this suite yet.'))
    await new Promise((r) => setTimeout(r, 0))
    expect(testManagementService.getSuiteCases).not.toHaveBeenCalled()
    expect(card('Checkout API').querySelector('table')).toBeNull()
    // The aggregated card still expands.
    fireEvent.click(within(card('Checkout')).getByText('3 passed'))
    await waitFor(() => expect(testManagementService.getSuiteCases).toHaveBeenCalledTimes(1))
    expect(vi.mocked(testManagementService.getSuiteCases).mock.calls[0][0]).toBe('Checkout')
  })

  it('a deep link to the empty suite loads nothing', async () => {
    renderAt('/test-management?tab=suites&suite=Checkout%20API')
    await screen.findByText('2 suites')
    await new Promise((r) => setTimeout(r, 0))
    expect(testManagementService.getSuiteCases).not.toHaveBeenCalled()
  })
})
