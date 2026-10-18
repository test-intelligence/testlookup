/**
 * W28 TCM-03 (contract §2.3 frontend 8): the Cases tab's selection and the
 * "⌘E bulk edit" hint made true.
 *
 *  - the row checkbox sits INSIDE the Test case cell, before the title (the
 *    table keeps its five column headers), and ticking it never opens the case;
 *    automation rows get none;
 *  - the header checkbox (inside the Test case header) selects the page's
 *    selectable rows;
 *  - a page, filter or search change clears the selection;
 *  - Ctrl+E (and Meta+E) opens the bulk bar and focuses its Action select —
 *    not while typing in the search box;
 *  - the footer names the platform's key; the dead ↑/↓ and ↵ hints are gone;
 *  - after Apply both rolls re-read and the selection keeps only the skipped.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { testCaseEditingService } from '@/services/testCaseEditingService'
import { useAuthStore, type User } from '@/store/authStore'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/testCaseEditingService', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/services/testCaseEditingService')>()
  return { ...real, testCaseEditingService: { bulkUpdate: vi.fn(), getCaseFresh: vi.fn() } }
})

const bulkUpdate = vi.mocked(testCaseEditingService.bulkUpdate)

function signIn(role: string) {
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role,
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'medium',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', allowed_actions: ['activate'],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

const A = base({ id: 'c-1', title: 'Verify refund email', version: 2 })
const B = base({ id: 'c-2', title: 'Sign in with SSO', status: 'approved', version: 4 })
const C = base({ id: 'c-3', title: 'Old checkout', status: 'deprecated', version: 1 })
const AUTOMATION = base({ id: 'auto-1', title: 'checkout_spec.ts › pays', source: 'automation' })

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
const box = (tc: ManagedTestCase) => within(row(tc.id)).getByRole('checkbox', { name: `Select ${tc.title}` }) as HTMLInputElement
const pageBox = () => screen.getByRole('checkbox', { name: 'Select all on this page' }) as HTMLInputElement
const bar = () => screen.queryByRole('toolbar', { name: 'Bulk edit' })
const footer = () => document.querySelector('[data-cases-footer-count]') as HTMLElement

describe('/test-management: select cases and edit them together (W28)', () => {
  const mutateCases = vi.fn()
  const mutateHealth = vi.fn()
  const platform = Object.getOwnPropertyDescriptor(window.navigator, 'platform')

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    signIn('QA_ENGINEER')
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    const page: PaginatedResponse<ManagedTestCase> = { items: [A, B, C, AUTOMATION], total: 60, page: 1, size: 25, pages: 3 }
    vi.mocked(useTestCases).mockImplementation((params) => ({
      data: page, error: undefined, isLoading: false, isValidating: false,
      mutate: params?.size === 200 ? mutateHealth : mutateCases,
    } as unknown as ReturnType<typeof useTestCases>))
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
    if (platform) Object.defineProperty(window.navigator, 'platform', platform)
    else Reflect.deleteProperty(window.navigator, 'platform')
  })

  it('puts the checkbox inside the Test case cell before the title; the table keeps five columns; a tick never opens the case', () => {
    renderTab()
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader')).toHaveLength(5)
    const firstCell = row(A.id).querySelector('td') as HTMLTableCellElement
    expect(firstCell.contains(box(A))).toBe(true)
    expect(box(A).compareDocumentPosition(within(firstCell).getByText(A.title)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // The page checkbox is inside the Test case header, not a sixth column.
    const header = within(table).getAllByRole('columnheader')[0]
    expect(header.contains(pageBox())).toBe(true)
    expect(header).toHaveTextContent('Test case')
    expect(within(row(AUTOMATION.id)).queryByRole('checkbox')).toBeNull()

    fireEvent.click(box(A))
    expect(box(A)).toBeChecked()
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(bar()).not.toBeNull()
    expect(within(bar() as HTMLElement).getByText('1 selected')).toBeInTheDocument()
  })

  it('the header checkbox selects the page\'s selectable rows, and clears them again', () => {
    renderTab()
    fireEvent.click(box(B))
    expect(pageBox().indeterminate).toBe(true)
    fireEvent.click(pageBox())
    expect([box(A), box(B), box(C)].every((b) => b.checked)).toBe(true)
    expect(pageBox()).toBeChecked()
    expect(within(bar() as HTMLElement).getByText('3 selected')).toBeInTheDocument()
    fireEvent.click(pageBox())
    expect([box(A), box(B), box(C)].some((b) => b.checked)).toBe(false)
  })

  it('a filter, the search or the page clears the selection', () => {
    renderTab()
    fireEvent.click(box(A))
    fireEvent.change(screen.getByRole('combobox', { name: 'Priority' }), { target: { value: 'high' } })
    expect(box(A)).not.toBeChecked()
    expect(bar()).toBeNull()

    fireEvent.click(box(B))
    fireEvent.change(screen.getByPlaceholderText('Search by title, tags or ID…'), { target: { value: 'sso' } })
    expect(box(B)).not.toBeChecked()

    fireEvent.click(box(C))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(box(C)).not.toBeChecked()
  })

  it('Ctrl+E and Meta+E open the bar and focus its Action select; not while typing in the search box', async () => {
    renderTab()
    expect(bar()).toBeNull()
    const search = screen.getByPlaceholderText('Search by title, tags or ID…')
    search.focus()
    expect(fireEvent.keyDown(search, { key: 'e', ctrlKey: true })).toBe(true)
    expect(bar()).toBeNull()

    expect(fireEvent.keyDown(document.body, { key: 'e', ctrlKey: true })).toBe(false)
    expect(bar()).not.toBeNull()
    expect(within(bar() as HTMLElement).getByText('Select cases with the checkboxes to edit them together.')).toBeInTheDocument()
    await waitFor(() => expect(within(bar() as HTMLElement).getByRole('combobox', { name: 'Action' })).toHaveFocus())

    fireEvent.click(within(bar() as HTMLElement).getByRole('button', { name: 'Clear' }))
    expect(bar()).toBeNull()
    // From a ticked checkbox (not typing) Meta+E works too; Shift / Alt leave the browser its key.
    fireEvent.click(box(A))
    expect(fireEvent.keyDown(box(A), { key: 'e', metaKey: true, shiftKey: true })).toBe(true)
    expect(fireEvent.keyDown(box(A), { key: 'E', metaKey: true })).toBe(false)
    await waitFor(() => expect(within(bar() as HTMLElement).getByRole('combobox', { name: 'Action' })).toHaveFocus())
  })

  it('the footer names the platform\'s key and drops the dead hints', () => {
    Object.defineProperty(window.navigator, 'platform', { value: 'MacIntel', configurable: true })
    const { unmount } = renderTab()
    expect(footer().textContent).toBe('4 of 60 · ⌘E bulk edit')
    unmount()
    Object.defineProperty(window.navigator, 'platform', { value: 'Win32', configurable: true })
    renderTab()
    expect(footer().textContent).toBe('4 of 60 · Ctrl+E bulk edit')
    expect(document.body.textContent).not.toMatch(/↑\/↓ navigate|↵ open/)
  })

  it('a viewer and All Projects get no checkbox, no hint and no Ctrl+E', () => {
    signIn('VIEWER')
    const { unmount } = renderTab()
    expect(screen.queryByRole('checkbox', { name: /^Select / })).toBeNull()
    expect(footer().textContent).toBe('4 of 60')
    expect(fireEvent.keyDown(document.body, { key: 'e', ctrlKey: true })).toBe(true)
    expect(bar()).toBeNull()
    unmount()
    signIn('QA_LEAD')
    renderTab(null)
    expect(screen.queryByRole('checkbox', { name: /^Select / })).toBeNull()
    expect(footer().textContent).toBe('4 of 60')
  })

  it('after Apply both rolls re-read and the selection keeps only the skipped cases', async () => {
    bulkUpdate.mockResolvedValue({
      action: 'set_priority', requested: 3, changed: 2, unchanged: 0, skipped: 1, not_found: 0,
      results: [
        { id: A.id, outcome: 'changed', version: 3, status: 'draft' },
        { id: B.id, outcome: 'changed', version: 5, status: 'approved' },
        { id: C.id, outcome: 'skipped', reason: 'Reinstate it to edit it', version: 1, status: 'deprecated' },
      ],
    })
    renderTab()
    fireEvent.click(pageBox())
    const toolbar = bar() as HTMLElement
    fireEvent.change(within(toolbar).getByRole('combobox', { name: 'Action' }), { target: { value: 'set_priority' } })
    fireEvent.change(within(toolbar).getByRole('combobox', { name: 'Priority' }), { target: { value: 'high' } })
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(bulkUpdate).toHaveBeenCalledTimes(1))
    expect(bulkUpdate).toHaveBeenCalledWith({
      project_id: 'project-1',
      case_ids: [A.id, B.id, C.id],
      expected_versions: { [A.id]: 2, [B.id]: 4, [C.id]: 1 },
      action: 'set_priority',
      priority: 'high',
    })
    await waitFor(() => expect(mutateCases).toHaveBeenCalled())
    expect(mutateHealth).toHaveBeenCalled()
    expect(box(A)).not.toBeChecked()
    expect(box(B)).not.toBeChecked()
    expect(box(C)).toBeChecked()
    // The bar stays to say what happened.
    expect(within(bar() as HTMLElement).getByRole('status')).toHaveTextContent('2 changed · 1 skipped')
    expect(within(bar() as HTMLElement).getByText(/Old checkout — Reinstate it to edit it/)).toBeInTheDocument()
  })
})
