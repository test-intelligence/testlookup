/**
 * W27b PAN-05 (contract §2.1, assertion 2): /test-management's Cases tab
 * opens every form and section IN THE PAGE, where its action lives.
 *
 *  - Insights: a read-only section right after the banner (and its
 *    stat-basis line), before the filter bar;
 *  - New test case / AI Generate: one at a time, between the filter bar and
 *    the cases row (`[data-cases-layout]`); a typed title asks
 *    `Discard your changes to "New Test Case"?` before AI Generate replaces it;
 *  - a row's Delete / Deprecate: the reason form is the `<tr>` right after
 *    that row, one cell spanning the table (5 columns, 4 beside an open case);
 *    Cancel and Escape send nothing, the confirm sends today's call; a late
 *    answer closes only its own form;
 *  - no `role="dialog"` and no `aria-modal` anywhere in the tab.
 *
 * The W27a deep-link tests stay in TestManagementPage.inPage.test.tsx.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { useLibraryHealth } from '@/hooks/useTestLibrary'
import { testManagementService } from '@/services/testManagementService'
import { useAuthStore } from '@/store/authStore'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

// W28: New test case and AI Generate are TESTER+, a row's Deprecate / Delete QA_LEAD+
// (signed out, usePermissions reads VIEWER). TestManagementPage.roles.test.tsx pins every role.
beforeEach(() => { useAuthStore.setState({ user: { id: 'u-lead', role: 'QA_LEAD' } as never }) })
afterEach(() => { useAuthStore.setState({ user: null }) })

// W13 (deliberate set-up): the page's new reads (library health, the owner list, saved views) stubbed idle.
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [] }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'active', version: 1, is_automated: false,
    automation_status: 'manual', ai_generated: false, allowed_actions: ['deprecate'], source: 'managed',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

const REFUND = base({ id: 'c-1', title: 'Verify refund email', version: 3 })
const SIGNIN = base({ id: 'c-2', title: 'Sign in with SSO', status: 'review_requested', version: 1 })

function renderTab(lifecycleV2 = true) {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId="project-1" lifecycleV2={lifecycleV2} />} />
      </Routes>
    </MemoryRouter>,
  )
}

const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
const noOverlay = () => {
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.querySelector('[aria-modal]')).toBeNull()
}
const caseRow = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLTableRowElement
const search = () => screen.getByPlaceholderText('Search by title, tags or ID…')
const layout = () => document.querySelector('[data-cases-layout]') as HTMLElement

describe('/test-management Cases: forms and sections in the page (W27b)', () => {
  const mutateCases = vi.fn()
  const mutateHealth = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    const page: PaginatedResponse<ManagedTestCase> = { items: [REFUND, SIGNIN], total: 2, page: 1, size: 25, pages: 1 }
    vi.mocked(useTestCases).mockImplementation((params) => ({
      data: page, error: undefined, isLoading: false, isValidating: false,
      mutate: params?.size === 200 ? mutateHealth : mutateCases,
    } as unknown as ReturnType<typeof useTestCases>))
    // W13 (deliberate set-up): the health read is `useLibraryHealth` now (the 200-row roll is gone).
    vi.mocked(useLibraryHealth).mockReturnValue(
      { data: undefined, error: undefined, isLoading: false, mutate: mutateHealth } as unknown as ReturnType<typeof useLibraryHealth>,
    )
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('Insights opens after the banner and before the filter bar, as a read-only aside; Close panel closes it', () => {
    renderTab()
    expect(screen.queryByRole('complementary', { name: 'Insights' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Insights/ }))

    const insights = screen.getByRole('complementary', { name: 'Insights' })
    const banner = document.querySelector('[data-status-banner]') as HTMLElement
    expect(follows(banner, insights)).toBe(true)
    expect(follows(insights, search())).toBe(true)
    expect(follows(insights, layout())).toBe(true)
    // Its cards in a two-column grid from lg (the drawer's single column went).
    const cards = insights.querySelector('[data-insights]') as HTMLElement
    expect(cards.className).toContain('lg:grid-cols-2')
    expect(cards.children).toHaveLength(5)
    noOverlay()

    fireEvent.click(within(insights).getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByRole('complementary', { name: 'Insights' })).toBeNull()
  })

  it('New test case opens between the filter bar and the cases row, its title focused; AI Generate asks before replacing a typed title', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'New test case from catalog toolbar' }))

    const create = screen.getByRole('region', { name: 'New Test Case' })
    expect(follows(search(), create)).toBe(true)
    expect(follows(create, layout())).toBe(true)
    expect(layout().contains(create)).toBe(false)
    expect(within(create).getByLabelText('Title *')).toHaveFocus()
    noOverlay()

    fireEvent.change(within(create).getByLabelText('Title *'), { target: { value: 'Guest checkout keeps the basket' } })
    fireEvent.click(screen.getByRole('button', { name: 'AI Generate' }))
    expect(within(create).getByText(/Discard your changes to/)).toHaveTextContent('Discard your changes to "New Test Case"?')
    fireEvent.click(within(create).getByRole('button', { name: 'Keep editing' }))
    expect(within(create).getByLabelText('Title *')).toHaveValue('Guest checkout keeps the basket')
    expect(screen.queryByRole('region', { name: 'AI Generate Test Cases' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'AI Generate' }))
    fireEvent.click(within(screen.getByRole('region', { name: 'New Test Case' })).getByRole('button', { name: 'Discard changes' }))
    const generate = screen.getByRole('region', { name: 'AI Generate Test Cases' })
    expect(screen.queryByRole('region', { name: 'New Test Case' })).toBeNull()
    expect(follows(search(), generate)).toBe(true)
    expect(follows(generate, layout())).toBe(true)
    expect(within(generate).getByRole('textbox', { name: 'What to generate test cases for' })).toHaveFocus()
    // The X is labelled now (it was a bare ×).
    fireEvent.click(within(generate).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'AI Generate Test Cases' })).toBeNull()
  })

  it('a clean New test case is replaced by AI Generate at once (no question)', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'New test case from catalog toolbar' }))
    fireEvent.click(screen.getByRole('button', { name: 'AI Generate' }))
    expect(screen.queryByRole('region', { name: 'New Test Case' })).toBeNull()
    expect(screen.getByRole('region', { name: 'AI Generate Test Cases' })).toBeInTheDocument()
    expect(screen.queryByText(/Discard your changes to/)).toBeNull()
  })

  it("a row's Deprecate opens the reason form as the next row, one cell over 5 columns; Cancel and Escape send nothing; the case never opened", () => {
    const transition = vi.spyOn(testManagementService, 'transitionCase')
    renderTab()
    const trash = screen.getByRole('button', { name: 'Deprecate Verify refund email' })
    trash.focus()
    fireEvent.click(trash)

    const form = screen.getByRole('region', { name: 'Deprecate test case' })
    const formRow = caseRow('c-1').nextElementSibling as HTMLTableRowElement
    expect(formRow).toHaveAttribute('data-in-page-row', '')
    expect(formRow.cells).toHaveLength(1)
    expect(formRow.cells[0]).toHaveAttribute('colspan', '5')
    expect(formRow).toContainElement(form)
    expect(formRow).not.toHaveAttribute('data-case-row')
    noOverlay()
    expect(within(form).getByRole('textbox', { name: /Reason/ })).toHaveFocus()
    expect(document.querySelector('[data-case-panel]')).toBeNull()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Deprecate test case' })).toBeNull()
    expect(trash).toHaveFocus()

    fireEvent.click(trash)
    const reason = screen.getByRole('textbox', { name: /Reason/ })
    fireEvent.change(reason, { target: { value: 'typed' } })
    fireEvent.keyDown(reason, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Deprecate test case' })).toBeNull()
    expect(transition).not.toHaveBeenCalled()
  })

  it('beside an open case the reason row spans the 4 columns left; Escape in it leaves the case open', () => {
    renderTab()
    fireEvent.click(caseRow('c-2'))
    expect(screen.getByRole('complementary', { name: 'Sign in with SSO' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Deprecate Verify refund email' }))
    const formRow = caseRow('c-1').nextElementSibling as HTMLTableRowElement
    expect(formRow.cells[0]).toHaveAttribute('colspan', '4')
    const reason = within(formRow).getByRole('textbox', { name: /Reason/ })
    fireEvent.keyDown(reason, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Deprecate test case' })).toBeNull()
    expect(screen.getByRole('complementary', { name: 'Sign in with SSO' })).toBeInTheDocument()
  })

  it('the confirm sends the L3 call with the version of the case the trash was clicked on, then both rolls', async () => {
    const transition = vi.spyOn(testManagementService, 'transitionCase').mockResolvedValue({ ...REFUND, status: 'deprecated', version: 4 })
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Deprecate Verify refund email' }))
    const form = screen.getByRole('region', { name: 'Deprecate test case' })
    expect(within(form).getByText('Deprecate Verify refund email. The case and its version history will be preserved.')).toBeInTheDocument()
    fireEvent.change(within(form).getByRole('textbox', { name: /Reason/ }), { target: { value: '  Superseded by case 7 ' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Deprecate' }))

    await waitFor(() => expect(screen.queryByRole('region', { name: 'Deprecate test case' })).toBeNull())
    expect(transition).toHaveBeenCalledTimes(1)
    expect(transition).toHaveBeenCalledWith('c-1', { action: 'deprecate', reason: 'Superseded by case 7', expected_version: 3 })
    expect(mutateCases).toHaveBeenCalled()
    expect(mutateHealth).toHaveBeenCalled()
  })

  it('legacy: the trash opens Delete test case and the confirm sends the delete with its reason', async () => {
    const remove = vi.spyOn(testManagementService, 'deleteCase').mockResolvedValue(undefined as never)
    renderTab(false)
    fireEvent.click(screen.getByRole('button', { name: 'Delete Sign in with SSO' }))
    const formRow = caseRow('c-2').nextElementSibling as HTMLTableRowElement
    const form = within(formRow).getByRole('region', { name: 'Delete test case' })
    fireEvent.change(within(form).getByRole('textbox', { name: /Reason/ }), { target: { value: 'Duplicate' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('c-2', 'Duplicate'))
    expect(remove).toHaveBeenCalledTimes(1)
  })

  it("another row's trash asks over a typed reason; a late answer closes only its own form", async () => {
    let answer: (value: ManagedTestCase) => void = () => {}
    const transition = vi.spyOn(testManagementService, 'transitionCase').mockImplementation(
      () => new Promise<ManagedTestCase>((resolve) => { answer = resolve }),
    )
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Deprecate Verify refund email' }))
    const first = screen.getByRole('region', { name: 'Deprecate test case' })
    fireEvent.change(within(first).getByRole('textbox', { name: /Reason/ }), { target: { value: 'Superseded' } })
    fireEvent.click(within(first).getByRole('button', { name: 'Deprecate' }))
    expect(transition).toHaveBeenCalledTimes(1)

    // The other row's trash: the typed form asks first, in place.
    fireEvent.click(screen.getByRole('button', { name: 'Deprecate Sign in with SSO' }))
    expect(within(first).getByText(/Discard your changes to/)).toBeInTheDocument()
    expect((caseRow('c-1').nextElementSibling as HTMLElement)).toContainElement(first)
    fireEvent.click(within(first).getByRole('button', { name: 'Discard changes' }))
    const second = within(caseRow('c-2').nextElementSibling as HTMLElement).getByRole('region', { name: 'Deprecate test case' })
    expect(screen.getAllByRole('region', { name: 'Deprecate test case' })).toHaveLength(1)

    // The first request answers now: the second form stays open.
    await act(async () => { answer({ ...REFUND, status: 'deprecated', version: 4 }) })
    expect(second).toBeInTheDocument()
    expect(within(second).getByRole('textbox', { name: /Reason/ })).toBeInTheDocument()
    expect(caseRow('c-2').nextElementSibling).toContainElement(second)
  })
})
