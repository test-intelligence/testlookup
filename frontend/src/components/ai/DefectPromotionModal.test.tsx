/**
 * W06 DEF-06 §2.6: a promotion that waits for approval says where it is
 * approved. A QA lead gets a link straight to Inbox › Approvals › Defects
 * (`/my-failures?tab=approvals&source=defects`: the Inbox route); anyone else
 * is told a QA lead approves it there (they cannot open that queue).
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
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
