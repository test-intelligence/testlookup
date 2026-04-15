import useSWR from 'swr'
import { decisionTrailService } from '@/services/decisionTrailService'
import type { DecisionTrailResponse } from '@/types/decisionTrail'

/**
 * Lazy fetch the AI decision trail for a run.
 *
 * Pass ``enabled=false`` to skip the request — the drawer uses this so we
 * only pay for the network round-trip when the user actually opens it.
 */
export function useDecisionTrail(runId: string | null, enabled: boolean = true) {
  return useSWR<DecisionTrailResponse>(
    enabled && runId ? ['decision-trail', runId] : null,
    () => decisionTrailService.get(runId!),
    { revalidateOnFocus: false },
  )
}
