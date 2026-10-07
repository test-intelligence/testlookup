/**
 * Inbox › Approvals › Test cases (UX redesign P4, D4): the test cases awaiting
 * review, with the review decisions Test cases' own review queue offers —
 * Claim / Unclaim, and Approve / Request changes / Reject, each decision with a
 * nonblank note for the audit trail (`TransitionReasonDialog`). The same
 * services and hooks as that queue (`useTestCases`, `transitionCase` under
 * lifecycle v2, else the legacy `reviewAction`); the full case editor and the
 * AI quality review stay on the Test cases page, linked from here.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock, Shield } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { refreshTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase, TestCaseTransitionAction } from '@/types/test-management'
import { useCaseQueues, type CaseQueueEntry } from './approvalQueues'

const ACTION_LABEL: Partial<Record<TestCaseTransitionAction, string>> = {
  claim_review: 'Claim review',
  unclaim: 'Unclaim',
  approve: 'Approve',
  request_changes: 'Request changes',
  reject: 'Reject',
}

/** Decisions that need a note for the audit trail. */
const DECISIONS = new Set<TestCaseTransitionAction>(['approve', 'request_changes', 'reject'])

/** Without lifecycle v2 the server takes these three, for every reviewer. */
const LEGACY_ACTIONS: TestCaseTransitionAction[] = ['approve', 'request_changes', 'reject']

const fmtDate = (iso?: string) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—'

export default function CaseApprovals() {
  const lifecycleV2 = useFeatureEnabled('test_case_lifecycle_v2')
  const { entries, requested, claimed, isLoading, error } = useCaseQueues()
  const [overrides, setOverrides] = useState<Map<string, ManagedTestCase>>(() => new Map())
  const [transitioning, setTransitioning] = useState<{ caseId: string; action: TestCaseTransitionAction } | null>(null)
  const [pending, setPending] = useState<{ caseItem: ManagedTestCase; action: TestCaseTransitionAction } | null>(null)

  // A decision's returned case replaces the row at once (or drops it when it
  // left review), so the list does not wait on the refetch to agree.
  const rows: CaseQueueEntry[] = []
  for (const entry of entries) {
    const updated = overrides.get(entry.caseItem.id)
    if (!updated) rows.push(entry)
    else if (updated.status === 'review_requested') rows.push({ caseItem: updated, source: 'requested' })
    else if (updated.status === 'under_review') rows.push({ caseItem: updated, source: 'claimed' })
  }

  const refreshQueues = () => Promise.all([requested.mutate(), claimed.mutate(), refreshTestCases()])

  async function act(tc: ManagedTestCase, action: TestCaseTransitionAction, notes?: string) {
    setTransitioning({ caseId: tc.id, action })
    let updated: ManagedTestCase
    try {
      updated = lifecycleV2
        ? await testManagementService.transitionCase(tc.id, {
            action,
            expected_version: tc.version,
            ...(notes ? { notes } : {}),
          })
        : await testManagementService.reviewAction(tc.id, action, notes)
    } catch {
      toast.error('Action failed')
      setTransitioning(null)
      return
    }
    setOverrides((current) => new Map(current).set(updated.id, updated))
    setPending(null)
    setTransitioning(null)
    toast.success(`Test case ${action.replace(/_/g, ' ')}`)
    void refreshQueues()
  }

  function choose(tc: ManagedTestCase, action: TestCaseTransitionAction) {
    if (DECISIONS.has(action)) setPending({ caseItem: tc, action })
    else void act(tc, action)
  }

  return (
    <div className="space-y-3">
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]"
        >
          <span>The test-case review queue could not be loaded completely. Any available cases are shown below.</span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void refreshQueues()}>
            Retry
          </button>
        </div>
      )}

      {isLoading && rows.length === 0 ? (
        <div className="flex justify-center py-8"><LoadingSpinner size="lg" /></div>
      ) : rows.length === 0 && !error ? (
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="No test cases awaiting review"
          description="Test cases submitted for review appear here."
        />
      ) : rows.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-[var(--color-border)]">
          <table className="w-full text-sm" aria-label="Test cases awaiting review">
            <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
              <tr>
                <th className="text-left px-3 py-2">Test case</th>
                <th className="text-left px-3 py-2">Priority</th>
                <th className="text-left px-3 py-2">Review</th>
                <th className="text-left px-3 py-2">Requested</th>
                <th className="text-right px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ caseItem: tc, source }) => {
                const sourceFailed = source === 'requested' ? Boolean(requested.error) : Boolean(claimed.error)
                const actions = lifecycleV2 ? tc.allowed_actions ?? [] : LEGACY_ACTIONS
                return (
                  <tr key={tc.id} className="border-t border-[var(--color-border)]" data-testid="case-approval-row">
                    <td className="px-3 py-2 max-w-[420px]">
                      <div className="truncate text-[var(--color-text)]" title={tc.title}>{tc.title}</div>
                      <div className="truncate text-[11px] text-[var(--color-text-muted)]">
                        <span className="capitalize">{tc.test_type}</span>
                        {tc.feature_area && <span> · {tc.feature_area}</span>}
                        {tc.steps && <span> · {tc.steps.length} steps</span>}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs capitalize text-[var(--color-text-secondary)]">{tc.priority}</td>
                    <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] whitespace-nowrap">
                      {source === 'claimed' ? 'Claimed' : 'Requested'}
                    </td>
                    <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" aria-hidden />
                        {fmtDate(tc.updated_at)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {sourceFailed ? (
                        <span className="text-xs text-[var(--status-broken)]">Actions unavailable until this list refreshes.</span>
                      ) : (
                        <span className="inline-flex items-center gap-1">
                          {actions.map((action) => {
                            const label = ACTION_LABEL[action]
                            if (!label) return null
                            const saving = transitioning?.caseId === tc.id && transitioning.action === action
                            return (
                              <button
                                key={action}
                                type="button"
                                disabled={transitioning !== null}
                                onClick={() => choose(tc, action)}
                                className="btn-ghost text-xs"
                              >
                                {saving ? 'Saving…' : label}
                              </button>
                            )
                          })}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="text-xs text-[var(--color-text-muted)]">
        <Link to="/test-management?tab=approvals" className="text-[var(--color-accent)] hover:underline">
          Open the review queue in Test cases
        </Link>{' '}
        for the case editor and its AI quality review.
      </p>

      {pending && (
        <TransitionReasonDialog
          title={ACTION_LABEL[pending.action] ?? 'Record review decision'}
          description="Record a nonblank review note for the audit trail."
          confirmLabel={ACTION_LABEL[pending.action] ?? 'Submit'}
          fieldLabel="Review notes"
          placeholder="Explain the review decision"
          busy={transitioning?.caseId === pending.caseItem.id}
          onCancel={() => setPending(null)}
          onConfirm={(notes) => act(pending.caseItem, pending.action, notes)}
        />
      )}
    </div>
  )
}
