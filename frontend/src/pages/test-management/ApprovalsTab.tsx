import { useState } from 'react'
import { Clock, Shield, Sparkles, Star } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import { refreshTestCases, useTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase, TestCaseTransitionAction } from '@/types/test-management'
import { PRIORITY_COLORS, fmtDate } from './format'
import { QualityScore, StatusPill } from './tmUi'

// ─── Tab: Approvals (UX redesign P4 item 7; was "Reviews") ───────────────────
//
// The test-case review queue: cases whose review was requested, and cases a
// reviewer has claimed. Renamed from Reviews; `?tab=reviews` still opens it.

const REVIEW_ACTION_LABELS: Partial<Record<TestCaseTransitionAction, string>> = {
  claim_review: 'Claim review',
  unclaim: 'Unclaim',
  approve: 'Approve',
  request_changes: 'Request changes',
  reject: 'Reject',
}

const REVIEW_DECISION_ACTIONS = new Set<TestCaseTransitionAction>([
  'approve',
  'request_changes',
  'reject',
])

const LEGACY_REVIEW_ACTIONS: TestCaseTransitionAction[] = ['approve', 'request_changes', 'reject']

interface ApprovalsTabProps { projectId: string | null; lifecycleV2: boolean }
type ReviewQueueSource = 'requested' | 'claimed'

export default function ApprovalsTab({ projectId: _projectId, lifecycleV2 }: ApprovalsTabProps) {
  const [reviewingId, setReviewingId] = useState<string | null>(null)
  const [transitioning, setTransitioning] = useState<{ caseId: string; action: TestCaseTransitionAction } | null>(null)
  const [pendingDecision, setPendingDecision] = useState<{ caseItem: ManagedTestCase; action: TestCaseTransitionAction } | null>(null)
  const [reviewOverrides, setReviewOverrides] = useState<Map<string, ManagedTestCase>>(() => new Map())
  const [locallyAiReviewedIds, setLocallyAiReviewedIds] = useState<Set<string>>(() => new Set())
  const [queueRefreshWarning, setQueueRefreshWarning] = useState<string | null>(null)
  const requestedQuery = useTestCases({ status: 'review_requested', size: 50 })
  const claimedQuery = useTestCases({ status: 'under_review', size: 50 })

  const entriesById = new Map<string, { caseItem: ManagedTestCase; source: ReviewQueueSource }>()
  for (const caseItem of requestedQuery.data?.items ?? []) {
    entriesById.set(caseItem.id, { caseItem, source: 'requested' })
  }
  for (const caseItem of claimedQuery.data?.items ?? []) {
    entriesById.set(caseItem.id, { caseItem, source: 'claimed' })
  }
  for (const updated of reviewOverrides.values()) {
    if (updated.status === 'review_requested') {
      entriesById.set(updated.id, { caseItem: updated, source: 'requested' })
    } else if (updated.status === 'under_review') {
      entriesById.set(updated.id, { caseItem: updated, source: 'claimed' })
    } else {
      entriesById.delete(updated.id)
    }
  }
  const reviewEntries = Array.from(entriesById.values())
  const isLoading = requestedQuery.isLoading || claimedQuery.isLoading
  const reviewError = requestedQuery.error ?? claimedQuery.error

  // The two queues AND every tm-cases roll: the library headline, the Cases
  // tab's health banner and its Insights review queue are derived from a
  // different useTestCases key, and refreshing only the queues left them
  // stale on the first paint back.
  const mutateReviews = () =>
    Promise.all([requestedQuery.mutate(), claimedQuery.mutate(), refreshTestCases()])

  async function retryReviews() {
    setQueueRefreshWarning(null)
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The review queue is still stale. Actions remain unavailable for rows from the failed source.')
    }
  }

  const handleAiReview = async (tc: ManagedTestCase) => {
    setReviewingId(tc.id)
    setQueueRefreshWarning(null)
    try {
      await testManagementService.aiReview(tc.id)
    } catch {
      toast.error('AI review failed')
      setReviewingId(null)
      return
    }

    setLocallyAiReviewedIds((current) => new Set(current).add(tc.id))
    setReviewingId(null)
    toast.success('AI review complete')
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The AI review completed, but the review queue could not be refreshed. The completed control remains disabled locally.')
    }
  }

  const handleReviewAction = async (
    tc: ManagedTestCase,
    action: TestCaseTransitionAction,
    notes?: string,
  ) => {
    setTransitioning({ caseId: tc.id, action })
    setQueueRefreshWarning(null)
    let updated: ManagedTestCase
    try {
      if (lifecycleV2) {
        updated = await testManagementService.transitionCase(tc.id, {
          action,
          expected_version: tc.version,
          ...(notes ? { notes } : {}),
        })
      } else {
        updated = await testManagementService.reviewAction(tc.id, action, notes)
      }
    } catch {
      toast.error('Action failed')
      setTransitioning(null)
      return
    }

    setReviewOverrides((current) => new Map(current).set(updated.id, updated))
    setPendingDecision(null)
    setTransitioning(null)
    toast.success(`Test case ${action.replace(/_/g, ' ')}`)
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The review action was saved, but the queue could not be refreshed. The returned case state is shown locally.')
    }
  }

  function chooseReviewAction(tc: ManagedTestCase, action: TestCaseTransitionAction) {
    if (REVIEW_DECISION_ACTIONS.has(action)) {
      setPendingDecision({ caseItem: tc, action })
      return
    }
    void handleReviewAction(tc, action)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-text-muted)]">{reviewEntries.length} test cases awaiting review</p>
      </div>

      {reviewError && (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]">
          <span>
            The review queue could not be loaded completely. Any available cases are shown below.
          </span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void retryReviews()}>
            Retry both lists
          </button>
        </div>
      )}

      {queueRefreshWarning && (
        <p role="status" className="rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-3 text-sm text-[var(--status-broken)]">
          {queueRefreshWarning}
        </p>
      )}

      {isLoading && reviewEntries.length === 0 ? (
        <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
      ) : reviewEntries.length === 0 && !reviewError ? (
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="No pending reviews"
          description="Test cases submitted for review will appear here"
        />
      ) : (
        <div className="space-y-3">
          {reviewEntries.map(({ caseItem: tc, source }) => {
            const sourceFailed = source === 'requested' ? !!requestedQuery.error : !!claimedQuery.error
            const aiReviewSavedLocally = locallyAiReviewedIds.has(tc.id)
            return (
            <div key={tc.id} className="card">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-sm font-medium text-[var(--color-text)] truncate">{tc.title}</h4>
                    <StatusPill status={tc.priority} map={PRIORITY_COLORS} />
                    <span className="text-xs text-[var(--color-text-muted)] capitalize">{tc.test_type}</span>
                  </div>
                  {tc.feature_area && <p className="text-xs text-[var(--color-text-muted)] mb-2">{tc.feature_area}</p>}
                  {tc.objective && <p className="text-xs text-[var(--color-text-muted)] line-clamp-2">{tc.objective}</p>}
                  <div className="flex items-center gap-4 mt-2">
                    <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                      <Clock className="h-3.5 w-3.5" />
                      Requested {fmtDate(tc.updated_at)}
                    </div>
                    {tc.ai_quality_score != null && (
                      <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                        <Star className="h-3.5 w-3.5" />
                        AI Score: <QualityScore score={tc.ai_quality_score} />
                      </div>
                    )}
                    {tc.steps && (
                      <span className="text-xs text-[var(--color-text-muted)]">{tc.steps.length} steps</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
                  {sourceFailed ? (
                    <span className="text-xs text-[var(--status-broken)]">Actions unavailable until this review list refreshes.</span>
                  ) : (
                    <>
                    <button
                      onClick={() => handleAiReview(tc)}
                      disabled={reviewingId === tc.id || aiReviewSavedLocally}
                      className="btn-secondary flex items-center gap-1.5 text-xs"
                    >
                      {reviewingId === tc.id ? <LoadingSpinner size="sm" /> : <Sparkles className="h-3.5 w-3.5" />}
                      {aiReviewSavedLocally ? 'AI reviewed' : 'AI Review'}
                    </button>
                    {(lifecycleV2 ? (tc.allowed_actions ?? []) : LEGACY_REVIEW_ACTIONS).map((action) => {
                    const label = REVIEW_ACTION_LABELS[action]
                    if (!label) return null
                    const saving = transitioning?.caseId === tc.id && transitioning.action === action
                    return (
                      <button
                        key={action}
                        type="button"
                        disabled={transitioning !== null}
                        onClick={() => chooseReviewAction(tc, action)}
                        className="btn-secondary text-xs"
                      >
                        {saving ? 'Saving…' : label}
                      </button>
                    )
                    })}
                    </>
                  )}
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}

      {pendingDecision && (
        <TransitionReasonDialog
          title={REVIEW_ACTION_LABELS[pendingDecision.action] ?? 'Record review decision'}
          description="Record a nonblank review note for the audit trail."
          confirmLabel={REVIEW_ACTION_LABELS[pendingDecision.action] ?? 'Submit'}
          fieldLabel="Review notes"
          placeholder="Explain the review decision"
          busy={transitioning?.caseId === pendingDecision.caseItem.id}
          onCancel={() => setPendingDecision(null)}
          onConfirm={(notes) => handleReviewAction(pendingDecision.caseItem, pendingDecision.action, notes)}
        />
      )}
    </div>
  )
}
