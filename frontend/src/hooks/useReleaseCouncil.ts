import useSWR from 'swr'
import {
  releaseCouncilService,
  ReleaseCouncilDecision,
} from '@/services/releaseCouncilService'

export function useReleaseCouncil(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<ReleaseCouncilDecision>(
    runId ? `release-council-${runId}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return releaseCouncilService.get(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    council: data,
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
