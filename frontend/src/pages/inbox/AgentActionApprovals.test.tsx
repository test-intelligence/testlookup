/**
 * W15 AGT-01 §2.1-5 / AGT-03 §2.3-10: Inbox › Approvals › Agent actions.
 * Waiting rows render agent text as TEXT; Approve is offered only when the
 * server says it would run, and asks in the row before it sends; Reject needs
 * a reason; a refusal is said in its row; Recently decided shows each outcome,
 * Undo only on executed + reversible rows, Running… while one runs, and an
 * unknown outcome with no retry control. Hooks mocked; no request is made.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentAction } from '@/types/agentActions'
import { APPROVE_BLOCKED, SECTION_COPY } from './agentActionModel'

const state = vi.hoisted(() => ({ isQaLead: true }))
const hooks = vi.hoisted(() => ({ pending: vi.fn(), decided: vi.fn(), decide: vi.fn() }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaLead: state.isQaLead }) }))
vi.mock('@/hooks/useAgentActions', () => ({
  usePendingAgentActions: hooks.pending,
  useDecidedAgentActions: hooks.decided,
  useDecideAgentAction: () => hooks.decide,
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import AgentActionApprovals from './AgentActionApprovals'

const XSS = '<img src=x onerror=alert(1)>'

function mk(over: Partial<AgentAction>): AgentAction {
  return {
    id: 'a1', project_id: 'p-1', test_run_id: 'run-1', pipeline_run_id: 'pipe-1',
    action_type: 'quarantine.apply', target_type: 'test', target_id: 'fpA1b2C3d4', status: 'pending_review',
    approval_required: true, idempotency_key: 'inv:1:quarantine.apply:abc', request_sha256: 'h', request_payload: {},
    canonical_type: 'quarantine.apply', title: 'Quarantine checkout-retry', rationale: 'Failing and known flaky.',
    risk: 'low', side_effect: 'internal', reversible: true, proposing_agent_id: 'investigator',
    proposing_agent_label: 'Investigator', proposing_review_state: 'accepted', test_run_build_number: '128',
    target: { kind: 'test', id: 'fpA1b2C3d4', label: 'checkout-retry', href: null },
    outcome: 'awaiting_approval', outcome_text: "Waiting for a QA lead's approval.", approvable: true,
    ...over,
  } as AgentAction
}

const QUARANTINE = mk({})
const BLOCKED = mk({
  id: 'a2', action_type: 'failure.reassign', canonical_type: 'failure.reassign', title: 'Reassign cart-total',
  proposing_review_state: 'pending_review', approvable: false, approve_blocked_reason: APPROVE_BLOCKED.reviewNotAccepted,
})
const ACK = mk({
  id: 'a3', action_type: 'decision_report_action', canonical_type: 'decision.acknowledge', title: 'Ship behind the flag',
  side_effect: 'none', reversible: false, proposing_agent_label: 'Decision report', target: { kind: 'release_item', label: 'ra-1' },
})

const page = (items: AgentAction[]) => ({
  data: { items, count: items.length, total: items.length }, error: undefined, isLoading: false, mutate: vi.fn(),
})

function renderSection(cache = new Map()) {
  return render(
    <SWRConfig value={{ provider: () => cache }}>
      <MemoryRouter>
        <AgentActionApprovals projectId="p-1" />
      </MemoryRouter>
    </SWRConfig>,
  )
}

const rows = () => screen.getAllByTestId('agent-action-row')
const rowOf = (id: string) => rows().find((r) => r.getAttribute('data-agent-action-id') === id) as HTMLElement

beforeEach(() => {
  state.isQaLead = true
  hooks.pending.mockReset().mockReturnValue(page([QUARANTINE, BLOCKED, ACK]))
  hooks.decided.mockReset().mockReturnValue(page([]))
  hooks.decide.mockReset().mockResolvedValue({})
  toast.success.mockReset()
  toast.error.mockReset()
})

describe('Waiting', () => {
  it('lists each pending action with its target, proposer, run and AI-report fact', () => {
    renderSection()
    expect(rows()).toHaveLength(3)
    const row = rowOf('a1')
    expect(within(row).getByText('Quarantine checkout-retry')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'checkout-retry' })).toHaveAttribute('href', '/runs/run-1?tab=tests')
    expect(within(row).getByRole('link', { name: 'Investigator · run #128' })).toHaveAttribute('href', '/runs/run-1')
    expect(within(row).getByText('AI report: accepted')).toBeInTheDocument()
    expect(within(row).getByText('low risk')).toBeInTheDocument()
    expect(within(rowOf('a2')).getByRole('link', { name: 'AI report: awaiting review' }))
      .toHaveAttribute('href', '/my-failures?tab=approvals&source=reports')
    expect(hooks.pending).toHaveBeenCalledWith('p-1', true)
  })

  it('renders agent-written titles, rationales and labels as text, never markup', () => {
    hooks.pending.mockReturnValue(page([mk({ title: XSS, rationale: XSS, target: { kind: 'test', label: XSS } })]))
    const { container } = renderSection()
    expect(container.querySelector('img')).toBeNull()
    expect(within(rows()[0]).getAllByText(XSS).length).toBeGreaterThanOrEqual(3)
  })

  it('disables Approve with the reason when the server says it would not run', () => {
    renderSection()
    const approve = within(rowOf('a2')).getByTestId('agent-action-approve')
    expect(approve).toBeDisabled()
    expect(approve).toHaveAttribute('title', APPROVE_BLOCKED.reviewNotAccepted)
    expect(within(rowOf('a1')).getByTestId('agent-action-approve')).toBeEnabled()
  })

  it('asks in the row before approving; only the confirm sends { status: "approved" }', async () => {
    renderSection()
    fireEvent.click(within(rowOf('a1')).getByTestId('agent-action-approve'))
    expect(hooks.decide).not.toHaveBeenCalled()
    const ask = screen.getByTestId('agent-action-approve-confirm')
    expect(ask).toHaveTextContent('Approve "Quarantine checkout-retry"? It runs within a minute and can be undone.')
    await act(async () => {
      fireEvent.click(within(ask).getByRole('button', { name: 'Approve' }))
    })
    expect(hooks.decide).toHaveBeenCalledWith('a1', { status: 'approved' })
    expect(screen.queryByText('Quarantine checkout-retry')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says an acknowledge is only recorded', () => {
    renderSection()
    fireEvent.click(within(rowOf('a3')).getByTestId('agent-action-approve'))
    expect(screen.getByTestId('agent-action-approve-confirm'))
      .toHaveTextContent('Approve "Ship behind the flag"? It is recorded as acknowledged.')
  })

  it('Reject opens an in-page form that needs a reason; the body carries the code (and notes only when typed)', async () => {
    renderSection()
    fireEvent.click(within(rowOf('a2')).getByTestId('agent-action-reject'))
    const form = screen.getByRole('region', { name: SECTION_COPY.rejectTitle })
    const send = within(form).getByTestId('agent-action-reject-confirm')
    expect(send).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Reason'), { target: { value: 'unsupported_claim' } })
    expect(send).toBeEnabled()
    await act(async () => {
      fireEvent.click(send)
    })
    expect(hooks.decide).toHaveBeenCalledWith('a2', { status: 'rejected', reason_code: 'unsupported_claim' })
    expect(screen.queryByRole('region', { name: SECTION_COPY.rejectTitle })).toBeNull()
    expect(rows().map((r) => r.getAttribute('data-agent-action-id'))).toEqual(['a1', 'a3'])
  })

  it('sends typed notes with the rejection', async () => {
    renderSection()
    fireEvent.click(within(rowOf('a1')).getByTestId('agent-action-reject'))
    const form = screen.getByRole('region', { name: SECTION_COPY.rejectTitle })
    fireEvent.change(within(form).getByLabelText('Reason'), { target: { value: 'other' } })
    fireEvent.change(within(form).getByLabelText('Notes (optional)'), { target: { value: '  wrong test  ' } })
    await act(async () => {
      fireEvent.click(within(form).getByTestId('agent-action-reject-confirm'))
    })
    expect(hooks.decide).toHaveBeenCalledWith('a1', { status: 'rejected', reason_code: 'other', notes: 'wrong test' })
  })

  it("shows a refusal's sentence in the row, never as a toast, and keeps the row", async () => {
    hooks.decide.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'act_mode_required', message: APPROVE_BLOCKED.actModeRequired } } },
    })
    renderSection()
    fireEvent.click(within(rowOf('a1')).getByTestId('agent-action-approve'))
    await act(async () => {
      fireEvent.click(within(screen.getByTestId('agent-action-approve-confirm')).getByRole('button', { name: 'Approve' }))
    })
    expect(screen.getByRole('alert')).toHaveTextContent(APPROVE_BLOCKED.actModeRequired)
    expect(rowOf('a1')).toBeInTheDocument()
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('Recently decided', () => {
  const EXECUTED = mk({
    id: 'd1', status: 'executed', outcome: 'executed', approvable: false,
    outcome_text: 'Executed · approved by Ana Lead · quarantined.',
    result_link: { kind: 'quarantine', id: 'q1', label: 'quarantined', href: '/flaky?tab=quarantined' },
  })
  const REJECTED = mk({ id: 'd2', status: 'rejected', outcome: 'rejected', outcome_text: 'Rejected by Ana Lead (other).' })
  const UNKNOWN = mk({
    id: 'd3', status: 'failed', error_code: 'outcome_unknown', outcome: 'outcome_unknown',
    outcome_text: 'The outcome is unknown: the run stopped while the action was executing. It is not retried — check checkout-retry and act on its own page.',
  })
  const RUNNING = mk({ id: 'd4', status: 'executing', outcome: 'approved_queued', outcome_text: 'Approved by Ana Lead · running…' })
  const ACKED = mk({ id: 'd5', status: 'executed', outcome: 'acknowledged', side_effect: 'none', reversible: false,
    canonical_type: 'decision.acknowledge', outcome_text: 'Acknowledged by Ana Lead.' })
  const decidedRow = (id: string) =>
    screen.getAllByTestId('agent-action-decided-row').find((r) => r.getAttribute('data-agent-action-id') === id) as HTMLElement

  it('is absent while nothing was decided', () => {
    renderSection()
    expect(screen.queryByText(SECTION_COPY.recentlyDecided)).toBeNull()
    expect(hooks.decided).toHaveBeenCalledWith('p-1', true)
  })

  it('shows each outcome; Undo and the result link only on the executed, reversible row', () => {
    hooks.decided.mockReturnValue(page([EXECUTED, REJECTED, UNKNOWN, RUNNING, ACKED]))
    renderSection()
    expect(screen.getByText(SECTION_COPY.recentlyDecided)).toBeInTheDocument()
    const executed = decidedRow('d1')
    expect(executed).toHaveTextContent('Executed · approved by Ana Lead · quarantined.')
    expect(within(executed).getByRole('link', { name: 'quarantined' })).toHaveAttribute('href', '/flaky?tab=quarantined')
    expect(within(executed).getByTestId('agent-action-undo')).toBeInTheDocument()
    for (const id of ['d2', 'd3', 'd4', 'd5']) expect(within(decidedRow(id)).queryByTestId('agent-action-undo')).toBeNull()
    expect(within(decidedRow('d4')).getByText('Running…')).toBeInTheDocument()
    expect(within(decidedRow('d5')).getByText('Acknowledged')).toBeInTheDocument()
  })

  it('says an unknown outcome with the target link and no retry control', () => {
    hooks.decided.mockReturnValue(page([UNKNOWN]))
    renderSection()
    const row = decidedRow('d3')
    expect(row).toHaveTextContent('It is not retried')
    expect(within(row).getByRole('link', { name: 'checkout-retry' })).toHaveAttribute('href', '/runs/run-1?tab=tests')
    expect(within(row).queryAllByRole('button')).toHaveLength(0)
  })

  it('Undo asks in the row, then sends { status: "rolled_back" }', async () => {
    hooks.decided.mockReturnValue(page([EXECUTED]))
    renderSection()
    fireEvent.click(within(decidedRow('d1')).getByTestId('agent-action-undo'))
    expect(hooks.decide).not.toHaveBeenCalled()
    const ask = screen.getByTestId('agent-action-undo-confirm')
    expect(ask).toHaveTextContent('Undo "Quarantine checkout-retry"? The quarantine is released.')
    await act(async () => {
      fireEvent.click(within(ask).getByRole('button', { name: 'Undo' }))
    })
    expect(hooks.decide).toHaveBeenCalledWith('d1', { status: 'rolled_back' })
  })
})

describe('the section around the queue', () => {
  it('renders nothing for anyone but a QA lead, and keys no request', () => {
    state.isQaLead = false
    const { container } = renderSection()
    expect(container).toBeEmptyDOMElement()
    expect(hooks.pending).toHaveBeenCalledWith('p-1', false)
    expect(hooks.decided).toHaveBeenCalledWith('p-1', false)
  })

  it('says nothing is waiting, and why when the cached policy is Shadow (no request for it)', () => {
    hooks.pending.mockReturnValue(page([]))
    const cache = new Map([['/projects/p-1/agent-configs/investigator', {
      data: { policies: [{ agent_id: 'investigator', mode: 'shadow' }] },
    }]])
    renderSection(cache)
    expect(screen.getByText(SECTION_COPY.emptyWaiting)).toBeInTheDocument()
    expect(screen.getByText(SECTION_COPY.shadowNote)).toBeInTheDocument()
  })

  it('without a cached policy says only that nothing is waiting', () => {
    hooks.pending.mockReturnValue(page([]))
    renderSection()
    expect(screen.getByText(SECTION_COPY.emptyWaiting)).toBeInTheDocument()
    expect(screen.queryByText(SECTION_COPY.shadowNote)).toBeNull()
  })

  it('a failed read says so with Retry, inside the section', () => {
    const mutate = vi.fn()
    hooks.pending.mockReturnValue({ data: undefined, error: new Error('500'), isLoading: false, mutate })
    renderSection()
    expect(screen.getByRole('alert')).toHaveTextContent(SECTION_COPY.loadError)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
  })
})
