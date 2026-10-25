/**
 * W28 (OR-04, contract §2.1 frontend 11 + the AMENDMENT): /test-management's
 * Cases tab offers each role exactly what the server lets it do.
 *
 *  - VIEWER: reads everything, changes nothing — no New test case, no AI
 *    Generate, no selection checkboxes, no bulk hint, no Edit, no Status
 *    moves, no comment box (`Viewers can read comments.`), no Run AI Review
 *    (`AI review needs the Tester role or above.`), no legacy Request Review,
 *    no row trash.
 *  - TESTER and QA_ENGINEER: create, edit, the Status moves, bulk, comments,
 *    AI review — but NOT Deprecate / Archive / Reinstate: those stay QA_LEAD
 *    or above (the AMENDMENT), so neither sees a row trash nor a retire move.
 *  - QA_LEAD and ADMIN: all of it, the retire moves and the trash included.
 *  - All Projects is read-only for every role.
 *
 * The server is the authority (its own role floors, G-A's regression file);
 * this pins that the page never offers a control the role cannot use.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAuditLog,
  useTestCaseComments,
  useTestCaseHistory,
  useTestCaseReviews,
  useTestCases,
} from '@/hooks/useTestManagement'
import { useAuthStore, type User } from '@/store/authStore'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

// W13 (deliberate set-up): the page's new reads (library health, the owner list, saved views) stubbed idle.
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
  useTestCaseHistory: vi.fn(),
  useTestCaseReviews: vi.fn(),
  useTestCaseComments: vi.fn(),
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [] }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

type Role = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'
const ROLES: Role[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
const WRITERS: Role[] = ['TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
const LEADS: Role[] = ['QA_LEAD', 'ADMIN']

function signIn(role: Role) {
  const user: User = {
    id: `u-${role.toLowerCase()}`, email: `${role.toLowerCase()}@example.test`, username: role.toLowerCase(),
    full_name: role, role, is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', author_id: 'u-author',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

// What the server would answer FOR A LEAD: the page still gates the retire moves by role.
const DRAFT = base({
  id: 'c-1', title: 'Verify refund email',
  allowed_actions: ['activate', 'request_review', 'deprecate'], reason_required_actions: ['deprecate'],
})
const AUTOMATION = base({ id: 'auto-1', title: 'checkout_spec.ts › pays', source: 'automation', allowed_actions: [] })

function renderTab({ lifecycleV2 = true, projectId = 'project-1' as string | null } = {}) {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2={lifecycleV2} />} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLTableRowElement
const pane = () => screen.getByRole('complementary', { name: DRAFT.title })
const openCase = () => fireEvent.click(row(DRAFT.id))
const tab = (name: string) => fireEvent.click(within(pane()).getByRole('button', { name }))
const footer = () => document.querySelector('[data-cases-footer-count]') as HTMLElement

describe('/test-management: each role sees exactly what it can do (W28)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    const page: PaginatedResponse<ManagedTestCase> = { items: [DRAFT, AUTOMATION], total: 2, page: 1, size: 25, pages: 1 }
    vi.mocked(useTestCases).mockImplementation(() => ({
      data: page, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestCases>))
    const empty = { data: [], isLoading: false, error: undefined, mutate: vi.fn() }
    vi.mocked(useTestCaseHistory).mockReturnValue(empty as unknown as ReturnType<typeof useTestCaseHistory>)
    vi.mocked(useTestCaseReviews).mockReturnValue(empty as unknown as ReturnType<typeof useTestCaseReviews>)
    vi.mocked(useTestCaseComments).mockReturnValue(empty as unknown as ReturnType<typeof useTestCaseComments>)
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
  })

  describe.each(ROLES)('as %s, lifecycle v2 on, one project', (role) => {
    const writes = WRITERS.includes(role)
    const leads = LEADS.includes(role)

    it('the list: New test case, AI Generate, checkboxes and the bulk hint for TESTER+, the trash for QA_LEAD+', () => {
      signIn(role)
      renderTab()
      expect(Boolean(screen.queryByRole('button', { name: 'New test case from catalog toolbar' }))).toBe(writes)
      expect(Boolean(screen.queryByRole('button', { name: 'AI Generate' }))).toBe(writes)
      expect(Boolean(within(row(DRAFT.id)).queryByRole('checkbox', { name: `Select ${DRAFT.title}` }))).toBe(writes)
      expect(Boolean(screen.queryByRole('checkbox', { name: 'Select all on this page' }))).toBe(writes)
      // An automation row is never selectable.
      expect(within(row(AUTOMATION.id)).queryByRole('checkbox')).toBeNull()
      expect(footer().textContent).toMatch(writes ? /^2 of 2 · (⌘|Ctrl\+)E bulk edit$/ : /^2 of 2$/)
      expect(Boolean(within(row(DRAFT.id)).queryByRole('button', { name: `Deprecate ${DRAFT.title}` }))).toBe(leads)
    })

    it('the open case: Edit for TESTER+; the Status moves, the retire move only for QA_LEAD+', () => {
      signIn(role)
      renderTab()
      openCase()
      expect(Boolean(within(pane()).queryByRole('button', { name: 'Edit' }))).toBe(writes)
      const toolbar = within(pane()).queryByRole('group', { name: 'Case actions' })
      expect(Boolean(toolbar)).toBe(writes)
      if (toolbar) {
        expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual(
          leads ? ['Make active', 'Request review', 'Deprecate…', 'Edit'] : ['Make active', 'Request review', 'Edit'],
        )
      }
    })

    it('comments and AI review: TESTER+ write; a viewer is told why not', () => {
      signIn(role)
      renderTab()
      openCase()
      tab('Comments')
      expect(Boolean(within(pane()).queryByRole('button', { name: 'Post Comment' }))).toBe(writes)
      expect(Boolean(within(pane()).queryByText('Viewers can read comments.'))).toBe(!writes)
      tab('AI Review')
      expect(Boolean(within(pane()).queryByRole('button', { name: 'Run AI Review' }))).toBe(writes)
      expect(Boolean(within(pane()).queryByText('AI review needs the Tester role or above.'))).toBe(!writes)
    })
  })

  describe.each(ROLES)('as %s, lifecycle v2 off (legacy)', (role) => {
    const writes = WRITERS.includes(role)
    const leads = LEADS.includes(role)

    it('the trash (Delete) for QA_LEAD+, the legacy Request Review and Edit for TESTER+, no Status moves', () => {
      signIn(role)
      renderTab({ lifecycleV2: false })
      expect(Boolean(within(row(DRAFT.id)).queryByRole('button', { name: `Delete ${DRAFT.title}` }))).toBe(leads)
      openCase()
      expect(Boolean(within(pane()).queryByRole('button', { name: 'Request Review' }))).toBe(writes)
      expect(Boolean(within(pane()).queryByRole('button', { name: 'Edit' }))).toBe(writes)
      expect(within(pane()).queryByRole('button', { name: 'Make active' })).toBeNull()
      expect(within(pane()).queryByText(/^Review is (required|optional)/)).toBeNull()
      const toolbar = within(pane()).queryByRole('group', { name: 'Case actions' })
      if (toolbar) expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Edit'])
    })
  })

  describe.each(WRITERS)('as %s in All Projects', (role) => {
    it('is read-only: no New test case, AI Generate, checkbox, hint, Edit, moves or comment box', () => {
      signIn(role)
      renderTab({ projectId: null })
      expect(screen.queryByRole('button', { name: 'New test case from catalog toolbar' })).toBeNull()
      expect(screen.queryByRole('button', { name: 'AI Generate' })).toBeNull()
      expect(screen.queryByRole('checkbox', { name: /^Select / })).toBeNull()
      expect(footer().textContent).toBe('2 of 2')
      openCase()
      expect(within(pane()).queryByRole('group', { name: 'Case actions' })).toBeNull()
      expect(within(pane()).queryByRole('button', { name: 'Edit' })).toBeNull()
      tab('Comments')
      expect(within(pane()).queryByRole('button', { name: 'Post Comment' })).toBeNull()
      expect(within(pane()).getByText('Choose a project to add a comment.')).toBeInTheDocument()
    })
  })
})
