import { describe, expect, it } from 'vitest'

import type { ManagedTestCase, TestCaseTransitionAction } from '@/types/test-management'
import {
  ACTION_LABELS,
  moveTitle,
  REASON_ACTIONS,
  REVIEW_NOTES_ACTIONS,
  STATUS_MOVE_LABELS,
  STATUS_MOVE_ORDER,
  statusLabel,
  statusMoves,
  transitionErrorMessage,
} from './caseStatusModel'

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
    version: 1,
    is_automated: false,
    automation_status: 'not_automated',
    ai_generated: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    author_id: 'author-1',
    ...overrides,
  }
}

const EVERY: TestCaseTransitionAction[] = [
  'request_review', 'claim_review', 'withdraw_review', 'unclaim', 'approve', 'reject', 'request_changes',
  'activate', 'flag_stale', 'revise', 'deprecate', 'reinstate', 'archive',
]

describe('the moved lifecycle helpers', () => {
  it('keeps the Lifecycle tab labels and reason sets verbatim', () => {
    expect(ACTION_LABELS.activate).toBe('Activate')
    expect(ACTION_LABELS.unclaim).toBe('Unclaim review')
    expect(ACTION_LABELS.flag_stale).toBe('Flag stale')
    expect([...REASON_ACTIONS].sort()).toEqual(['archive', 'deprecate', 'flag_stale', 'reinstate'])
    expect([...REVIEW_NOTES_ACTIONS].sort()).toEqual(['approve', 'reject', 'request_changes'])
  })

  it('reads a refusal the way the Lifecycle tab did', () => {
    expect(transitionErrorMessage({ response: { data: { detail: 'Viewers cannot change test cases' } } }))
      .toBe('Viewers cannot change test cases')
    expect(transitionErrorMessage({ response: { data: { detail: { message: 'Stale version' } } } })).toBe('Stale version')
    expect(transitionErrorMessage({ response: { data: { detail: { current_state: 'needs_update', attempted_action: 'activate' } } } }))
      .toBe('Activate is no longer allowed from needs update.')
    expect(transitionErrorMessage(new Error('x'))).toBe('The lifecycle action could not be completed.')
  })
})

describe('statusMoves', () => {
  it('offers allowed_actions ∩ the move order, in that order, with outcome labels', () => {
    const moves = statusMoves(managedCase({ allowed_actions: EVERY }), 'author-1')
    expect(moves.map((m) => m.action)).toEqual([...STATUS_MOVE_ORDER])
    expect(moves.map((m) => m.label)).toEqual([
      'Make active', 'Back to draft', 'Request review', 'Withdraw review', 'Approve my case…',
      'Needs update…', 'Deprecate…', 'Archive…', 'Reinstate…',
    ])
    // claim / unclaim / reject / request changes stay on the Lifecycle and Approvals tabs.
    expect(moves.map((m) => m.action)).not.toContain('claim_review')
    expect(moves.map((m) => m.action)).not.toContain('reject')
  })

  it('keeps the server order out of it: the move order wins', () => {
    const moves = statusMoves(managedCase({ allowed_actions: ['deprecate', 'request_review', 'activate'] }), null)
    expect(moves.map((m) => m.action)).toEqual(['activate', 'request_review', 'deprecate'])
  })

  it('offers Approve my case… only to the case author', () => {
    const lead = managedCase({ allowed_actions: ['approve', 'withdraw_review'], status: 'review_requested' })
    expect(statusMoves(lead, 'author-1').map((m) => m.action)).toEqual(['withdraw_review', 'approve'])
    expect(statusMoves(lead, 'claimant-9').map((m) => m.action)).toEqual(['withdraw_review'])
    expect(statusMoves(lead, null).map((m) => m.action)).toEqual(['withdraw_review'])
    expect(statusMoves({ ...lead, author_id: undefined }, 'author-1').map((m) => m.action)).toEqual(['withdraw_review'])
  })

  it('reads needsReason from reason_required_actions when the server sends it', () => {
    const moves = statusMoves(
      managedCase({ allowed_actions: ['activate', 'flag_stale', 'deprecate'], reason_required_actions: ['deprecate'] }),
      null,
    )
    expect(moves.map((m) => [m.action, m.needsReason])).toEqual([
      ['activate', false],
      ['flag_stale', false],
      ['deprecate', true],
    ])
  })

  it('falls back to REASON_ACTIONS for an older server, and a self-approval always asks', () => {
    const moves = statusMoves(
      managedCase({ allowed_actions: ['activate', 'approve', 'flag_stale', 'deprecate', 'archive', 'reinstate'] }),
      'author-1',
    )
    expect(Object.fromEntries(moves.map((m) => [m.action, m.needsReason]))).toEqual({
      activate: false,
      approve: true,
      flag_stale: true,
      deprecate: true,
      archive: true,
      reinstate: true,
    })
    const withList = statusMoves(managedCase({ allowed_actions: ['approve'], reason_required_actions: [] }), 'author-1')
    expect(withList[0].needsReason).toBe(true)
  })

  it('leaves the retire moves out below QA lead (the AMENDMENT)', () => {
    const caseItem = managedCase({ allowed_actions: ['activate', 'deprecate', 'archive', 'reinstate', 'flag_stale'] })
    expect(statusMoves(caseItem, null, { canRetire: false }).map((m) => m.action)).toEqual(['activate', 'flag_stale'])
    expect(statusMoves(caseItem, null, { canRetire: true }).map((m) => m.action))
      .toEqual(['activate', 'flag_stale', 'deprecate', 'archive', 'reinstate'])
  })

  it('offers nothing without allowed_actions', () => {
    expect(statusMoves(managedCase(), 'author-1')).toEqual([])
  })

  it('labels every move', () => {
    for (const action of STATUS_MOVE_ORDER) expect(STATUS_MOVE_LABELS[action]).toBeTruthy()
    expect(moveTitle('Approve my case…')).toBe('Approve my case')
    expect(moveTitle('Make active')).toBe('Make active')
    expect(statusLabel('active')).toBe('Active')
    expect(statusLabel('review_requested')).toBe('Review requested')
  })
})
