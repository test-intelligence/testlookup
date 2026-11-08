import useSWR from 'swr'

import { observabilityTimelineService } from '@/services/observabilityTimelineService'
import type { RunObservabilityTimeline } from '@/types/observabilityTimeline'

/** The SWR key's first element (`['run-observability-timeline', runId]`). */
export const OBSERVABILITY_TIMELINE_KEY = 'run-observability-timeline'

/**
 * W39 STRAT-07: the run's stored observability evidence. Asks nothing until `runId` is known, and the card that calls
 * it is mounted only inside the Run page's Timing tab, so a tab that is not open asks for nothing. The read is pure
 * (the server fetches and writes nothing), so focus never re-asks; a failed read is the card's sentence at once (no
 * silent retries — opening the tab again asks again).
 */
export function useObservabilityTimeline(runId: string | null | undefined) {
  const { data, error, isLoading } = useSWR<RunObservabilityTimeline>(
    runId ? [OBSERVABILITY_TIMELINE_KEY, runId] : null,
    () => observabilityTimelineService.getRunObservabilityTimeline(runId as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return { data: data ?? null, isLoading, isError: !!error }
}
