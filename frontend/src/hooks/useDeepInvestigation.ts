import useSWR from 'swr'
import { deepInvestigationService, type PipelineStatus } from '@/services/deepInvestigationService'
import type { DeepFinding, FailureCluster, ReleaseDecision } from '@/types/deep-investigation'

export function useFailureClusters(runId: string | null) {
  return useSWR<FailureCluster[]>(
    runId ? `/deep-investigate/${runId}/clusters` : null,
    () => deepInvestigationService.getClusters(runId ?? ''),
    { revalidateOnFocus: false },
  )
}

export function useDeepFindings(runId: string | null) {
  return useSWR<DeepFinding[]>(
    runId ? `/deep-investigate/${runId}/findings` : null,
    () => deepInvestigationService.getFindings(runId ?? ''),
    { revalidateOnFocus: false },
  )
}

/**
 * WF-1 pipeline execution status for a run, fetched declaratively via SWR.
 *
 * Replaces a load-on-mount `useEffect` (set-state-in-effect) on
 * DeepInvestigationPage. The key is `null` (no fetch) when there is no run —
 * exactly the old `if (!runId) { setPipelineStatus(null); return }` guard.
 * `revalidateOnFocus` is left at the SWR default (true) to honour the page's
 * "refresh on focus" intent that the bare effect never actually delivered, and
 * `shouldRetryOnError: false` surfaces a failed load immediately as `isError`.
 */
export function usePipelineStatus(
  runId: string | null,
  workflowType: 'deep' | 'offline' = 'deep',
) {
  return useSWR<PipelineStatus>(
    runId ? [`/agents/runs/${runId}/pipeline-status`, workflowType] : null,
    () => deepInvestigationService.getPipelineStatus(runId ?? '', workflowType),
    { shouldRetryOnError: false },
  )
}

export function useReleaseDecision(runId: string | null) {
  return useSWR<ReleaseDecision>(
    runId ? `/release-readiness/${runId}` : null,
    () => deepInvestigationService.getReleaseDecision(runId ?? ''),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}
