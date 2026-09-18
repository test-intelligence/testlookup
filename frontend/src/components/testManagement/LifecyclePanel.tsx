import { useState } from 'react'
import toast from 'react-hot-toast'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useAllowedTestCaseTransitions } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type {
  AllowedTestCaseTransition,
  ManagedTestCase,
  TestCaseTransitionAction,
} from '@/types/test-management'
import TransitionReasonDialog from './TransitionReasonDialog'

const ACTION_LABELS: Record<TestCaseTransitionAction, string> = {
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

const REASON_ACTIONS = new Set<TestCaseTransitionAction>([
  'deprecate',
  'reinstate',
  'archive',
  'flag_stale',
])

const REVIEW_NOTES_ACTIONS = new Set<TestCaseTransitionAction>([
  'approve',
  'reject',
  'request_changes',
])

function transitionErrorMessage(error: unknown): string {
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

interface LifecyclePanelProps {
  caseItem: ManagedTestCase
  onChanged: (updated: ManagedTestCase) => void
}

export default function LifecyclePanel({ caseItem, onChanged }: LifecyclePanelProps) {
  const { data, error, isLoading, mutate } = useAllowedTestCaseTransitions(caseItem.id)
  const [pendingAction, setPendingAction] = useState<TestCaseTransitionAction | null>(null)
  const [inputAction, setInputAction] = useState<TestCaseTransitionAction | null>(null)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null)
  const [actionsStale, setActionsStale] = useState(false)

  // In v2 the availability endpoint is authoritative. Do not infer an action
  // from state or stale list data when it is loading, unavailable, or failed.
  const decisions: AllowedTestCaseTransition[] = error || actionsStale ? [] : (data ?? [])

  async function transition(action: TestCaseTransitionAction, input?: string) {
    setPendingAction(action)
    setInlineError(null)
    setRefreshWarning(null)
    setActionsStale(false)
    let updated: ManagedTestCase
    try {
      updated = await testManagementService.transitionCase(caseItem.id, {
        action,
        expected_version: caseItem.version,
        ...(input && REVIEW_NOTES_ACTIONS.has(action) ? { notes: input } : {}),
        ...(input && REASON_ACTIONS.has(action) ? { reason: input } : {}),
      })
    } catch (transitionError) {
      setInlineError(transitionErrorMessage(transitionError))
      setPendingAction(null)
      try {
        await mutate()
      } catch {
        // The mutation already failed; retain its actionable error message.
      }
      return
    }

    onChanged(updated)
    setInputAction(null)
    toast.success(`${ACTION_LABELS[action]} complete`)
    try {
      await mutate()
    } catch {
      setActionsStale(true)
      setRefreshWarning('The lifecycle change was saved, but available actions could not be refreshed. Retry before taking another action.')
    } finally {
      setPendingAction(null)
    }
  }

  async function retryActions() {
    setRefreshWarning(null)
    try {
      await mutate()
      setActionsStale(false)
    } catch {
      setActionsStale(true)
      setRefreshWarning('Lifecycle actions are still unavailable. No action can be taken until the list refreshes.')
    }
  }

  function chooseAction(action: TestCaseTransitionAction) {
    if (REASON_ACTIONS.has(action) || REVIEW_NOTES_ACTIONS.has(action)) {
      setInputAction(action)
      return
    }
    void transition(action)
  }

  return (
    <section aria-label="Test case lifecycle" className="space-y-4">
      <div>
        <p className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Lifecycle</p>
        <div className="mt-1 flex items-center gap-2">
          <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2.5 py-1 text-sm font-medium capitalize text-[var(--color-text)]">
            {caseItem.status.replace(/_/g, ' ')}
          </span>
          {caseItem.lifecycle_state_changed_at && (
            <span className="text-xs text-[var(--color-text-muted)]">
              since {new Date(caseItem.lifecycle_state_changed_at).toLocaleString()}
            </span>
          )}
        </div>
        {caseItem.needs_update_reason && (
          <p className="mt-2 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-xs text-[var(--status-broken)]">
            Needs update: {caseItem.needs_update_reason.replace(/_/g, ' ')}
          </p>
        )}
      </div>

      <div>
        <p className="mb-2 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Available actions</p>
        {isLoading && decisions.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <LoadingSpinner size="sm" /> Loading lifecycle actions…
          </div>
        ) : (error || actionsStale) && decisions.length === 0 ? (
          <button type="button" className="btn-secondary text-sm" onClick={() => void retryActions()}>
            Retry lifecycle actions
          </button>
        ) : decisions.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No lifecycle actions are available.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {decisions.map((decision) => (
              <button
                key={decision.action}
                type="button"
                disabled={!decision.allowed || pendingAction !== null}
                title={!decision.allowed ? (decision.blocked_reason ?? 'Not allowed from the current state') : undefined}
                onClick={() => chooseAction(decision.action)}
                className="btn-secondary text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pendingAction === decision.action ? 'Saving…' : ACTION_LABELS[decision.action]}
              </button>
            ))}
          </div>
        )}
      </div>

      {inlineError && (
        <p role="alert" className="rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-2 text-sm text-[var(--status-failed)]">
          {inlineError}
        </p>
      )}

      {refreshWarning && (
        <p role="status" className="rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-sm text-[var(--status-broken)]">
          {refreshWarning}
        </p>
      )}

      {inputAction && (
        <TransitionReasonDialog
          title={ACTION_LABELS[inputAction]}
          description={REVIEW_NOTES_ACTIONS.has(inputAction)
            ? 'Record a nonblank review note for the audit trail.'
            : 'This action changes the governed lifecycle and will be recorded in the audit trail.'}
          confirmLabel={ACTION_LABELS[inputAction]}
          fieldLabel={REVIEW_NOTES_ACTIONS.has(inputAction) ? 'Review notes' : 'Reason'}
          placeholder={REVIEW_NOTES_ACTIONS.has(inputAction)
            ? 'Explain the review decision'
            : 'Explain why this lifecycle change is needed'}
          busy={pendingAction === inputAction}
          onCancel={() => setInputAction(null)}
          onConfirm={(input) => transition(inputAction, input)}
        />
      )}
    </section>
  )
}
