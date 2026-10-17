/**
 * Inbox › Approvals › Test cases (W27b PAN-05, contract §2.1 assertion 3, flow
 * L14): a decision's review notes open IN THE PAGE, as a full-width row right
 * under the case's row (one cell over the table's 5 columns), never a modal.
 * Claim / Unclaim send at once; Approve / Request changes / Reject send the
 * same service call as before, with the trimmed notes, once the notes are in;
 * Cancel and Escape send nothing; typed notes ask before another row's form
 * replaces them; the queues and every tm-cases roll are re-read after a
 * decision.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { refreshTestCases, useTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase } from '@/types/test-management'
import CaseApprovals from './CaseApprovals'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useTestCases: vi.fn(),
  refreshTestCases: vi.fn(),
}))
vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: vi.fn() }))
vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      reviewAction: vi.fn(),
      transitionCase: vi.fn(),
    },
  }
})
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function reviewCase(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'c1', project_id: 'project-1', title: 'Checkout case c1', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'review_requested', version: 1, is_automated: false,
    automation_status: 'manual', ai_generated: false, allowed_actions: ['claim_review'],
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', ...overrides,
  }
}

const C1 = reviewCase({})
const C2 = reviewCase({ id: 'c2', title: 'Checkout case c2', status: 'under_review', version: 4, allowed_actions: ['approve', 'request_changes', 'reject', 'unclaim'] })

const mutateRequested = vi.fn()
const mutateClaimed = vi.fn()

function renderQueue() {
  return render(
    <MemoryRouter>
      <CaseApprovals />
    </MemoryRouter>,
  )
}

const rowOf = (title: string) =>
  screen.getAllByTestId('case-approval-row').find((row) => row.textContent?.includes(title)) as HTMLTableRowElement

describe('Inbox › Approvals › Test cases: the notes form in the page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mutateRequested.mockResolvedValue(undefined)
    mutateClaimed.mockResolvedValue(undefined)
    vi.mocked(refreshTestCases).mockResolvedValue([] as never)
    vi.mocked(useFeatureEnabled).mockReturnValue(false)
    vi.mocked(useTestCases).mockImplementation(((params: { status?: string }) => (params.status === 'review_requested'
      ? { data: { items: [C1], total: 1, page: 1, size: 50, pages: 1 }, isLoading: false, error: undefined, mutate: mutateRequested }
      : { data: { items: [C2], total: 1, page: 1, size: 50, pages: 1 }, isLoading: false, error: undefined, mutate: mutateClaimed })) as never)
  })

  it('opens Approve under its row, one cell spanning the 5 columns, notes focused; no dialog', () => {
    renderQueue()
    const row = rowOf('Checkout case c1')
    fireEvent.click(within(row).getByRole('button', { name: 'Approve' }))

    const form = screen.getByRole('region', { name: 'Approve' })
    const formRow = row.nextElementSibling as HTMLTableRowElement
    expect(formRow).toHaveAttribute('data-in-page-row', '')
    expect(formRow).not.toHaveAttribute('data-testid')
    expect(formRow.cells).toHaveLength(1)
    expect(formRow.cells[0]).toHaveAttribute('colspan', '5')
    expect(formRow).toContainElement(form)
    expect(within(form).getByRole('textbox', { name: /Review notes/ })).toHaveFocus()
    expect(within(form).getByText('Record a nonblank review note for the audit trail.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    // The form row is not a case row.
    expect(screen.getAllByTestId('case-approval-row')).toHaveLength(2)
  })

  it('Cancel and Escape send nothing', () => {
    renderQueue()
    const approve = within(rowOf('Checkout case c1')).getByRole('button', { name: 'Approve' })
    fireEvent.click(approve)
    fireEvent.click(within(screen.getByRole('region', { name: 'Approve' })).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Approve' })).toBeNull()

    fireEvent.click(approve)
    const notes = screen.getByRole('textbox', { name: /Review notes/ })
    fireEvent.change(notes, { target: { value: 'typed' } })
    fireEvent.keyDown(notes, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Approve' })).toBeNull()
    expect(testManagementService.reviewAction).not.toHaveBeenCalled()
    expect(testManagementService.transitionCase).not.toHaveBeenCalled()
  })

  it('legacy: Approve → the trimmed notes → reviewAction once, then the queues and every tm-cases roll', async () => {
    vi.mocked(testManagementService.reviewAction).mockResolvedValue({ ...C1, status: 'approved' })
    renderQueue()
    fireEvent.click(within(rowOf('Checkout case c1')).getByRole('button', { name: 'Approve' }))
    const form = screen.getByRole('region', { name: 'Approve' })
    const confirm = within(form).getByRole('button', { name: 'Approve' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(form).getByRole('textbox', { name: /Review notes/ }), { target: { value: '  Covers the refund path  ' } })
    fireEvent.click(confirm)

    await waitFor(() => expect(testManagementService.reviewAction).toHaveBeenCalledWith('c1', 'approve', 'Covers the refund path'))
    expect(testManagementService.reviewAction).toHaveBeenCalledTimes(1)
    expect(testManagementService.transitionCase).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Approve' })).toBeNull())
    expect(mutateRequested).toHaveBeenCalled()
    expect(mutateClaimed).toHaveBeenCalled()
    expect(refreshTestCases).toHaveBeenCalled()
    // The approved case left the queue.
    expect(screen.queryByText('Checkout case c1')).toBeNull()
  })

  it('lifecycle v2: Claim review sends at once (no form); Request changes sends the transition with the version and notes', async () => {
    vi.mocked(useFeatureEnabled).mockReturnValue(true)
    vi.mocked(testManagementService.transitionCase)
      .mockResolvedValueOnce({ ...C1, status: 'under_review', version: 2 })
      .mockResolvedValueOnce({ ...C2, status: 'draft', version: 5 })
    renderQueue()

    await act(async () => {
      fireEvent.click(within(rowOf('Checkout case c1')).getByRole('button', { name: 'Claim review' }))
    })
    expect(testManagementService.transitionCase).toHaveBeenCalledWith('c1', { action: 'claim_review', expected_version: 1 })
    expect(screen.queryByRole('region', { name: 'Claim review' })).toBeNull()

    fireEvent.click(within(rowOf('Checkout case c2')).getByRole('button', { name: 'Request changes' }))
    const form = screen.getByRole('region', { name: 'Request changes' })
    expect(rowOf('Checkout case c2').nextElementSibling).toContainElement(form)
    fireEvent.change(within(form).getByRole('textbox', { name: /Review notes/ }), { target: { value: ' Step 3 has no expected result ' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Request changes' }))

    await waitFor(() => expect(testManagementService.transitionCase).toHaveBeenCalledTimes(2))
    expect(testManagementService.transitionCase).toHaveBeenLastCalledWith('c2', {
      action: 'request_changes',
      expected_version: 4,
      notes: 'Step 3 has no expected result',
    })
    expect(testManagementService.reviewAction).not.toHaveBeenCalled()
  })

  it("typed notes ask before another row's decision replaces them; Keep editing keeps them", () => {
    renderQueue()
    fireEvent.click(within(rowOf('Checkout case c1')).getByRole('button', { name: 'Approve' }))
    const first = screen.getByRole('region', { name: 'Approve' })
    fireEvent.change(within(first).getByRole('textbox', { name: /Review notes/ }), { target: { value: 'Half a note' } })

    fireEvent.click(within(rowOf('Checkout case c2')).getByRole('button', { name: 'Reject' }))
    expect(within(first).getByText(/Discard your changes to/)).toHaveTextContent('Discard your changes to "Approve"?')
    fireEvent.click(within(first).getByRole('button', { name: 'Keep editing' }))
    expect(within(first).getByRole('textbox', { name: /Review notes/ })).toHaveValue('Half a note')
    expect(screen.queryByRole('region', { name: 'Reject' })).toBeNull()

    fireEvent.click(within(rowOf('Checkout case c2')).getByRole('button', { name: 'Reject' }))
    fireEvent.click(within(first).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Approve' })).toBeNull()
    const reject = screen.getByRole('region', { name: 'Reject' })
    expect(rowOf('Checkout case c2').nextElementSibling).toContainElement(reject)
    expect(within(reject).getByRole('textbox', { name: /Review notes/ })).toHaveValue('')
  })
})
