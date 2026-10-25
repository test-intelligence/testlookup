/**
 * W13 TCL-03 (contract §2.3 frontend 9): the Cases tab's health banner and the
 * Insights read the SERVER's library health (`useLibraryHealth`), computed
 * over the whole library; the page's 200-row `useTestCases` roll and its
 * synthesis are gone.
 *
 *  - the banner's verdict and four facts come from the answer;
 *  - All Projects asks nothing (`useLibraryHealth(null)`) and says why;
 *  - loading / failed: pending, no facts, every Insights card keeps its
 *    heading with a spinner or "Could not load library health." — never zeros;
 *  - Insights keeps five cards; `Review queue · {total}`; a queue row opens its
 *    case through `?case=` (fetched when not on the page);
 *  - the server's Strategy gaps, in order, an unmeasured list saying why;
 *  - `handleRefresh` revalidates the cases read AND the health read.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLibraryHealth } from '@/hooks/useTestLibrary'
import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import { useAuthStore, type User } from '@/store/authStore'
import { LIBRARY_HEALTH_FIXTURE } from '@/test/testLibraryMocks'
import type { LibraryHealth } from '@/types/test-library'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-09-20T00:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-09-20T00:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: { ...actual.testManagementService, getCase: vi.fn(), transitionCase: vi.fn() },
  }
})

const ACTIVE: ManagedTestCase = {
  id: 'case-1', project_id: 'project-1', title: 'Checkout preserves cart', test_type: 'functional', priority: 'high',
  severity: 'major', test_suite_id: null, status: 'active', version: 1, is_automated: true, automation_status: 'automated',
  ai_generated: false, source: 'managed', allowed_actions: ['deprecate'],
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
}

const where = { url: '' }
function Where() {
  const location = useLocation()
  useEffect(() => { where.url = location.pathname + location.search }, [location])
  return null
}

function renderTab(projectId: string | null = 'project-1') {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Where />
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2 />} />
      </Routes>
    </MemoryRouter>,
  )
}

const mutateCases = vi.fn()
const mutateHealth = vi.fn()

function answer(health: LibraryHealth | undefined, error?: unknown) {
  vi.mocked(useLibraryHealth).mockReturnValue(
    { data: health, error, isLoading: false, isValidating: false, mutate: mutateHealth } as unknown as ReturnType<typeof useLibraryHealth>,
  )
}

const banner = () => document.querySelector('[data-status-banner]') as HTMLElement
const facts = () => Array.from(banner().querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
const openInsights = () => {
  fireEvent.click(screen.getByRole('button', { name: /^Insights/ }))
  return screen.getByRole('complementary', { name: 'Insights' })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  const user: User = {
    id: 'u-1', email: 'lead@example.test', username: 'lead', full_name: 'Lead', role: 'QA_LEAD',
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
  vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
  const page: PaginatedResponse<ManagedTestCase> = { items: [ACTIVE], total: 1, page: 1, size: 25, pages: 1 }
  vi.mocked(useTestCases).mockImplementation(() => (
    { data: page, error: undefined, isLoading: false, isValidating: false, mutate: mutateCases } as unknown as ReturnType<typeof useTestCases>
  ))
  answer(LIBRARY_HEALTH_FIXTURE)
})
afterEach(() => { act(() => useAuthStore.setState({ user: null })) })

describe('/test-management Cases: the library health is the server\'s (W13 TCL-03)', () => {
  it('the banner reads the verdict and its four facts from the answer', () => {
    renderTab()
    expect(useLibraryHealth).toHaveBeenCalledWith('project-1')
    expect(banner()).toHaveTextContent('Library health 70/100')
    expect((banner().querySelector('[data-banner-pill]') as HTMLElement).textContent).toBe('NEEDS ATTENTION')
    expect(facts()).toEqual(['Cases 3', 'Awaiting review 1', 'Automated 33%', 'Stale drafts 0'])
    expect(screen.queryByTestId('library-health-stat-basis')).toBeNull()
  })

  it('holds no 200-row roll: the only cases read is the table\'s page', () => {
    renderTab()
    const sizes = vi.mocked(useTestCases).mock.calls.map(([params]) => params?.size)
    expect(sizes.length).toBeGreaterThan(0)
    expect(sizes.every((size) => size === 25)).toBe(true)
  })

  it('All Projects asks nothing and says why', () => {
    answer(undefined)
    renderTab(null)
    expect(useLibraryHealth).toHaveBeenCalledWith(null)
    expect(banner()).toHaveTextContent('Choose a project: library health is per project.')
    expect(facts()).toEqual([])
  })

  it('a failed read: pending, no facts, and every card says so under its heading (never zeros)', () => {
    answer(undefined, new Error('down'))
    renderTab()
    expect(banner()).toHaveTextContent('Could not load library health.')
    expect(facts()).toEqual([])
    const insights = openInsights()
    for (const heading of ['Library health', 'Review queue', 'Strategy gaps', 'Automation coverage']) {
      expect(within(insights).getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(within(insights).getAllByText('Could not load library health.')).toHaveLength(4)
    expect(insights).not.toHaveTextContent(/Review queue · 0/)
    // The table's own alert is about the cases read only.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('Insights keeps five cards, read from the answer', () => {
    renderTab()
    const insights = openInsights()
    expect(within(insights).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Library health', 'Review queue · 1', 'Strategy gaps', 'Recent activity', 'Automation coverage',
    ])
    expect(insights).toHaveTextContent('3 cases · 1 awaiting review, 0 stale drafts over 30 days, 0 deprecated still in active plans.')
    expect(insights).toHaveTextContent('target 60%')
    const lines = Array.from(insights.querySelectorAll('[data-health-component]')).map((l) => l.textContent)
    expect(lines).toEqual([
      'Automated 33 · 1 of 3 cases automated (target 60 %)',
      'Stale drafts 100 · 0 of 3 cases are drafts untouched for 30 days',
      'Review freshness 64 · the oldest of 1 review has waited 5 days (target under 14)',
      'Retired cases in open plans 100 · 0 of 3 cases are retired and still in an open plan',
    ])
    expect(within(insights).getByText(/Library refreshed/)).toBeInTheDocument()
    expect(insights).toHaveTextContent('1 automated (33%)')
  })

  it('the server\'s Strategy gaps in its order; an unmeasured list says why instead of a count', () => {
    renderTab()
    const insights = openInsights()
    const gaps = Array.from(insights.querySelectorAll('[data-strategy-gap]')).map((g) => g.getAttribute('data-strategy-gap'))
    expect(gaps).toEqual(['automation_below_target', 'flaky_active'])
    const below = insights.querySelector('[data-strategy-gap="automation_below_target"]') as HTMLElement
    expect(below).toHaveTextContent('Automation below target')
    expect(below).toHaveTextContent('33 % automated · target 60 %')
    expect(below).toHaveTextContent('27 % gap')
    const flaky = insights.querySelector('[data-strategy-gap="flaky_active"]') as HTMLElement
    expect(flaky).toHaveTextContent('Flaky and active')
    expect(flaky).toHaveTextContent('No flaky scores have been computed for this project yet.')
    expect(flaky).not.toHaveTextContent(/\b0\b/)
  })

  it('a review-queue row opens its case through ?case= (fetched: it is not on this page)', async () => {
    vi.mocked(testManagementService.getCase).mockResolvedValue({ ...ACTIVE, id: 'case-2', title: 'Sign in with SSO' })
    renderTab()
    const insights = openInsights()
    fireEvent.click(within(insights).getByRole('button', { name: /Sign in with SSO/ }))
    await waitFor(() => expect(where.url).toBe('/test-management?case=case-2'))
    await waitFor(() => expect(testManagementService.getCase).toHaveBeenCalledWith('case-2'))
  })

  it('a queue longer than its five rows links to the Approvals tab', () => {
    answer({ ...LIBRARY_HEALTH_FIXTURE, review_queue: { ...LIBRARY_HEALTH_FIXTURE.review_queue, total: 12 } })
    renderTab()
    const insights = openInsights()
    expect(within(insights).getByRole('heading', { name: 'Review queue · 12' })).toBeInTheDocument()
    expect(within(insights).getByRole('link', { name: 'Open the Approvals tab' })).toHaveAttribute('href', '/test-management?tab=approvals')
  })

  it('handleRefresh revalidates the cases read AND the health read (after a deprecation)', async () => {
    vi.mocked(testManagementService.transitionCase).mockResolvedValue({ ...ACTIVE, status: 'deprecated', version: 2 })
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Deprecate Checkout preserves cart' }))
    const form = screen.getByRole('region', { name: 'Deprecate test case' })
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: 'Superseded by checkout v2' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Deprecate' }))
    await waitFor(() => expect(mutateCases).toHaveBeenCalled())
    expect(mutateHealth).toHaveBeenCalled()
  })

  it('an unmeasured library: pending "No authored test cases yet", and the card says why', () => {
    answer({ ...LIBRARY_HEALTH_FIXTURE, measured: false, score: null, tag: null, total_cases: 0, automated_cases: 0 })
    renderTab()
    expect(banner()).toHaveTextContent('No authored test cases yet')
    expect(facts()).toEqual([])
    expect(openInsights()).toHaveTextContent(/Library health summarises the authored test-case catalog/)
  })
})
