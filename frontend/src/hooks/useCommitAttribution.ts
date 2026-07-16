import useSWR from 'swr'
import {
  commitAttributionService,
  CommitRange,
  SuspectRanking,
  SuspectQuery,
} from '@/services/commitAttributionService'

/** Epic 8 US-8.1 — the commit range associated with a run. */
export function useCommitRange(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<CommitRange>(
    runId ? `commit-range-${runId}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return commitAttributionService.getCommitRange(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    range: data ?? null,
    isLoading,
    isError: !!error,
    refresh: mutate,
    available: data?.available ?? false,
    commits: data?.commits ?? [],
  }
}

/** Epic 8 US-8.2 — ranked suspect commits for a failing cluster/test. */
export function useSuspects(runId: string | null, query: SuspectQuery = {}) {
  const key = query.clusterId
    ? `suspects-${runId}-cluster-${query.clusterId}`
    : query.fingerprint
      ? `suspects-${runId}-fp-${query.fingerprint}`
      : `suspects-${runId}`

  const { data, error, isLoading, mutate } = useSWR<SuspectRanking>(
    runId ? key : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return commitAttributionService.getSuspects(runId, query)
    },
    { revalidateOnFocus: false },
  )

  return {
    ranking: data ?? null,
    suspects: data?.suspects ?? [],
    available: data?.available ?? false,
    caveat: data?.caveat ?? '',
    hasLocationSignal: data?.has_location_signal ?? false,
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
