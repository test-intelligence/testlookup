/**
 * W13 TCL-06 (contract §2.7, G-D): requirement traceability on the page, only
 * with `test_case_requirement_traceability` ON (read ONCE by the page and
 * passed to the Cases tab, default off).
 *
 *  - off: no Requirements section, five Insights cards, the unchanged search
 *    placeholder (five pinned sites run with it off);
 *  - on: G-E's Requirements section ends an AUTHORED case's Details tab, the
 *    sixth Insights card (Requirement coverage), the requirement placeholder,
 *    and a key click filters the list (`requirement=`) with its removable chip.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { useCaseRequirements, useLibraryHealth } from '@/hooks/useTestLibrary'
import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { useAuthStore, type User } from '@/store/authStore'
import { LIBRARY_HEALTH_FIXTURE } from '@/test/testLibraryMocks'
import type { LibraryHealth, RequirementLinkList } from '@/types/test-library'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import TestManagementPage, { TestCasesTab } from './TestManagementPage'

vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', activeProject: { id: 'project-1', name: 'Project One' }, projects: [] }
  return {
    ALL_PROJECTS_ID: '__ALL__',
    useProjectStore: (selector: (s: typeof state) => unknown) => selector(state),
  }
})
vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: vi.fn(() => false) }))
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'medium',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', allowed_actions: ['activate'],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}
const AUTHORED = base({ id: 'case-1', title: 'Refund within 24h' })
const AUTOMATION = base({ id: 'auto-1', title: 'refund_spec.ts › refunds', source: 'automation' })

const HEALTH: LibraryHealth = {
  ...LIBRARY_HEALTH_FIXTURE,
  requirements: {
    measured: true, known: 15, verified: 12, partly_verified: 2, gaps: 3, truncated: false,
    top_gaps: [{ key: 'REQ-PAY-09', normalized_key: 'REQ-PAY-09', reason: 'no_link', sources: ['generation_batch'], linked_cases: 0, batch_claimed_cases: 3 }],
  },
}
const LINKS: RequirementLinkList = {
  total: 1,
  items: [{
    id: 'link-1', test_case_id: 'case-1', requirement_key: 'req-pay-07', normalized_key: 'REQ-PAY-07',
    requirement_title: 'Refund within 24h', link_type: 'verifies', created_by_name: 'QA', created_at: '2026-10-09T00:00:00Z',
  }],
}

const where = { url: '' }
function Where() {
  const location = useLocation()
  useEffect(() => { where.url = location.pathname + location.search }, [location])
  return null
}

function renderTab(requirementTraceability: boolean) {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Where />
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId="project-1" lifecycleV2 requirementTraceability={requirementTraceability} />} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLElement
const pane = (title: string) => screen.getByRole('complementary', { name: title })
const insights = () => {
  fireEvent.click(screen.getByRole('button', { name: /^Insights/ }))
  return screen.getByRole('complementary', { name: 'Insights' })
}
const lastParams = () => {
  const calls = vi.mocked(useTestCases).mock.calls
  return calls[calls.length - 1]?.[0] as Record<string, unknown>
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role: 'TESTER',
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
  vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
  const page: PaginatedResponse<ManagedTestCase> = { items: [AUTHORED, AUTOMATION], total: 2, page: 1, size: 25, pages: 1 }
  vi.mocked(useTestCases).mockImplementation(() => (
    { data: page, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn() } as unknown as ReturnType<typeof useTestCases>
  ))
  vi.mocked(useLibraryHealth).mockReturnValue(
    { data: HEALTH, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useLibraryHealth>,
  )
  vi.mocked(useCaseRequirements).mockReturnValue(
    { data: LINKS, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useCaseRequirements>,
  )
})
afterEach(() => { act(() => useAuthStore.setState({ user: null })) })

describe('/test-management: requirement traceability, flag OFF (W13 TCL-06)', () => {
  it('no Requirements section, five Insights cards, the unchanged placeholder', () => {
    renderTab(false)
    expect(screen.getByPlaceholderText('Search by title, tags or ID…')).toBeInTheDocument()
    fireEvent.click(row(AUTHORED.id))
    expect(document.querySelector('[data-case-requirements]')).toBeNull()
    expect(useCaseRequirements).not.toHaveBeenCalled()
    expect(within(insights()).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Library health', 'Review queue · 1', 'Strategy gaps', 'Recent activity', 'Automation coverage',
    ])
    expect(screen.queryByRole('heading', { name: 'Requirement coverage' })).toBeNull()
  })

  it('the page reads the flag once and passes it down: off by default', () => {
    render(<MemoryRouter initialEntries={['/test-management']}><TestManagementPage /></MemoryRouter>)
    expect(vi.mocked(useFeatureEnabled).mock.calls.map(([key]) => key)).toContain('test_case_requirement_traceability')
    expect(screen.getByPlaceholderText('Search by title, tags or ID…')).toBeInTheDocument()
  })
})

describe('/test-management: requirement traceability, flag ON (W13 TCL-06)', () => {
  it('the requirement placeholder; the page passes the flag on', () => {
    vi.mocked(useFeatureEnabled).mockImplementation((key: string) => key === 'test_case_requirement_traceability')
    render(<MemoryRouter initialEntries={['/test-management']}><TestManagementPage /></MemoryRouter>)
    expect(screen.getByPlaceholderText('Search by title, tags, ID or requirement…')).toBeInTheDocument()
  })

  it('an authored case ends its Details with the Requirements section; an automation row has none', () => {
    renderTab(true)
    fireEvent.click(row(AUTHORED.id))
    const section = pane(AUTHORED.title).querySelector('[data-case-requirements]') as HTMLElement
    expect(section).not.toBeNull()
    expect(section).toHaveTextContent('req-pay-07')
    expect(useCaseRequirements).toHaveBeenCalledWith(AUTHORED.id, true)
    fireEvent.click(row(AUTOMATION.id))
    expect(pane(AUTOMATION.title).querySelector('[data-case-requirements]')).toBeNull()
  })

  it('the sixth Insights card: Requirement coverage, both numbers', () => {
    renderTab(true)
    const panel = insights()
    expect(within(panel).getByRole('heading', { name: 'Requirement coverage' })).toBeInTheDocument()
    expect(panel).toHaveTextContent('12 of 15')
  })

  it('a key click filters the list by it; the chip removes the filter', async () => {
    renderTab(true)
    fireEvent.click(row(AUTHORED.id))
    const section = pane(AUTHORED.title).querySelector('[data-case-requirements]') as HTMLElement
    fireEvent.click(within(section).getByRole('button', { name: 'req-pay-07' }))
    await waitFor(() => expect(where.url).toContain('requirement=REQ-PAY-07'))
    expect(lastParams()).toMatchObject({ requirement: 'REQ-PAY-07' })
    fireEvent.click(screen.getByRole('button', { name: 'Remove the requirement filter REQ-PAY-07' }))
    await waitFor(() => expect(where.url).not.toContain('requirement='))
    expect(lastParams()).not.toHaveProperty('requirement')
  })
})
