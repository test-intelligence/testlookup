/**
 * W28 TCM-01 / TCM-04 (contract §2.1 frontend 10, §2.4 6): editing a case in
 * the page.
 *
 *  - Edit (the Details toolbar) opens the edit form IN the Details tab, in
 *    place of the read-only details (the toolbar hides), Title focused;
 *  - the pane's slot: with typed input, Close case, another row, Next and
 *    another tab ask `Discard your changes to "Edit test case"?` first;
 *  - a deprecated / archived case's Edit is disabled (`Reinstate it to edit
 *    it`); an automation row has no Edit; All Projects has none;
 *  - after Save the pane shows the new version and both rolls re-read;
 *  - History → Restore this version → the pane shows the new version and the
 *    history re-reads.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAuditLog,
  useTestCaseComments,
  useTestCaseHistory,
  useTestCaseReviews,
  useTestCases,
} from '@/hooks/useTestManagement'
import { useLibraryHealth } from '@/hooks/useTestLibrary'
import { testCaseEditingService } from '@/services/testCaseEditingService'
import { useAuthStore, type User } from '@/store/authStore'
import type { ManagedTestCase, PaginatedResponse, TestCaseVersion } from '@/types/test-management'
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
vi.mock('@/services/testCaseEditingService', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/services/testCaseEditingService')>()
  return {
    ...real,
    testCaseEditingService: {
      updateCase: vi.fn(),
      getCaseFresh: vi.fn(),
      getCaseHistoryFresh: vi.fn(),
      bulkUpdate: vi.fn(),
      restoreVersion: vi.fn(),
    },
  }
})

const service = vi.mocked(testCaseEditingService)

function signIn(role: string) {
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role,
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', author_id: 'u-author',
    allowed_actions: ['activate', 'request_review'], reason_required_actions: [],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

const REFUND = base({ id: 'c-1', title: 'Verify refund email', version: 3, steps: [{ step_number: 1, action: 'Refund', expected_result: 'Email' }] })
const SIGNIN = base({ id: 'c-2', title: 'Sign in with SSO' })
const RETIRED = base({ id: 'c-3', title: 'Old checkout', status: 'deprecated', allowed_actions: ['reinstate'] })
const AUTOMATION = base({ id: 'auto-1', title: 'checkout_spec.ts › pays', source: 'automation', allowed_actions: [] })

function version(n: number, title: string): TestCaseVersion {
  return {
    id: `v-${n}`, test_case_id: REFUND.id, version: n, title, status: 'draft', change_type: n === 1 ? 'created' : 'updated',
    created_at: `2026-10-0${n}T00:00:00Z`, changed_by_name: 'Priya Shah', changed_fields: n === 1 ? [] : ['title'],
  }
}

function renderTab(projectId: string | null = 'project-1') {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2 />} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLTableRowElement
const pane = (title = REFUND.title) => screen.getByRole('complementary', { name: title })
const editForm = () => screen.getByRole('region', { name: 'Edit test case' })
const titleField = () => within(editForm()).getByRole('textbox', { name: 'Title' })
const askQuestion = () => within(editForm()).queryByText(/Discard your changes to/)

function openAndType(id = REFUND.id) {
  fireEvent.click(row(id))
  fireEvent.click(within(pane()).getByRole('button', { name: 'Edit' }))
  fireEvent.change(titleField(), { target: { value: 'Verify the refund email arrives' } })
}

describe('/test-management: edit a case in the page (W28)', () => {
  const mutateCases = vi.fn()
  const mutateHealth = vi.fn()
  const mutateHistory = vi.fn()
  let history: TestCaseVersion[] = []

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    signIn('QA_ENGINEER')
    history = [version(3, 'Verify refund email'), version(2, 'Verify refund'), version(1, 'Refund')]
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    const page: PaginatedResponse<ManagedTestCase> = {
      items: [REFUND, SIGNIN, RETIRED, AUTOMATION], total: 4, page: 1, size: 25, pages: 1,
    }
    vi.mocked(useTestCases).mockImplementation((params) => ({
      data: page, error: undefined, isLoading: false, isValidating: false,
      mutate: params?.size === 200 ? mutateHealth : mutateCases,
    } as unknown as ReturnType<typeof useTestCases>))
    // W13 (deliberate set-up): the health read is `useLibraryHealth` now (the 200-row roll is gone).
    vi.mocked(useLibraryHealth).mockReturnValue(
      { data: undefined, error: undefined, isLoading: false, mutate: mutateHealth } as unknown as ReturnType<typeof useLibraryHealth>,
    )
    vi.mocked(useTestCaseHistory).mockImplementation((id?: string) => ({
      data: id ? history : undefined, isLoading: false, error: undefined, mutate: mutateHistory,
    } as unknown as ReturnType<typeof useTestCaseHistory>))
    const empty = { data: [], isLoading: false, error: undefined, mutate: vi.fn() }
    vi.mocked(useTestCaseReviews).mockReturnValue(empty as unknown as ReturnType<typeof useTestCaseReviews>)
    vi.mocked(useTestCaseComments).mockReturnValue(empty as unknown as ReturnType<typeof useTestCaseComments>)
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
  })

  it('Edit opens the form in the Details tab in place of the details, the toolbar hidden, Title focused', () => {
    renderTab()
    fireEvent.click(row(REFUND.id))
    const toolbar = within(pane()).getByRole('group', { name: 'Case actions' })
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Edit' }))
    const form = editForm()
    expect(pane().contains(form)).toBe(true)
    expect(titleField()).toHaveFocus()
    expect(titleField()).toHaveValue('Verify refund email')
    expect(within(pane()).queryByRole('group', { name: 'Case actions' })).toBeNull()
    // The read-only details are not shown beside the form.
    expect(within(pane()).queryByText('Test Steps')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    // Cancel brings the details back.
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Edit test case' })).toBeNull()
    expect(within(pane()).getByRole('group', { name: 'Case actions' })).toBeInTheDocument()
  })

  it('a typed form asks before Close case', () => {
    renderTab()
    openAndType()
    fireEvent.click(within(pane()).getByRole('button', { name: 'Close case' }))
    expect(askQuestion()).toHaveTextContent('Discard your changes to "Edit test case"?')
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Keep editing' }))
    expect(titleField()).toHaveValue('Verify the refund email arrives')
    fireEvent.click(within(pane()).getByRole('button', { name: 'Close case' }))
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('complementary', { name: REFUND.title })).toBeNull()
  })

  it('a typed form asks before another row opens, and the form goes with its case', () => {
    renderTab()
    openAndType()
    fireEvent.click(row(SIGNIN.id))
    expect(askQuestion()).toBeInTheDocument()
    expect(pane()).toBeInTheDocument()
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Discard changes' }))
    expect(pane(SIGNIN.title)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Edit test case' })).toBeNull()
  })

  it('a typed form asks before Next', () => {
    renderTab()
    openAndType()
    fireEvent.click(within(pane()).getByRole('button', { name: 'Next case' }))
    expect(askQuestion()).toBeInTheDocument()
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Discard changes' }))
    expect(pane(SIGNIN.title)).toBeInTheDocument()
  })

  it('a typed form asks before another tab of the pane', () => {
    renderTab()
    openAndType()
    fireEvent.click(within(pane()).getByRole('button', { name: 'History' }))
    expect(askQuestion()).toBeInTheDocument()
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Edit test case' })).toBeNull()
    // Back on Details: the details, not the form.
    fireEvent.click(within(pane()).getByRole('button', { name: 'Details' }))
    expect(within(pane()).getByRole('group', { name: 'Case actions' })).toBeInTheDocument()
  })

  it('a clean form never asks: Next steps at once and the form goes', () => {
    renderTab()
    fireEvent.click(row(REFUND.id))
    fireEvent.click(within(pane()).getByRole('button', { name: 'Edit' }))
    fireEvent.click(within(pane()).getByRole('button', { name: 'Next case' }))
    expect(pane(SIGNIN.title)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Edit test case' })).toBeNull()
  })

  it('a retired case\'s Edit is disabled with how to edit it; an automation row has no Edit', () => {
    renderTab()
    fireEvent.click(row(RETIRED.id))
    const edit = within(pane(RETIRED.title)).getByRole('button', { name: 'Edit' })
    expect(edit).toBeDisabled()
    expect(edit).toHaveAttribute('title', 'Reinstate it to edit it')
    fireEvent.click(row(AUTOMATION.id))
    expect(within(pane(AUTOMATION.title)).queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('All Projects has no Edit', () => {
    renderTab(null)
    fireEvent.click(row(REFUND.id))
    expect(within(pane()).queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('after Save the pane shows the new version and both rolls re-read', async () => {
    service.updateCase.mockResolvedValue({ ...REFUND, title: 'Verify the refund email arrives', version: 4 })
    renderTab()
    openAndType()
    fireEvent.click(within(editForm()).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Edit test case' })).toBeNull())
    expect(service.updateCase).toHaveBeenCalledWith(REFUND.id, { expected_version: 3, title: 'Verify the refund email arrives' })
    const updatedPane = pane('Verify the refund email arrives')
    const versionCell = within(updatedPane).getByText('Version').parentElement as HTMLElement
    expect(versionCell).toHaveTextContent('4')
    expect(mutateCases).toHaveBeenCalled()
    expect(mutateHealth).toHaveBeenCalled()
  })

  it('History → Restore this version → the pane shows the new version and the history re-reads', async () => {
    service.restoreVersion.mockResolvedValue({ ...REFUND, title: 'Refund', version: 4 })
    renderTab()
    fireEvent.click(row(REFUND.id))
    fireEvent.click(within(pane()).getByRole('button', { name: 'History' }))
    const card = document.querySelector('[data-history-version="1"]') as HTMLElement
    expect(card).not.toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Restore this version' }))
    fireEvent.click(within(card).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(service.restoreVersion).toHaveBeenCalledWith(REFUND.id, { version: 1, expected_version: 3 }))
    await waitFor(() => expect(screen.getByRole('complementary', { name: 'Refund' })).toBeInTheDocument())
    expect(mutateHistory).toHaveBeenCalled()
    expect(mutateCases).toHaveBeenCalled()
    expect(mutateHealth).toHaveBeenCalled()
  })
})
