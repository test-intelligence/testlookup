/** SWR hook for polling a generation batch until complete. */
import useSWR from 'swr'
import { ragService } from '@/services/ragGenerationService'

export function useGenerationBatch(batchId: string | null) {
  return useSWR(
    batchId ? ['generation-batch', batchId] : null,
    () => ragService.getBatch(batchId!),
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
    () => ragService.listSources(projectId!, params),
    { refreshInterval: 30_000 },
  )
}

export function useRagStatus() {
  return useSWR(
    'rag-status',
    () => ragService.getStatus(),
    { refreshInterval: 60_000 },
  )
}
