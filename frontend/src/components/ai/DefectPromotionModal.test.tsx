/**
 * W06 DEF-06 §2.6: a promotion that waits for approval says where it is
 * approved. A QA lead gets a link straight to Inbox › Approvals › Defects
 * (`/my-failures?tab=approvals&source=defects`: the Inbox route); anyone else
 * is told a QA lead approves it there (they cannot open that queue).
 *
 * W27a PAN-03: the form renders in the page, inside the failure block that
 * opened it (a region named "Promote to Defect"), and reopening it for the
 * same cluster shows the cached candidate again.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DefectCandidateResponse, DefectPromotionResponse } from '@/types/defect-promotion'

const state = vi.hoisted(() => ({ role: 'QA_LEAD' }))
// The REAL usePermissions, driven by the signed-in user's role.
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (s: { user: { role: string } }) => unknown) => selector({ user: { role: state.role } }),
}))

const service = vi.hoisted(() => ({ getCandidate: vi.fn(), promote: vi.fn() }))
vi.mock('@/services/defectPromotionService', () => ({ defectPromotionService: service }))

import DefectPromotionModal from './DefectPromotionModal'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'

const CANDIDATE: DefectCandidateResponse = {
  cluster_id: 'c-1', run_id: 'r-1', title: 'NPE in checkout', description: 'What failed…', severity: 'CRITICAL',
  component: 'checkout', owner_team: 'payments', labels: ['regression'], duplicate_hint: '', duplicate_detected: false,
  duplicate_defect_id: null, criticality_scores: {}, composite_score: 0,
  evidence_bundle: { stack_traces: [], log_anomalies: [], data_sources: [] }, failure_category: 'PRODUCT_BUG', member_count: 3,
}

const result = (approval: string): DefectPromotionResponse => ({
  defect_id: 'd-1', cluster_id: 'c-1', severity: 'CRITICAL', title: 'NPE in checkout', owner_team: 'payments',
  component: 'checkout', duplicate_detected: false, duplicate_defect_id: null, jira_ticket: null, jira_url: null,
  approval_status: approval, requires_approval: approval === 'pending_review', policy_reasons: ['CRITICAL severity'],
})

async function promote(approval = 'pending_review') {
  service.promote.mockResolvedValue(result(approval))
  const onClose = vi.fn()
  const onSuccess = vi.fn()
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <DefectPromotionModal runId="r-1" clusterId="c-1" clusterLabel="NPE cluster" onClose={onClose} onSuccess={onSuccess} />
      </MemoryRouter>
    </SWRConfig>,
  )
  await screen.findByDisplayValue('NPE in checkout')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Promote to Defect' }))
  })
  return { onClose, onSuccess }
}

beforeEach(() => {
  state.role = 'QA_LEAD'
  service.getCandidate.mockReset()
  service.promote.mockReset()
  service.getCandidate.mockResolvedValue(CANDIDATE)
})

describe('DefectPromotionModal — awaiting approval', () => {
  it.each(['QA_LEAD', 'ADMIN'])('links a %s to Inbox › Approvals › Defects', async (role) => {
    state.role = role
    const { onClose } = await promote()
    expect(screen.getByText('Defect created — awaiting approval')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Review it in Inbox › Approvals' })
    expect(link).toHaveAttribute('href', '/my-failures?tab=approvals&source=defects')
    expect(screen.queryByText('A QA lead approves it in Inbox › Approvals.')).toBeNull()
    fireEvent.click(link)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it.each(['QA_ENGINEER', 'TESTER', 'VIEWER'])('tells a %s that a QA lead approves it, with no link', async (role) => {
    state.role = role
    await promote()
    expect(screen.getByText('A QA lead approves it in Inbox › Approvals.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Review it in Inbox › Approvals' })).toBeNull()
  })

  it("hands the host the server's approval status, so a pending promotion is not closed away", async () => {
    const pending = await promote()
    expect(pending.onSuccess).toHaveBeenCalledWith('d-1', null, 'pending_review')
  })

  it('says nothing about approval for a promotion that took effect at once', async () => {
    await promote('approved')
    expect(screen.getByText('Defect promoted successfully')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Review it in Inbox › Approvals' })).toBeNull()
    expect(screen.queryByText('A QA lead approves it in Inbox › Approvals.')).toBeNull()
  })
})

// ── W27a PAN-03: the form renders in the page, inside its host ──────────────

function Host({ slot }: { slot?: boolean }) {
  const [open, setOpen] = useState(true)
  const formSlot = useInlineSlot<string>()
  return (
    <div data-testid="failure-block">
      <button type="button" onClick={() => (slot ? formSlot.open('c-1') : setOpen(true))}>File defect</button>
      {slot && <button type="button" onClick={() => formSlot.open('c-2')}>File another</button>}
      {(slot ? formSlot.openKey === 'c-1' : open) && (
        <DefectPromotionModal
          runId="r-1"
          clusterId="c-1"
          clusterLabel="NPE cluster"
          slot={slot ? formSlot : undefined}
          onClose={() => (slot ? formSlot.close() : setOpen(false))}
        />
      )}
    </div>
  )
}

function renderHost(slot = false) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <Host slot={slot} />
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('DefectPromotionModal — in the page (W27a PAN-03)', () => {
  it('renders inside its host as a region named "Promote to Defect" (no portal, no modal), the title focused', async () => {
    renderHost()
    const form = screen.getByRole('region', { name: 'Promote to Defect' })
    expect(screen.getByTestId('failure-block').contains(form)).toBe(true)
    expect(form.querySelector('#defect-promotion-title')).toHaveTextContent('Promote to Defect')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.getByLabelText(/^Title/)).toHaveFocus()
    await screen.findByDisplayValue('NPE in checkout')
  })

  it('a pending_review answer keeps the form mounted, with its result and the approvals link', async () => {
    service.promote.mockResolvedValue(result('pending_review'))
    renderHost()
    await screen.findByDisplayValue('NPE in checkout')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Promote to Defect' }))
    })
    const form = screen.getByRole('region', { name: 'Promote to Defect' })
    expect(within(form).getByText('Defect created — awaiting approval')).toBeInTheDocument()
    expect(within(form).getByRole('link', { name: 'Review it in Inbox › Approvals' })).toHaveAttribute('href', '/my-failures?tab=approvals&source=defects')
    expect(screen.getByTestId('failure-block').contains(form)).toBe(true)
  })

  it('Escape inside closes it; File defect again shows the candidate again (it once reopened empty)', async () => {
    renderHost()
    await screen.findByDisplayValue('NPE in checkout')
    fireEvent.keyDown(screen.getByLabelText(/^Title/), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Promote to Defect' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'File defect' }))
    const form = screen.getByRole('region', { name: 'Promote to Defect' })
    // The cached candidate, at once: no empty form, no second read.
    expect(within(form).getByLabelText(/^Title/)).toHaveValue('NPE in checkout')
    expect(within(form).getByLabelText('Component')).toHaveValue('checkout')
    expect(within(form).getByLabelText('Owner Team')).toHaveValue('payments')
    expect(within(form).getByLabelText(/^Labels/)).toHaveValue('regression')
    expect(within(form).getByLabelText('Description')).toHaveValue('What failed…')
    expect(within(form).getByRole('group', { name: 'Severity' })).toBeInTheDocument()
  })

  it('typed input asks before another form replaces it; a stored promotion leaves nothing to lose', async () => {
    service.promote.mockResolvedValue(result('pending_review'))
    renderHost(true)
    fireEvent.click(screen.getByRole('button', { name: 'File defect' }))
    await screen.findByDisplayValue('NPE in checkout')
    fireEvent.change(screen.getByLabelText(/^Title/), { target: { value: 'NPE in checkout v2' } })
    // Typed into: another cluster's File defect asks first, and Keep editing keeps the text.
    fireEvent.click(screen.getByRole('button', { name: 'File another' }))
    expect(screen.getByText(/Discard your changes to/)).toHaveTextContent('Discard your changes to "Promote to Defect"?')
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText(/^Title/)).toHaveValue('NPE in checkout v2')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Promote to Defect' }))
    })
    expect(screen.getByText('Defect created — awaiting approval')).toBeInTheDocument()
    // Stored: the next form replaces this one at once.
    fireEvent.click(screen.getByRole('button', { name: 'File another' }))
    expect(screen.queryByText(/Discard your changes/)).toBeNull()
    expect(screen.queryByRole('region', { name: 'Promote to Defect' })).toBeNull()
  })
})
