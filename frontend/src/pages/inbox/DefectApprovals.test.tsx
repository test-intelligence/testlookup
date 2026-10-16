/**
 * W06 DEF-06 §2.6-9/10: Inbox › Approvals › Defects. A QA lead sees each
 * pending defect with why it waits and where its ticket would go; Approve
 * calls the review once and toasts what the Jira filing did, the row leaves
 * and the queue revalidates; Reject needs a reason (trimmed). Anyone else
 * renders nothing and never requests the queue.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DefectApprovalResponse, PendingDefect } from '@/types/defect-promotion'

const state = vi.hoisted(() => ({ isQaLead: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaLead: state.isQaLead }) }))

const service = vi.hoisted(() => ({ listPendingDefects: vi.fn(), reviewDefect: vi.fn() }))
vi.mock('@/services/defectPromotionService', () => ({ defectPromotionService: service }))

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import DefectApprovals from './DefectApprovals'

const DAY = 86_400_000

function pending(id: string, extra: Partial<PendingDefect> = {}): PendingDefect {
  return {
    defect_id: id, project_id: 'p-1', title: `Checkout fails ${id}`, severity: 'CRITICAL', component: 'checkout',
    owner_team: 'payments', created_at: new Date(Date.now() - 3 * DAY - 60_000).toISOString(),
    policy_reasons: ['CRITICAL severity needs a QA lead', 'A Jira project key was given'],
    jira_project_key: 'QA', run_id: 'run-1', cluster_id: 'c-1', ai_confidence_score: 90, ...extra,
  }
}

const list = (items: PendingDefect[], total = items.length) => ({ items, total, page: 1, page_size: 100 })

const approval = (extra: Partial<DefectApprovalResponse>): DefectApprovalResponse => ({
  defect_id: 'd-1', approval_status: 'approved', message: 'Defect approved successfully.',
  jira_ticket: null, jira_url: null, ...extra,
})

function renderQueue() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <DefectApprovals projectId="p-1" />
      </MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  state.isQaLead = true
  service.listPendingDefects.mockReset()
  service.reviewDefect.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
  service.listPendingDefects.mockResolvedValue(list([pending('d-1')]))
})

describe('DefectApprovals', () => {
  it('lists each pending defect with why it waits, its Jira project, age and run', async () => {
    renderQueue()
    const row = await screen.findByTestId('defect-approval-row')
    expect(service.listPendingDefects).toHaveBeenCalledWith('p-1', 1, 100)
    expect(within(row).getByText('Checkout fails d-1')).toBeInTheDocument()
    expect(within(row).getByText('CRITICAL')).toBeInTheDocument()
    expect(within(row).getByText('checkout · payments')).toBeInTheDocument()
    expect(within(row).getByText('Waiting because:')).toBeInTheDocument()
    expect(within(row).getAllByRole('listitem').map((li) => li.textContent))
      .toEqual(['CRITICAL severity needs a QA lead', 'A Jira project key was given'])
    expect(within(row).getByText('Jira project QA')).toBeInTheDocument()
    expect(within(row).getByText('created 3d ago')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Open run' })).toHaveAttribute('href', '/runs/run-1?tab=analysis')
  })

  it('says what approving does without a Jira project, and dashes blank fields', async () => {
    service.listPendingDefects.mockResolvedValue(list([
      pending('d-1', { jira_project_key: null, component: null, owner_team: '  ', run_id: null }),
    ]))
    renderQueue()
    const row = await screen.findByTestId('defect-approval-row')
    expect(within(row).getByText('No Jira project: approving keeps it in TestLookup')).toBeInTheDocument()
    expect(within(row).getByText('— · —')).toBeInTheDocument()
    expect(within(row).queryByRole('link', { name: 'Open run' })).toBeNull()
  })

  it('renders titles and reasons as text, never as HTML', async () => {
    service.listPendingDefects.mockResolvedValue(list([
      pending('d-1', { title: '<img src=x onerror=alert(1)>', policy_reasons: ['<b>bold</b>'] }),
    ]))
    const { container } = renderQueue()
    const row = await screen.findByTestId('defect-approval-row')
    expect(within(row).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(within(row).getByText('<b>bold</b>')).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
  })

  it('shows the empty state when nothing waits', async () => {
    service.listPendingDefects.mockResolvedValue(list([]))
    renderQueue()
    expect(await screen.findByText('No defects are waiting for approval.')).toBeInTheDocument()
  })

  it.each([
    [{ jira_status: 'filed', jira_ticket: { key: 'QA-12' }, jira_url: 'https://jira.example.test/browse/QA-12' }, 'success', 'Approved — Jira QA-12 filed'],
    [{ jira_status: 'linked', jira_url: 'https://jira.example.test/browse/QA-7' }, 'success', 'Approved — linked to the open Jira issue QA-7'],
    [{ jira_status: 'not_filed', jira_reason: 'offline_mode' }, 'success', 'Approved — Jira not filed (offline mode)'],
    [{ jira_status: 'not_filed', jira_reason: 'disabled' }, 'success', 'Approved — Jira not filed (Jira integration disabled)'],
    [{ jira_status: 'not_filed', jira_reason: 'not_configured' }, 'success', 'Approved — Jira not filed (Jira not configured)'],
    [{ jira_status: 'failed', jira_error: 'Jira answered HTTP 400.' }, 'error', 'Approved — Jira filing failed: Jira answered HTTP 400.'],
    [{ jira_status: 'unknown', jira_error: 'Jira did not confirm the issue.' }, 'error', 'Approved — Jira filing failed: Jira did not confirm the issue.'],
    [{ jira_status: 'not_requested' }, 'success', 'Approved'],
  ] as const)('approves once and toasts %o', async (answer, kind, text) => {
    service.reviewDefect.mockResolvedValue(approval(answer))
    renderQueue()
    await screen.findByTestId('defect-approval-row')
    service.listPendingDefects.mockResolvedValue(list([]))
    await act(async () => {
      fireEvent.click(screen.getByTestId('defect-approve'))
    })
    expect(service.reviewDefect).toHaveBeenCalledTimes(1)
    expect(service.reviewDefect).toHaveBeenCalledWith('d-1', { action: 'approve' })
    if (kind === 'success') {
      expect(toast.success).toHaveBeenCalledWith(text)
      expect(toast.error).not.toHaveBeenCalled()
    } else {
      expect(toast.error).toHaveBeenCalledWith(text)
    }
    expect(screen.queryByTestId('defect-approval-row')).toBeNull()
    await waitFor(() => expect(service.listPendingDefects).toHaveBeenCalledTimes(2))
  })

  it('keeps the row when the approval fails (the interceptor showed why)', async () => {
    service.reviewDefect.mockRejectedValue({ response: { status: 404 } })
    renderQueue()
    await screen.findByTestId('defect-approval-row')
    await act(async () => {
      fireEvent.click(screen.getByTestId('defect-approve'))
    })
    expect(screen.getByTestId('defect-approval-row')).toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByTestId('defect-approve')).toBeEnabled()
  })

  it('rejects with a trimmed reason, which it requires', async () => {
    service.reviewDefect.mockResolvedValue(approval({ approval_status: 'rejected' }))
    renderQueue()
    await screen.findByTestId('defect-approval-row')
    fireEvent.click(screen.getByTestId('defect-reject'))
    const dialog = screen.getByRole('dialog', { name: 'Reject this defect?' })
    expect(within(dialog).getByText('It is closed as Invalid and stops counting toward the release gate.')).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: 'Reject' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Reason \(recorded in the activity log\)/), { target: { value: '   ' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Reason \(recorded in the activity log\)/), { target: { value: '  Not a product bug  ' } })
    expect(confirm).toBeEnabled()
    await act(async () => {
      fireEvent.click(confirm)
    })
    expect(service.reviewDefect).toHaveBeenCalledWith('d-1', { action: 'reject', reason: 'Not a product bug' })
    expect(toast.success).toHaveBeenCalledWith('Rejected — closed as Invalid')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByTestId('defect-approval-row')).toBeNull()
  })

  it('renders nothing and never requests the queue for anyone but a QA lead', async () => {
    state.isQaLead = false
    const { container } = renderQueue()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(container).toBeEmptyDOMElement()
    expect(service.listPendingDefects).not.toHaveBeenCalled()
  })
})
