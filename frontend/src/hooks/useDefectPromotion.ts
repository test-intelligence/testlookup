import { useState } from 'react'
import useSWR from 'swr'
import { defectPromotionService } from '@/services/defectPromotionService'
import type {
  DefectCandidateResponse,
  DefectPromotionRequest,
  DefectPromotionResponse,
} from '@/types/defect-promotion'

/**
 * Fetch the pre-assembled defect candidate for a cluster.
 * Only fetches when both runId and clusterId are provided.
 */
export function useDefectCandidate(
  runId: string | null,
  clusterId: string | null,
) {
  const shouldFetch = Boolean(runId && clusterId)
  const { data, isLoading, error, mutate } = useSWR<DefectCandidateResponse>(
    shouldFetch
      ? `defect-candidate-${runId}-${clusterId}`
      : null,
    () => defectPromotionService.getCandidate(runId!, clusterId!),
    { revalidateOnFocus: false },
  )
  return { candidate: data, isLoading, isError: Boolean(error), mutate }
}

/**
 * Hook for submitting a defect promotion from a modal form.
 */
export function usePromoteCluster() {
  const [isPromoting, setIsPromoting] = useState(false)
  const [promotionResult, setPromotionResult] =
    useState<DefectPromotionResponse | null>(null)
  const [promotionError, setPromotionError] = useState<string | null>(null)

  const promote = async (
    runId: string,
    clusterId: string,
    request: DefectPromotionRequest,
  ): Promise<DefectPromotionResponse | null> => {
    setIsPromoting(true)
    setPromotionError(null)
    try {
      const result = await defectPromotionService.promote(runId, clusterId, request)
      setPromotionResult(result)
      return result
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to promote cluster to defect'
      setPromotionError(message)
      return null
    } finally {
      setIsPromoting(false)
    }
  }

  const reset = () => {
    setPromotionResult(null)
    setPromotionError(null)
  }

  return { promote, isPromoting, promotionResult, promotionError, reset }
}
