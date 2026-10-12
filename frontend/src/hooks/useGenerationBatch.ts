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

/**
 * Whether Knowledge RAG is on for `projectId` (and the signed-in user).
 *
 * Keyed per project: the `knowledge_rag` flag can be allow-listed to some
 * projects, and an unscoped status said "not enabled" for every one of them
 * while their generate calls would have worked (or, the other way round,
 * showed the form to a project the gate refuses).
 */
export function useRagStatus(projectId: string | null) {
  return useSWR(
    ['rag-status', projectId],
    () => ragService.getStatus(projectId),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}
