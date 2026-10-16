/**
 * The Runs list's batch reads (FAIL-04 / FAIL-03 / FAIL-05).
 *
 * A NEW file on purpose: `hooks/useRuns.ts` is replaced by a fixed-factory
 * `vi.mock` in ~40 test files, where a hook added to it would be `undefined`.
 *
 *  - `useRunAiVerdicts(runs)`: ONE `GET /runs/ai-verdicts` for the runs on
 *    screen (≤ 25, runs in flight excluded: they have nothing analysed yet).
 *    The server reads stored state only, so it polls with the runs list.
 *  - `useRunSignatures(ids)`: ONE `GET /runs/failure-signatures` for ≤ 50
 *    failed builds. The costly read: no poll, re-asked only when the id set
 *    changes (new failed builds arriving with the runs list's poll).
 *  - `useRunBranches(days)`: the active project's branches for the toolbar's
 *    branch select; nothing is asked in All Projects.
 *
 * Every key carries the ids as ONE sorted, joined string (`keyPart`), never an
 * array rebuilt per render.
 */
import { useMemo } from 'react'
import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { keyPart } from '@/lib/scopeParams'
import { aiVerdictFromBatch, type AiVerdict } from '@/pages/runsList/aiVerdict'
import { runListService } from '@/services/runListService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { RunBranchList, RunSignatureBatch, RunVerdictBatch, RunVerdictItem, TestRun } from '@/types/runs'
import { isRunInProgress } from '@/utils/runPassRate'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'

/** `GET /runs/ai-verdicts` takes at most this many ids (the table's page size). */
export const MAX_VERDICT_RUN_IDS = 25

const IN_PROGRESS: AiVerdict = { recommendation: null, reason: 'in-progress' }
const REQUEST_FAILED: AiVerdict = { recommendation: null, reason: 'unavailable' }

/** The ids the verdict batch asks for: runs not in flight, ≤ 25, sorted. */
export function verdictRunIds(runs: readonly Pick<TestRun, 'id' | 'status'>[]): string[] {
  const ids = runs.filter((r) => !isRunInProgress(r.status)).map((r) => r.id)
  return Array.from(new Set(ids)).slice(0, MAX_VERDICT_RUN_IDS).sort()
}

export function useRunAiVerdicts(runs: readonly TestRun[]): {
  verdictOf: (run: Pick<TestRun, 'id' | 'status'>) => AiVerdict | undefined
  isLoading: boolean
} {
  const ids = useMemo(() => verdictRunIds(runs), [runs])
  const key = ids.length > 0 ? keyPart(ids) : null
  const { data, error, isLoading } = useSWR<RunVerdictBatch>(
    key ? ['run-ai-verdicts', key] : null,
    () => runListService.getAiVerdicts(ids),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
      refreshInterval: REFRESH_INTERVALS.ACTIVE,
    },
  )
  const byId = useMemo(() => {
    const map = new Map<string, RunVerdictItem>()
    for (const item of data?.items ?? []) map.set(item.run_id, item)
    return map
  }, [data])
  const unavailable = useMemo(() => new Set(data?.unavailable ?? []), [data])
  const asked = useMemo(() => new Set(ids), [ids])

  const verdictOf = (run: Pick<TestRun, 'id' | 'status'>): AiVerdict | undefined => {
    if (isRunInProgress(run.status)) return IN_PROGRESS
    // Beyond the batch's cap: never asked, so never "not analysed".
    if (!asked.has(run.id)) return REQUEST_FAILED
    if (error && !data) return REQUEST_FAILED
    if (!data) return undefined
    return aiVerdictFromBatch(byId.get(run.id), unavailable.has(run.id))
  }
  return { verdictOf, isLoading: Boolean(key) && isLoading }
}

export function useRunSignatures(runIds: readonly string[]): {
  data: RunSignatureBatch | undefined
  error: unknown
  isLoading: boolean
} {
  const ids = useMemo(() => Array.from(new Set(runIds)).sort(), [runIds])
  const key = ids.length > 0 ? keyPart(ids) : null
  // `keepPreviousData`: when a new failed build changes the id set, the
  // groups already drawn stay (the header button, the chips) while the new
  // set loads; a run the old answer did not hold reads "loading" meanwhile.
  const { data, error, isLoading } = useSWR<RunSignatureBatch>(
    key ? ['run-signatures', key] : null,
    () => runListService.getFailureSignatures(ids),
    { revalidateOnFocus: false, shouldRetryOnError: false, keepPreviousData: true },
  )
  return { data: key ? data : undefined, error, isLoading: Boolean(key) && isLoading }
}

/** `enabled: false` asks nothing (the page has no runs list to filter yet: loading or failed). */
export function useRunBranches(days: number, enabled = true) {
  const projectId = useActiveProjectId()
  const ask = enabled && projectId !== null && projectId !== ALL_PROJECTS_ID
  return useProjectScopedSWR<RunBranchList>(
    'run-branches',
    (pid) => runListService.getBranches(pid ?? '', days),
    { revalidateOnFocus: false, shouldRetryOnError: false },
    [days],
    ask,
  )
}
