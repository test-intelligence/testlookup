import useSWR from 'swr'
import { deepInvestigationService } from '@/services/deepInvestigationService'
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

export function useReleaseDecision(runId: string | null) {
  return useSWR<ReleaseDecision>(
    runId ? `/release-readiness/${runId}` : null,
    () => deepInvestigationService.getReleaseDecision(runId ?? ''),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}
