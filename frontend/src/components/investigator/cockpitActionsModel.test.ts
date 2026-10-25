/**
 * The cockpit's Proposed-actions model (W15 AGT-02): matching a typed action
 * to its ledger row, the state label per outcome, the target label, and how a
 * QA lead does it by hand.
 */
import { describe, expect, it } from 'vitest'

import type { InvestigationProposal, StructuredAction } from '@/types/investigator'
import {
  NOT_PROPOSED,
  REVIEW_HREF,
  SHADOW_NOT_PROPOSED,
  cockpitActionRows,
  doItMyself,
  matchProposal,
  stateLabel,
  targetLabel,
} from './cockpitActionsModel'

const QUARANTINE: StructuredAction = {
  action_type: 'quarantine.apply',
  target_type: 'test',
  target_id: 'fpA1b2C3d4',
  title: 'Quarantine checkout-retry',
  payload: { test_fingerprint: 'fpA1b2C3d4', test_name: 'checkout-retry', suite_name: 'checkout' },
}
const REASSIGN: StructuredAction = {
  action_type: 'failure.reassign',
  target_type: 'test_case',
  target_id: 'tc-1',
  title: 'Reassign checkout-total to the checkout owner',
  payload: { test_case_id: 'tc-1', suite_name: 'checkout' },
}
const DEFECT: StructuredAction = {
  action_type: 'defect.promote',
  target_type: 'cluster',
  target_id: 'clA_checkout',
  title: 'Promote a defect for cluster NullPointer in checkout',
  payload: { cluster_id: 'clA_checkout', cluster_label: 'NullPointer in checkout' },
}

function proposal(over: Partial<InvestigationProposal> = {}): InvestigationProposal {
  return {
    id: 'act-1',
    action_type: 'quarantine.apply',
    canonical_type: 'quarantine.apply',
    target_type: 'test',
    target_id: 'fpA1b2C3d4',
    status: 'pending_review',
    error_code: null,
    outcome: 'awaiting_approval',
    outcome_text: "Waiting for a QA lead's approval.",
    idempotency_key: 'inv:1:quarantine.apply:abc',
    title: 'Quarantine checkout-retry',
    created_at: '2026-10-25T10:00:00Z',
    ...over,
  }
}

describe('matchProposal', () => {
  it('matches on the action type and the target id, newest first', () => {
    const newest = proposal({ id: 'new' })
    const older = proposal({ id: 'old', status: 'rejected', error_code: 'superseded', outcome: 'superseded' })
    const otherTarget = proposal({ id: 'other', target_id: 'fpZ9y8X7w6' })
    const otherType = proposal({ id: 'type', action_type: 'failure.reassign', canonical_type: 'failure.reassign' })
    expect(matchProposal(QUARANTINE, [otherTarget, otherType, newest, older])?.id).toBe('new')
    expect(matchProposal(QUARANTINE, [otherTarget, otherType])).toBeNull()
    expect(matchProposal(QUARANTINE, undefined)).toBeNull()
  })

  it('falls back to the raw action type when the row has no canonical type', () => {
    expect(matchProposal(QUARANTINE, [proposal({ canonical_type: null })])?.id).toBe('act-1')
  })
})

describe('stateLabel', () => {
  it('names the shadow mode when nothing was proposed', () => {
    expect(stateLabel(null, 'shadow')).toEqual({ label: SHADOW_NOT_PROPOSED, tone: 'muted', pending: false })
    const missing = stateLabel(null, 'suggest')
    expect(missing.label).toBe(NOT_PROPOSED)
    expect(missing.title).toMatch(/already done/)
  })

  it.each([
    [{ outcome: 'awaiting_approval' }, 'Waiting for approval', 'info', true],
    [{ status: 'approved', outcome: 'approved_queued' }, 'Approved · running', 'info', false],
    [{ status: 'executed', outcome: 'executed' }, 'Executed', 'success', false],
    [{ status: 'executed', outcome: 'acknowledged' }, 'Acknowledged', 'success', false],
    [{ status: 'rolled_back', outcome: 'rolled_back' }, 'Undone', 'muted', false],
    [{ status: 'rejected', outcome: 'rejected' }, 'Rejected', 'muted', false],
    [{ status: 'rejected', error_code: 'superseded', outcome: 'superseded' }, 'Replaced by a newer proposal', 'muted', false],
    [{ status: 'failed', error_code: 'act_mode_required', outcome: 'not_executed_policy' }, 'Not executed', 'warning', false],
    [{ status: 'failed', error_code: 'outcome_unknown', outcome: 'outcome_unknown' }, 'Outcome unknown', 'warning', false],
  ] as const)('%o → %s', (over, label, tone, pending) => {
    const state = stateLabel(proposal(over as Partial<InvestigationProposal>), 'suggest')
    expect(state).toMatchObject({ label, tone, pending })
  })

  it("keeps the server's outcome sentence as the chip's title", () => {
    const state = stateLabel(proposal({ status: 'failed', outcome: 'not_executed_refused', outcome_text: 'Not executed: The test is already quarantined.' }), 'act')
    expect(state.title).toBe('Not executed: The test is already quarantined.')
  })

  it('reads a superseded row from an old server without an outcome', () => {
    expect(stateLabel(proposal({ status: 'rejected', error_code: 'superseded', outcome: 'unknown' }), 'suggest').label)
      .toBe('Replaced by a newer proposal')
    expect(stateLabel(proposal({ outcome: '' }), 'suggest').label).toBe('Waiting for approval')
  })
})

describe('targetLabel and doItMyself', () => {
  it('labels the target from typed fields only', () => {
    expect(targetLabel(QUARANTINE)).toBe('test in checkout')
    expect(targetLabel(REASSIGN)).toBe('failure in checkout')
    expect(targetLabel(DEFECT)).toBe('cluster NullPointer in checkout')
    expect(targetLabel({ ...DEFECT, payload: {} })).toBe('cluster clA_checkout')
  })

  it('opens the existing quarantine form, or links to the Run page that does the job', () => {
    expect(doItMyself(QUARANTINE, 'run-1')).toEqual({
      kind: 'form',
      prefill: { test_fingerprint: 'fpA1b2C3d4', test_name: 'checkout-retry', suite_name: 'checkout' },
    })
    expect(doItMyself(REASSIGN, 'run-1')).toEqual({ kind: 'link', label: 'Open the failure', href: '/runs/run-1?tab=tests' })
    expect(doItMyself(DEFECT, 'run-1')).toEqual({ kind: 'link', label: 'Open the clusters', href: '/runs/run-1?tab=analysis' })
    expect(doItMyself({ ...QUARANTINE, action_type: 'decision.acknowledge' }, 'run-1')).toBeNull()
  })

  it('builds links from ids only, encoded', () => {
    const manual = doItMyself(REASSIGN, 'run/../x?y')
    expect(manual).toEqual({ kind: 'link', label: 'Open the failure', href: '/runs/run%2F..%2Fx%3Fy?tab=tests' })
  })
})

describe('cockpitActionRows', () => {
  it('renders nothing without typed actions or on an old detail without proposals', () => {
    const ctx = { mode: 'suggest', runId: 'run-1', isQaLead: true }
    expect(cockpitActionRows(undefined, [], ctx)).toBeNull()
    expect(cockpitActionRows([], [], ctx)).toBeNull()
    expect(cockpitActionRows([QUARANTINE], undefined, ctx)).toBeNull()
  })

  it('offers Review and Do it myself to a QA lead on a pending row only', () => {
    const rows = cockpitActionRows(
      [QUARANTINE, REASSIGN],
      [proposal(), proposal({ id: 'act-2', action_type: 'failure.reassign', canonical_type: 'failure.reassign', target_id: 'tc-1', status: 'executed', outcome: 'executed' })],
      { mode: 'suggest', runId: 'run-1', isQaLead: true },
    ) ?? []
    expect(rows.map((r) => r.state.label)).toEqual(['Waiting for approval', 'Executed'])
    expect(rows[0]).toMatchObject({ canReview: true, doItMyself: { kind: 'form' } })
    expect(rows[1]).toMatchObject({ canReview: false, doItMyself: null })
    expect(REVIEW_HREF).toBe('/my-failures?tab=approvals&source=actions')
  })

  it('offers Do it myself on a shadow action, and nothing to a QA engineer', () => {
    const shadow = cockpitActionRows([DEFECT], [], { mode: 'shadow', runId: 'run-1', isQaLead: true }) ?? []
    expect(shadow[0]).toMatchObject({ canReview: false, doItMyself: { kind: 'link', label: 'Open the clusters' } })
    const engineer = cockpitActionRows([QUARANTINE], [proposal()], { mode: 'suggest', runId: 'run-1', isQaLead: false }) ?? []
    expect(engineer[0]).toMatchObject({ canReview: false, doItMyself: null })
  })
})
