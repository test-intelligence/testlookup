import useSWR, { unstable_serialize, useSWRConfig } from 'swr'
import { criteriaDeletionService } from '@/services/retentionService'
import type { DeletionJobList } from '@/types/retention'

/** A job in flight (`running` / `queued`) is polled this often. */
export const DELETION_JOBS_ACTIVE_POLL_MS = 5000
/** A job awaiting collection is polled this often, so `expired` appears without a reload (W22b). */
export const DELETION_JOBS_AWAITING_POLL_MS = 30000

/**
 * The jobs list's polling predicate: 5 s while any job is `running` / `queued`, else 30 s while any job is
 * `awaiting_collection`, else 0 (a settled list does not poll forever).
 */
export function deletionJobsRefreshInterval(data: DeletionJobList | undefined): number {
  const jobs = data?.jobs ?? []
  if (jobs.some((j) => j.status === 'running' || j.status === 'queued')) return DELETION_JOBS_ACTIVE_POLL_MS
  if (jobs.some((j) => j.status === 'awaiting_collection')) return DELETION_JOBS_AWAITING_POLL_MS
  return 0
}

/**
 * Deletion jobs for a project, newest first, with `total` (every job of the project, ignoring the limit).
 *
 * `refreshInterval` is deliberate: a criteria job runs asynchronously and the
 * operator is watching for it to finish. Polling stops once nothing is in
 * flight or waiting for its archive to be collected.
 */
export function useDeletionJobs(projectId: string | null, enabled = true) {
  const active = projectId !== null && enabled
  const key = active ? `/api/v1/projects/${projectId}/deletion/jobs` : null
  // A NUMBER derived from the cached list, not SWR's function form: SWR reads a function only when its polling
  // effect starts and after a timer it armed fires, so a list that was settled (0 → no timer) never started polling
  // when Execute queued a job — the card sat on "Queued" while the job was already awaiting collection (the W22b
  // local demo). New data re-renders this hook, the number changes, and SWR re-arms its timer
  // (`useDecidedAgentActions`' rule).
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(unstable_serialize(key))?.data as DeletionJobList | undefined) : undefined
  return useSWR<DeletionJobList>(
    key,
    active ? () => criteriaDeletionService.jobs(projectId) : null,
    {
      revalidateOnFocus: false,
      refreshInterval: deletionJobsRefreshInterval(cached),
    },
  )
}
