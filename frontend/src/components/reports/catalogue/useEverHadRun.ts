/**
 * K6 (Wave 2.6): "has this project EVER had a run?" — the unfiltered existence
 * probe a catalogue frame is handed as `everHadData`.
 *
 * It is the only thing that may turn an empty chart into "no data yet, ingest
 * test results" instead of "nothing matches the current filters", so it must
 * not inherit a filter: no window, no suite and no release (`useRuns`'s
 * `ignoreGlobalRelease`). Otherwise a project with thousands of runs, viewed
 * under a release that has none, would be told it has never run anything
 * (lesson: a global filter poisons an existence probe).
 *
 * The params are EXACTLY Overview's own probe (`OverviewPage`: `useRuns({ page:
 * 1, size: 1 }, { ignoreGlobalRelease: true })`), but this read is QUIET, and a
 * quiet read is its own SWR entry (R1B-7): sharing Overview's would let
 * whichever fetcher fired first decide whether a failure toasts, for both. On
 * Overview with the catalogue on that is one more small request; on every
 * other page nothing else asks.
 *
 *   - `null` while disabled or while the probe is in flight;
 *   - `true` once a run is seen — and then it LATCHES for that project and
 *     stops asking (`useRuns` polls every 15 s, and a project that has had a
 *     run cannot stop having had one);
 *   - `false` when the project has no run at all (it keeps asking, so the
 *     first ingest flips it);
 *   - `true` if the probe FAILS: the frame then says "nothing matches" for an
 *     empty chart, which is at worst unhelpful, where `false` would show the
 *     first-run setup on a populated project and `null` would hold a skeleton
 *     forever. The probe is QUIET (`useRuns`' `quiet`): its failure is not
 *     the reader's problem, so it never raises the page-wide error toast.
 */
import { useState } from 'react'
import { useRuns } from '@/hooks/useRuns'
import { useActiveProjectId } from '@/hooks/useProjectScopedSWR'

/** Overview's probe parameters, verbatim (module-level: one identity, a stable SWR key). */
export const EVER_HAD_RUN_PARAMS = { page: 1, size: 1 } as const

export function useEverHadRun(enabled: boolean): boolean | null {
  const projectId = useActiveProjectId()
  const [latchedFor, setLatchedFor] = useState<string | null>(null)
  const latched = projectId !== null && latchedFor === projectId

  const { data, error } = useRuns(EVER_HAD_RUN_PARAMS, { ignoreGlobalRelease: true, enabled: enabled && !latched, quiet: true })
  const hasRun = data === undefined ? undefined : (data.items?.length ?? 0) > 0
  // Latched during render (React's "adjust state while rendering"), not in an
  // effect: the next render already has no key, so no poll is scheduled.
  if (hasRun === true && projectId !== null && latchedFor !== projectId) setLatchedFor(projectId)

  if (!enabled) return null
  if (latched || hasRun === true) return true
  if (hasRun === false) return false
  if (error !== undefined) return true
  return null
}
