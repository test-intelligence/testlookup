import useSWR from 'swr'
import {
  runIntelligenceService,
  BaselineDiff,
  RunIntelligence,
  RunModeSummary,
  ScoringModel,
} from '@/services/runIntelligenceService'

export function useRunIntelligence(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<RunIntelligence>(
    runId ? `run-intelligence-${runId}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return runIntelligenceService.get(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    intelligence: data,
    isLoading,
    isError: !!error,
    refresh: mutate,
    // Convenience accessors for new fields
    whatChanged: data?.what_changed_since_last_good_run ?? null,
    dimensionScores: data?.dimension_scores ?? [],
    defectCandidates: data?.defect_candidates ?? [],
    summaryModes: data?.summary_modes ?? null,
    provenance: data?.provenance ?? null,
    allGreen: data?.all_green ?? false,
  }
}

export function useRunModeSummary(
  runId: string | null,
  mode: 'executive' | 'developer' | 'manager',
) {
  const { data, error, isLoading } = useSWR<RunModeSummary>(
    runId ? `run-summary-${runId}-${mode}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return runIntelligenceService.getSummary(runId, mode)
    },
    { revalidateOnFocus: false },
  )

  return {
    summary: data,
    isLoading,
    isError: !!error,
  }
}

export function useBaselineDiff(runId: string | null) {
  const { data, error, isLoading } = useSWR<BaselineDiff>(
    runId ? `baseline-diff-${runId}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return runIntelligenceService.getBaselineDiff(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    diff: data,
    isLoading,
    isError: !!error,
  }
}

export function useScoringModel() {
  const { data, error, isLoading } = useSWR<ScoringModel>(
    'scoring-model',
    () => runIntelligenceService.getScoringModel(),
    {
      revalidateOnFocus: false,
      // Scoring model rarely changes — cache aggressively
      dedupingInterval: 60_000,
    },
  )

  return {
    scoringModel: data,
    isLoading,
    isError: !!error,
    // Convenience: description lookup by dimension name
    getDescription: (name: string): string =>
      data?.dimensions.find(d => d.name === name)?.description ?? '',
  }
}
