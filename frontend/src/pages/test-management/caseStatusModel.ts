/**
 * The test-case lifecycle's labels and the Details toolbar's Status moves
 * (W28 TCM-02). Pure: no React, no request.
 *
 * ACTION_LABELS, REASON_ACTIONS, REVIEW_NOTES_ACTIONS and
 * transitionErrorMessage moved here VERBATIM from LifecyclePanel.tsx (the
 * Lifecycle tab keeps the full governed list with every blocked reason); the
 * Status moves are the subset of a case's allowed_actions the toolbar offers,
 * labelled by their outcome.
 */
import type { ManagedTestCase, TestCaseTransitionAction } from '@/types/test-management'

export const ACTION_LABELS: Record<TestCaseTransitionAction, string> = {
  request_review: 'Request review',
  claim_review: 'Claim review',
  withdraw_review: 'Withdraw review',
  unclaim: 'Unclaim review',
  approve: 'Approve',
  reject: 'Reject',
  request_changes: 'Request changes',
  activate: 'Activate',
  flag_stale: 'Flag stale',
  revise: 'Revise',
  deprecate: 'Deprecate',
  reinstate: 'Reinstate',
  archive: 'Archive',
}

export const REASON_ACTIONS: ReadonlySet<TestCaseTransitionAction> = new Set<TestCaseTransitionAction>([
  'deprecate',
  'reinstate',
  'archive',
  'flag_stale',
])

export const REVIEW_NOTES_ACTIONS: ReadonlySet<TestCaseTransitionAction> = new Set<TestCaseTransitionAction>([
  'approve',
  'reject',
  'request_changes',
])

export function transitionErrorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const value = detail as {
      current_state?: string
      attempted_action?: string
      message?: string
    }
    if (value.message) return value.message
    if (value.current_state && value.attempted_action) {
      return `${ACTION_LABELS[value.attempted_action as TestCaseTransitionAction] ?? value.attempted_action} is no longer allowed from ${value.current_state.replace(/_/g, ' ')}.`
    }
  }
  return 'The lifecycle action could not be completed.'
}

/** The Status moves (§2.2): the subset of allowed_actions the Details toolbar offers, in this order. */
export const STATUS_MOVE_ORDER = [
  'activate',
  'revise',
  'request_review',
  'withdraw_review',
  'approve',
  'flag_stale',
  'deprecate',
  'archive',
  'reinstate',
] as const

export type StatusMoveAction = (typeof STATUS_MOVE_ORDER)[number]

export const STATUS_MOVE_LABELS: Record<StatusMoveAction, string> = {
  activate: 'Make active',
  revise: 'Back to draft',
  request_review: 'Request review',
  withdraw_review: 'Withdraw review',
  approve: 'Approve my case…',
  flag_stale: 'Needs update…',
  deprecate: 'Deprecate…',
  archive: 'Archive…',
  reinstate: 'Reinstate…',
}

/**
 * The retire moves. They stay QA_LEAD or above (W28 contract AMENDMENT,
 * 2026-10-17, OR-04 as corrected): the toolbar and the bulk bar offer them to
 * QA leads and administrators only, whatever an answer lists.
 */
export const RETIRE_MOVES: ReadonlySet<TestCaseTransitionAction> = new Set<TestCaseTransitionAction>([
  'deprecate',
  'archive',
  'reinstate',
])

export interface StatusMove {
  action: TestCaseTransitionAction
  label: string
  needsReason: boolean
}

export interface StatusMoveOptions {
  /** false: the retire moves (Deprecate / Archive / Reinstate) are left out. Default true (the answer decides). */
  canRetire?: boolean
}

const MOVE_SET: ReadonlySet<string> = new Set<string>(STATUS_MOVE_ORDER)

/**
 * The toolbar's moves for this case and viewer: allowed_actions ∩
 * STATUS_MOVE_ORDER, in that order. `approve` only when the case's author is
 * the current user (a lead's self-approval: the server put it in
 * allowed_actions; a claimant's ordinary Approve stays on the Lifecycle and
 * Approvals tabs). needsReason: the answer's reason_required_actions when it
 * has one, else REASON_ACTIONS (an older server); a self-approval always asks.
 */
export function statusMoves(
  caseItem: ManagedTestCase,
  currentUserId: string | null,
  options: StatusMoveOptions = {},
): StatusMove[] {
  const { canRetire = true } = options
  const allowed = new Set<string>(caseItem.allowed_actions ?? [])
  const reasonList = Array.isArray(caseItem.reason_required_actions) ? caseItem.reason_required_actions : null
  const isAuthor = Boolean(currentUserId) && caseItem.author_id === currentUserId
  const moves: StatusMove[] = []
  for (const action of STATUS_MOVE_ORDER) {
    if (!allowed.has(action) || !MOVE_SET.has(action)) continue
    if (action === 'approve' && !isAuthor) continue
    if (RETIRE_MOVES.has(action) && !canRetire) continue
    const needsReason = action === 'approve'
      || (reasonList ? reasonList.includes(action) : REASON_ACTIONS.has(action))
    moves.push({ action, label: STATUS_MOVE_LABELS[action], needsReason })
  }
  return moves
}

/** A move's label without its trailing "…" (the reason form's title and confirm button). */
export function moveTitle(label: string): string {
  return label.replace(/…$/, '')
}

/** "Active", "Review requested", "Needs update": a lifecycle state as the toast names it. */
export function statusLabel(status: string): string {
  const text = status.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
