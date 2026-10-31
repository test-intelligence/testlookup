import useSWR, { useSWRConfig } from 'swr'

import { projectDataService } from '@/services/projectDataService'
import { isActiveJob, JOB_POLL_MS, type ProjectDataJob, type ProjectDataJobList } from '@/types/projectData'

/**
 * W24 ADM-05: the project-data reads (SWR).
 *
 * - `useProjectExports(projectId)` — the project's newest export(s); null key without a project.
 * - `useMyImports(enabled)` — the caller's newest import(s); null key unless enabled (ADMIN only).
 * - `useProjectDataJob(jobId)` — one job, re-read every `JOB_POLL_MS` while it is queued / running and not at
 *   all once it is terminal; never without a job id.
 *
 * The job's `refreshInterval` is a NUMBER recomputed from the cached job on every render, never SWR's function
 * form: SWR re-reads a function only when a timer it armed fires, so a function that once answered 0 never
 * re-arms (the W22b demo bug). New data re-renders the hook, the number changes, and SWR re-arms at once.
 */

/** SWR keys, exported so a card can `mutate` them after a POST. */
export const projectExportsKey = (projectId: string, limit = 1) => `project-data/exports/${projectId}?limit=${limit}`
export const myImportsKey = (limit = 1) => `project-data/imports/mine?limit=${limit}`
export const projectDataJobKey = (jobId: string) => `project-data/jobs/${jobId}`

/** `JOB_POLL_MS` while the job is queued / running, else 0 (polling off). */
export function projectDataJobRefreshInterval(job: ProjectDataJob | null | undefined): number {
  return isActiveJob(job) ? JOB_POLL_MS : 0
}

export function useProjectExports(projectId: string | null | undefined, limit = 1) {
  return useSWR<ProjectDataJobList>(
    projectId ? projectExportsKey(projectId, limit) : null,
    () => projectDataService.listExports(projectId as string, limit),
    { revalidateOnFocus: false },
  )
}

export function useMyImports(enabled: boolean, limit = 1) {
  return useSWR<ProjectDataJobList>(
    enabled ? myImportsKey(limit) : null,
    () => projectDataService.listMyImports(limit),
    { revalidateOnFocus: false },
  )
}

export function useProjectDataJob(jobId: string | null | undefined) {
  const key = jobId ? projectDataJobKey(jobId) : null
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(key)?.data as ProjectDataJob | undefined) : undefined
  return useSWR<ProjectDataJob>(
    key,
    () => projectDataService.getJob(jobId as string),
    { refreshInterval: projectDataJobRefreshInterval(cached), revalidateOnFocus: false },
  )
}
