/**
 * The Status moves of a case (W28 TCM-02): the Details tab's toolbar
 * (`role="group"`, "Case actions"), first thing above the details.
 *
 * Buttons, one per move the server allows this viewer from this state,
 * labelled by outcome (`Make active`, `Back to draft`, `Request review`,
 * `Approve my case…`, `Deprecate…`), never a `<select>` (a closed select's
 * arrow keys would fire a transition). A move without a reason sends at once;
 * one that needs a reason opens the reason form right under the toolbar, in
 * the pane's slot. A refusal (409 / 403) is said under the toolbar and the
 * case is re-read. The Lifecycle tab stays the full governed list.
 *
 * Deprecate / Archive / Reinstate are offered to QA leads and administrators
 * only (W28 contract AMENDMENT): the host passes `canRetire`.
 */
import { useState, type ReactNode } from 'react'
import toast from 'react-hot-toast'

import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { testCaseEditingService } from '@/services/testCaseEditingService'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase, TestCaseTransitionAction } from '@/types/test-management'
import { moveTitle, statusLabel, statusMoves, transitionErrorMessage, type StatusMove } from './caseStatusModel'

export interface CaseStatusControlProps {
  caseItem: ManagedTestCase
  currentUserId: string | null
  /** The case pane's slot (key `reason`): one open form per pane, and the discard question. */
  slot?: InlineSlot<string>
  /** A move saved (or the case re-read after a refusal): the host shows it and refreshes the lists. */
  onChanged: (updated: ManagedTestCase) => void
  /** QA_LEAD or above: Deprecate / Archive / Reinstate are offered. Default false. */
  canRetire?: boolean
  /** The toolbar's right-hand side (the Edit button). */
  edit?: ReactNode
}

const REQUEST_REVIEW_HINT =
  'Request review moves it to Review requested until a reviewer approves it; plans that already hold it keep it.'

export default function CaseStatusControl({
  caseItem,
  currentUserId,
  slot,
  onChanged,
  canRetire = false,
  edit,
}: CaseStatusControlProps) {
  const [pending, setPending] = useState<TestCaseTransitionAction | null>(null)
  const [reasonMove, setReasonMove] = useState<StatusMove | null>(null)
  const [error, setError] = useState<string | null>(null)

  const editedAfterApproval = caseItem.review_required === true && caseItem.edited_after_approval === true
  const allMoves = statusMoves(caseItem, currentUserId, { canRetire })
  // An edited-after-approval case offers Request review first.
  const moves = editedAfterApproval
    ? [...allMoves.filter((m) => m.action === 'request_review'), ...allMoves.filter((m) => m.action !== 'request_review')]
    : allMoves
  const offersReview = moves.some((m) => m.action === 'request_review')
  const reasonOpen = reasonMove !== null && (!slot || slot.openKey === 'reason')

  async function send(move: StatusMove, reason?: string) {
    setPending(move.action)
    setError(null)
    try {
      const updated = await testManagementService.transitionCase(caseItem.id, {
        action: move.action,
        ...(reason ? { reason } : {}),
        ...(reason && move.action === 'approve' ? { notes: reason } : {}),
        expected_version: caseItem.version,
      })
      setReasonMove(null)
      if (slot?.openKey === 'reason') slot.close()
      onChanged(updated)
      toast.success(`Moved to ${statusLabel(updated.status)}`)
    } catch (moveError) {
      setError(transitionErrorMessage(moveError))
      try {
        onChanged(await testCaseEditingService.getCaseFresh(caseItem.id))
      } catch {
        // The refusal's sentence stays; the case shown is the last one read.
      }
    } finally {
      setPending(null)
    }
  }

  function choose(move: StatusMove) {
    if (!move.needsReason) {
      void send(move)
      return
    }
    setError(null)
    if (slot) {
      // Another form of this pane with typed input asks first; [Discard changes] then opens this one.
      slot.open('reason')
    }
    setReasonMove(move)
  }

  function closeReason() {
    setReasonMove(null)
    if (slot?.openKey === 'reason') slot.close()
  }

  return (
    <div className="space-y-2" data-case-status-control="">
      <div
        role="group"
        aria-label="Case actions"
        data-case-toolbar=""
        className="flex flex-wrap items-start justify-between gap-2"
      >
        <div className="flex min-w-0 flex-wrap gap-1.5">
          {moves.map((move) => (
            <button
              key={move.action}
              type="button"
              className="btn-secondary text-xs"
              // One move at a time: while one saves, or its reason form is open, the others wait.
              disabled={pending !== null || reasonOpen}
              onClick={() => choose(move)}
            >
              {pending === move.action ? 'Saving…' : move.label}
            </button>
          ))}
        </div>
        {edit}
      </div>
      {editedAfterApproval && (
        <div data-edited-after-approval="" className="text-xs text-[var(--status-broken)]">
          <p className="m-0">
            Edited after approval (since v{caseItem.edited_since_approval_version ?? caseItem.version}; now v{caseItem.version}).
          </p>
          {offersReview && <p className="m-0 mt-0.5 text-[var(--color-text-muted)]">{REQUEST_REVIEW_HINT}</p>}
        </div>
      )}
      <p className="m-0 text-xs text-[var(--color-text-muted)]" data-review-policy="">
        {caseItem.review_required === true
          ? 'Review is required before Active in this project.'
          : 'Review is optional in this project.'}
      </p>
      {error && (
        <p role="alert" className="m-0 rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-2 text-sm text-[var(--status-failed)]">
          {error}
        </p>
      )}
      {reasonOpen && reasonMove && (
        <TransitionReasonDialog
          title={moveTitle(reasonMove.label)}
          description="This change is recorded in the audit trail with your reason."
          confirmLabel={moveTitle(reasonMove.label)}
          busy={pending === reasonMove.action}
          slot={slot}
          onCancel={closeReason}
          onConfirm={(reason) => send(reasonMove, reason)}
        />
      )}
    </div>
  )
}
