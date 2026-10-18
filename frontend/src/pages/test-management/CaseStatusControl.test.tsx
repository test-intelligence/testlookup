import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { testCaseEditingService } from '@/services/testCaseEditingService'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase } from '@/types/test-management'
import CaseStatusControl from './CaseStatusControl'

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/services/testManagementService', () => ({ testManagementService: { transitionCase: vi.fn() } }))
vi.mock('@/services/testCaseEditingService', () => ({ testCaseEditingService: { getCaseFresh: vi.fn() } }))

const transitionCase = vi.mocked(testManagementService.transitionCase)
const getCaseFresh = vi.mocked(testCaseEditingService.getCaseFresh)

function managedCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'case-1',
    project_id: 'p-1',
    title: 'Sign in',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    status: 'draft',
    version: 4,
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    author_id: 'lead-1',
    allowed_actions: ['activate', 'request_review', 'deprecate'],
    reason_required_actions: ['deprecate'],
    review_required: false,
    ...overrides,
  }
}

function Host({ caseItem, onChanged, canRetire = true, currentUserId = 'lead-1' }: {
  caseItem: ManagedTestCase
  onChanged: (u: ManagedTestCase) => void
  canRetire?: boolean
  currentUserId?: string | null
}) {
  const slot = useInlineSlot<'edit' | 'reason'>()
  return (
    <div data-testid="pane">
      <CaseStatusControl
        caseItem={caseItem}
        currentUserId={currentUserId}
        slot={slot}
        onChanged={onChanged}
        canRetire={canRetire}
        edit={<button type="button">Edit</button>}
      />
      <p data-testid="open-key">{slot.openKey ?? 'none'}</p>
    </div>
  )
}

function setup(caseItem = managedCase(), options: { canRetire?: boolean; currentUserId?: string | null } = {}) {
  const onChanged = vi.fn()
  render(<Host caseItem={caseItem} onChanged={onChanged} {...options} />)
  const toolbar = screen.getByRole('group', { name: 'Case actions' })
  return { toolbar, onChanged }
}

describe('CaseStatusControl', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the moves as buttons, the Edit button on the right, and the review line', () => {
    const { toolbar } = setup()
    expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Make active', 'Request review', 'Deprecate…', 'Edit',
    ])
    expect(toolbar).toHaveAttribute('data-case-toolbar')
    expect(screen.getByText('Review is optional in this project.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('Make active sends the move once and says where the case went', async () => {
    const updated = managedCase({ status: 'active', version: 5 })
    transitionCase.mockResolvedValue(updated)
    const { toolbar, onChanged } = setup()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Make active' }))
    expect(within(toolbar).getByRole('button', { name: 'Saving…' })).toBeDisabled()
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated))
    expect(transitionCase).toHaveBeenCalledTimes(1)
    expect(transitionCase).toHaveBeenCalledWith('case-1', { action: 'activate', expected_version: 4 })
    expect(toast.success).toHaveBeenCalledWith('Moved to Active')
  })

  it('Deprecate… opens the reason form under the toolbar; Cancel and Escape send nothing', async () => {
    const { toolbar } = setup()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Deprecate…' }))
    let form = screen.getByRole('region', { name: 'Deprecate' })
    expect(within(form).getByText('This change is recorded in the audit trail with your reason.')).toBeInTheDocument()
    expect(screen.getByTestId('open-key')).toHaveTextContent('reason')
    // The other moves wait while it is open.
    expect(within(toolbar).getByRole('button', { name: 'Make active' })).toBeDisabled()
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Deprecate' })).toBeNull()
    expect(screen.getByTestId('open-key')).toHaveTextContent('none')

    fireEvent.click(within(toolbar).getByRole('button', { name: 'Deprecate…' }))
    form = screen.getByRole('region', { name: 'Deprecate' })
    fireEvent.keyDown(within(form).getByRole('textbox'), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Deprecate' })).toBeNull()
    expect(transitionCase).not.toHaveBeenCalled()
  })

  it('Deprecate sends the reason', async () => {
    transitionCase.mockResolvedValue(managedCase({ status: 'deprecated', version: 5 }))
    const { toolbar, onChanged } = setup()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Deprecate…' }))
    const form = screen.getByRole('region', { name: 'Deprecate' })
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: '  Superseded by TC-42 ' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Deprecate' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(transitionCase).toHaveBeenCalledWith('case-1', { action: 'deprecate', reason: 'Superseded by TC-42', expected_version: 4 })
    expect(toast.success).toHaveBeenCalledWith('Moved to Deprecated')
    expect(screen.queryByRole('region', { name: 'Deprecate' })).toBeNull()
  })

  it('Approve my case… (a lead\'s own case) sends the reason as the notes too', async () => {
    transitionCase.mockResolvedValue(managedCase({ status: 'approved', version: 5 }))
    const caseItem = managedCase({
      status: 'review_requested', review_required: true,
      allowed_actions: ['withdraw_review', 'approve'], reason_required_actions: ['approve'],
    })
    const { toolbar } = setup(caseItem)
    expect(screen.getByText('Review is required before Active in this project.')).toBeInTheDocument()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Approve my case…' }))
    const form = screen.getByRole('region', { name: 'Approve my case' })
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: 'Checked against the spec' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Approve my case' }))
    await waitFor(() => expect(transitionCase).toHaveBeenCalledWith('case-1', {
      action: 'approve', reason: 'Checked against the spec', notes: 'Checked against the spec', expected_version: 4,
    }))
  })

  it('offers no Approve my case… on someone else\'s case', () => {
    setup(managedCase({ status: 'review_requested', allowed_actions: ['approve'] }), { currentUserId: 'claimant-2' })
    expect(screen.queryByRole('button', { name: 'Approve my case…' })).toBeNull()
  })

  it('a refusal shows its sentence under the toolbar and re-reads the case', async () => {
    transitionCase.mockRejectedValue({ response: { status: 409, data: { detail: { current_state: 'active', attempted_action: 'activate' } } } })
    const fresh = managedCase({ status: 'active', version: 5, allowed_actions: ['revise'] })
    getCaseFresh.mockResolvedValue(fresh)
    const { toolbar, onChanged } = setup()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Make active' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Activate is no longer allowed from active.')
    expect(getCaseFresh).toHaveBeenCalledWith('case-1')
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(fresh))
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('a 403 keeps its sentence even when the re-read fails', async () => {
    transitionCase.mockRejectedValue({ response: { status: 403, data: { detail: 'Viewers cannot change test cases' } } })
    getCaseFresh.mockRejectedValue(new Error('down'))
    const { toolbar, onChanged } = setup()
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Make active' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Viewers cannot change test cases')
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('an edited-after-approval case says so and offers Request review first', () => {
    const caseItem = managedCase({
      status: 'active', version: 7, review_required: true, edited_after_approval: true, edited_since_approval_version: 6,
      allowed_actions: ['flag_stale', 'deprecate', 'request_review'], reason_required_actions: ['flag_stale', 'deprecate'],
    })
    const { toolbar } = setup(caseItem)
    const line = document.querySelector('[data-edited-after-approval]') as HTMLElement
    expect(line).toHaveTextContent('Edited after approval (since v6; now v7).')
    expect(line).toHaveTextContent('Request review moves it to Review requested until a reviewer approves it; plans that already hold it keep it.')
    expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Request review', 'Needs update…', 'Deprecate…', 'Edit',
    ])
  })

  it('shows no edited-after-approval line with review off (a marker left from review on is hidden)', () => {
    setup(managedCase({ status: 'active', review_required: false, edited_after_approval: true, edited_since_approval_version: 2 }))
    expect(document.querySelector('[data-edited-after-approval]')).toBeNull()
  })

  it('offers no retire move below QA lead (the AMENDMENT)', () => {
    const { toolbar } = setup(managedCase({ allowed_actions: ['activate', 'deprecate', 'archive', 'reinstate'] }), { canRetire: false })
    expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Make active', 'Edit'])
  })
})
