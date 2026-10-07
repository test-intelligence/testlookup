/**
 * The three queues behind Inbox › Approvals (UX redesign P4, owner decision
 * D4): AI reports awaiting review, quarantine proposals, and test cases
 * awaiting review. Each reads through the hook its own page already uses, with
 * the same arguments, so the SWR keys — and the caches — are shared with
 * `ReviewsBody`, `QuarantineBody` and Test cases' review queue: a decision taken
 * in one place updates the counts everywhere.
 *
 * Every queue belongs to one project. Callers mount these only while a single
 * project is active (in All Projects mode the Approvals tab shows the project
 * prompt and asks for nothing).
 */
import { useTestCases } from '@/hooks/useTestManagement'
import { useProjectReviews } from '@/hooks/useReviews'
import { useQuarantineList } from '@/hooks/useFlakyQuarantine'
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
  /** Everything waiting; undefined until all three queues have answered. */
  total: number | undefined
}

/** How many items wait in each queue (`undefined` while a queue is loading or failed). */
export function useApprovalCounts(projectId: string): ApprovalCounts {
  const reviews = useProjectReviews('pending_review')
  const quarantine = useQuarantineList({ projectId, liveOnly: true })
  const cases = useCaseQueues()
  const reports = reviews.isLoading || reviews.isError ? undefined : reviews.reviews.length
  const proposals =
    quarantine.isLoading || quarantine.isError
      ? undefined
      : quarantine.requests.filter((r) => isQuarantineProposal(r.status)).length
  const caseCount = cases.isLoading || cases.error ? undefined : cases.entries.length
  const total =
    reports === undefined || proposals === undefined || caseCount === undefined
      ? undefined
      : reports + proposals + caseCount
  return { reports, quarantine: proposals, cases: caseCount, total }
}
