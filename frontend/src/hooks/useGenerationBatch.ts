/** SWR hook for polling a generation batch until complete. */
import useSWR from 'swr'
import { ragService } from '@/services/ragGenerationService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useGenerationBatch(batchId: string | null) {
  return useSWR(
    batchId ? ['generation-batch', batchId] : null,
    () => {
      if (!batchId) {
        throw new Error('Batch ID is required')
      }
      return ragService.getBatch(batchId)
    },
    {
      refreshInterval: (data) => {
        // Poll every 3s while pending, stop when complete/failed
        if (!data || data.status === 'pending') return 3000
        return 0
      },
    },
  )
}

export function useKnowledgeSources(projectId: string | null, params?: Record<string, unknown>) {
  return useSWR(
    projectId ? ['knowledge-sources', projectId, params] : null,
    () => {
      if (!projectId) {
        throw new Error('Project ID is required')
      }
      return ragService.listSources(projectId, params)
    },
    { refreshInterval: REFRESH_INTERVALS.POLLING },
  )
}

export function useRagStatus() {
  return useSWR(
    'rag-status',
    () => ragService.getStatus(),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}
