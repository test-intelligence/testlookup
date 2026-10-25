/**
 * The queues behind Inbox › Approvals (UX redesign P4, owner decision D4): AI
 * reports awaiting review, quarantine proposals, test cases awaiting review,
 * and (QA leads only) defects awaiting approval (W06 DEF-06) and agent
 * actions awaiting a decision (W15 AGT-01). Each reads through the hook its
 * own view already uses, with the same arguments, so the SWR keys — and the
 * caches — are shared with `ReviewsBody`, `QuarantineBody`, Test cases'
 * review queue, `DefectApprovals` and `AgentActionApprovals`: a decision taken
 * in one place updates the counts everywhere.
 *
 * Every queue belongs to one project. Callers mount these only while a single
 * project is active (in All Projects mode the Approvals tab shows the project
 * prompt and asks for nothing).
 */
import { useTestCases } from '@/hooks/useTestManagement'
import { useProjectReviews } from '@/hooks/useReviews'
import { useQuarantineList } from '@/hooks/useFlakyQuarantine'
import { usePendingDefects } from '@/hooks/usePendingDefects'
import { usePendingAgentActions } from '@/hooks/useAgentActions'
import { usePermissions } from '@/hooks/usePermissions'
import type { ManagedTestCase } from '@/types/test-management'
import { isQuarantineProposal } from '../flaky/flakyModel'

/** The two test-case review queues, exactly as Test cases' review tab asks for them. */
const REQUESTED = { status: 'review_requested', size: 50 } as const
const CLAIMED = { status: 'under_review', size: 50 } as const

export type CaseQueueSource = 'requested' | 'claimed'

export interface CaseQueueEntry {
  caseItem: ManagedTestCase
  source: CaseQueueSource
}

/** Requested and claimed test cases, one row per case (a case in both lists is shown once, as claimed). */
export function useCaseQueues() {
  const requested = useTestCases(REQUESTED)
  const claimed = useTestCases(CLAIMED)
  const byId = new Map<string, CaseQueueEntry>()
  for (const caseItem of requested.data?.items ?? []) byId.set(caseItem.id, { caseItem, source: 'requested' })
  for (const caseItem of claimed.data?.items ?? []) byId.set(caseItem.id, { caseItem, source: 'claimed' })
  return {
    entries: Array.from(byId.values()),
    requested,
    claimed,
    isLoading: requested.isLoading || claimed.isLoading,
    error: requested.error ?? claimed.error,
  }
}

export interface ApprovalCounts {
  reports: number | undefined
  quarantine: number | undefined
  cases: number | undefined
  /**
   * Defects awaiting approval. The list is QA_LEAD-only, so it is asked for
   * only for a QA lead; for anyone else this is 0 (nothing they can decide)
   * and the total never waits on a queue the caller cannot read.
   */
  defects: number | undefined
  /**
   * Agent actions awaiting a decision (W15 AGT-01): QA leads only, like
   * defects — the server's `total` of the pending view, read through the SAME
   * SWR key as the Agent actions section (one request for the count and the
   * list); 0 for anyone else, whose total never waits on it.
   */
  actions: number | undefined
  /** Everything waiting; undefined until every queue the caller reads has answered. */
  total: number | undefined
}

/** How many items wait in each queue (`undefined` while a queue is loading or failed). */
export function useApprovalCounts(projectId: string): ApprovalCounts {
  const reviews = useProjectReviews('pending_review')
  const quarantine = useQuarantineList({ projectId, liveOnly: true })
  const cases = useCaseQueues()
  const { isQaLead } = usePermissions()
  const pendingDefects = usePendingDefects(projectId, isQaLead)
  const pendingActions = usePendingAgentActions(projectId, isQaLead)
  const reports = reviews.isLoading || reviews.isError ? undefined : reviews.reviews.length
  const proposals =
    quarantine.isLoading || quarantine.isError
      ? undefined
      : quarantine.requests.filter((r) => isQuarantineProposal(r.status)).length
  const caseCount = cases.isLoading || cases.error ? undefined : cases.entries.length
  const defects = !isQaLead ? 0 : pendingDefects.error ? undefined : pendingDefects.total
  // An older server answers `count` (the page length) without `total`. No page, not loading and no error is a
  // queue that was not asked (no key): nothing waits there for this reader.
  const actionPage = pendingActions.data
  const actions = !isQaLead
    ? 0
    : pendingActions.error
      ? undefined
      : actionPage
        ? (actionPage.total ?? actionPage.count)
        : pendingActions.isLoading
          ? undefined
          : 0
  const total =
    reports === undefined ||
    proposals === undefined ||
    caseCount === undefined ||
    defects === undefined ||
    actions === undefined
      ? undefined
      : reports + proposals + caseCount + defects + actions
  return { reports, quarantine: proposals, cases: caseCount, defects, actions, total }
}
