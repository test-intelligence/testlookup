import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAllowedTestCaseTransitions } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase } from '@/types/test-management'
import LifecyclePanel from './LifecyclePanel'

vi.mock('@/hooks/useTestManagement', () => ({
  useAllowedTestCaseTransitions: vi.fn(),
}))

vi.mock('@/services/testManagementService', () => ({
  testManagementService: { transitionCase: vi.fn() },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

function managedCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'case-1',
    project_id: 'project-1',
    title: 'Sign in',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    status: 'active',
    version: 2,
    is_automated: false,
    automation_status: 'manual',
    ai_generated: false,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

describe('LifecyclePanel', () => {
  const mutate = vi.fn().mockResolvedValue(undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    ;(useAllowedTestCaseTransitions as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        { action: 'deprecate', allowed: true },
        { action: 'archive', allowed: false, blocked_reason: 'Deprecate the case first.' },
      ],
      isLoading: false,
      error: undefined,
      mutate,
    })
  })

  it('renders server decisions and requires a trimmed reason for deprecation', async () => {
    const updated = managedCase({ status: 'deprecated' })
    ;(testManagementService.transitionCase as ReturnType<typeof vi.fn>).mockResolvedValue(updated)
    const onChanged = vi.fn()

    render(<LifecyclePanel caseItem={managedCase()} onChanged={onChanged} />)

    expect(screen.getByLabelText('Test case lifecycle')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Archive' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Archive' })).toHaveAttribute('title', 'Deprecate the case first.')

    fireEvent.click(screen.getByRole('button', { name: 'Deprecate' }))
    const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: 'Deprecate' })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: '  Superseded by case 42  ' } })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(testManagementService.transitionCase).toHaveBeenCalledWith('case-1', {
        action: 'deprecate',
        reason: 'Superseded by case 42',
      })
    })
    expect(onChanged).toHaveBeenCalledWith(updated)
    expect(mutate).toHaveBeenCalled()
  })

  it('posts a reasonless lifecycle action directly', async () => {
    const draft = managedCase({ status: 'draft' })
    const requested = managedCase({ status: 'review_requested' })
    ;(useAllowedTestCaseTransitions as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [{ action: 'request_review', allowed: true }],
      isLoading: false,
      mutate,
    })
    ;(testManagementService.transitionCase as ReturnType<typeof vi.fn>).mockResolvedValue(requested)

    render(<LifecyclePanel caseItem={draft} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Request review' }))

    await waitFor(() => {
      expect(testManagementService.transitionCase).toHaveBeenCalledWith('case-1', {
        action: 'request_review',
      })
    })
  })

  it('requires trimmed review notes and sends them as notes for an approval', async () => {
    const underReview = managedCase({ status: 'under_review' })
    const approved = managedCase({ status: 'approved' })
    ;(useAllowedTestCaseTransitions as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [{ action: 'approve', allowed: true }],
      isLoading: false,
      mutate,
    })
    ;(testManagementService.transitionCase as ReturnType<typeof vi.fn>).mockResolvedValue(approved)

    render(<LifecyclePanel caseItem={underReview} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    const dialog = screen.getByRole('dialog', { name: 'Approve' })
    const confirm = within(dialog).getByRole('button', { name: 'Approve' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Review notes/ }), {
      target: { value: '  Independently verified expected behavior  ' },
    })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(testManagementService.transitionCase).toHaveBeenCalledWith('case-1', {
        action: 'approve',
        notes: 'Independently verified expected behavior',
      })
    })
  })

  it('fails closed when stale allowed transitions are accompanied by an error', () => {
    ;(useAllowedTestCaseTransitions as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [{ action: 'deprecate', allowed: true }],
      error: new Error('unavailable'),
      isLoading: false,
      mutate,
    })

    render(
      <LifecyclePanel
        caseItem={managedCase({ allowed_actions: ['deprecate'] })}
        onChanged={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Deprecate' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry lifecycle actions' })).toBeInTheDocument()
  })

  it('treats a successful transition followed by refresh failure as saved but stale', async () => {
    const refresh = vi.fn().mockRejectedValue(new Error('refresh unavailable'))
    const updated = managedCase({ status: 'deprecated', deprecation_reason: 'Superseded' })
    ;(useAllowedTestCaseTransitions as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [{ action: 'deprecate', allowed: true }],
      error: undefined,
      isLoading: false,
      mutate: refresh,
    })
    ;(testManagementService.transitionCase as ReturnType<typeof vi.fn>).mockResolvedValue(updated)
    const onChanged = vi.fn()

    render(<LifecyclePanel caseItem={managedCase()} onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('button', { name: 'Deprecate' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Reason/ }), {
      target: { value: 'Superseded' },
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Deprecate' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('change was saved')
    expect(screen.queryByRole('button', { name: 'Deprecate' })).not.toBeInTheDocument()
    expect(testManagementService.transitionCase).toHaveBeenCalledTimes(1)
  })
})
