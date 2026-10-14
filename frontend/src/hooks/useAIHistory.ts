import useSWR from 'swr'

import { aiHistoryService, type AICompareOptions } from '@/services/aiHistoryService'
import type {
  AICompare,
  AIDigestDiffResponse,
  AIHistoryKind,
  AIHistoryList,
  AIPreviousRun,
  AIVersion,
} from '@/types/aiHistory'

/**
 * W03 AIH-04: the run's AI history hooks. Each asks nothing until its ids are known (a `null` key), so a tab that
 * is not open, a run still loading or a version not yet mapped to its id makes no request. Keys carry every
 * parameter, so a different version or baseline is a new read.
 *
 * Only the version list revalidates on focus: a "Trigger pipeline" that finished while the reader was elsewhere
 * shows up as a new version when they come back to the tab.
 */
export function useAIHistory(runId: string | null | undefined, kind: AIHistoryKind = 'analysis_digest') {
  const { data, error, isLoading, mutate } = useSWR<AIHistoryList>(
    runId ? ['ai-history', runId, kind] : null,
    () => aiHistoryService.listVersions(runId as string, { kind }),
    { revalidateOnFocus: true },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

export function useAIVersion(runId: string | null | undefined, versionId: string | null | undefined) {
  const { data, error, isLoading, mutate } = useSWR<AIVersion>(
    runId && versionId ? ['ai-version', runId, versionId] : null,
    () => aiHistoryService.getVersion(runId as string, versionId as string),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

/** `to`: a version id, `latest` or `live`. */
export function useAIVersionDiff(
  runId: string | null | undefined,
  fromId: string | null | undefined,
  to: string | null | undefined,
) {
  const { data, error, isLoading, mutate } = useSWR<AIDigestDiffResponse>(
    runId && fromId && to ? ['ai-diff', runId, fromId, to] : null,
    () => aiHistoryService.diffVersions(runId as string, { from: fromId as string, to: to as string }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

/** Pass `runId: null` to ask nothing (e.g. two runs of different projects). */
export function useAICompare(runId: string | null | undefined, options: AICompareOptions = {}) {
  const { baselineRunId, suiteName, versionId, baselineVersionId } = options
  const { data, error, isLoading, mutate } = useSWR<AICompare>(
    runId
      ? ['ai-compare', runId, baselineRunId || '', suiteName || '', versionId || '', baselineVersionId || '']
      : null,
    () => aiHistoryService.compare(runId as string, { baselineRunId, suiteName, versionId, baselineVersionId }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

/** The server's previous run of this run's suite on its branch (D42). */
export function usePreviousRun(runId: string | null | undefined, suiteName?: string | null) {
  const { data, error, isLoading, mutate } = useSWR<AIPreviousRun>(
    runId ? ['run-previous', runId, suiteName || ''] : null,
    () => aiHistoryService.getPreviousRun(runId as string, { suiteName }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}
