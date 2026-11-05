import useSWR, { useSWRConfig } from 'swr'

import { subjectRightsService } from '@/services/subjectRightsService'
import {
  isActiveJob,
  JOB_POLL_MS,
  type ErasurePreview,
  type SubjectJob,
  type SubjectJobList,
} from '@/types/subjectRights'

/**
 * W31 STRAT-12: the subject-rights reads (SWR).
 *
 * - `useMyExports()` — my own exports of myself (Settings › Profile).
 * - `useUserJobs(userId)` — a person's export / erasure jobs (ADMIN, Settings › Users); null key without an id.
 * - `useSubjectJob(jobId)` — one job, re-read every `JOB_POLL_MS` while it is queued / running and not at all
 *   once it is terminal or an erasure reached `phase === 'done'`; never without a job id.
 * - `useErasurePreview(userId, enabled)` — the erasure preview (ADMIN); no polling, no focus revalidation, NO
 *   request unless enabled; `mutate()` re-reads it on demand and after an erase.
 *
 * The job's `refreshInterval` is a NUMBER recomputed from the cached job on every render, never SWR's function
 * form: SWR re-reads a function only when a timer it armed fires, so a function that once answered 0 never
 * re-arms (the W22b demo bug). New data re-renders the hook, the number changes, and SWR re-arms at once.
 */

/** SWR keys, exported so a card can `mutate` them after a POST. */
export const myExportsKey = () => 'subject-rights/me/exports'
export const userJobsKey = (userId: string) => `subject-rights/users/${userId}/jobs`
export const subjectJobKey = (jobId: string) => `subject-rights/jobs/${jobId}`
export const erasurePreviewKey = (userId: string) => `subject-rights/users/${userId}/erasure-preview`

/** `JOB_POLL_MS` while the job is queued / running (an erasure before `done`), else 0 (polling off). */
export function subjectJobRefreshInterval(job: SubjectJob | null | undefined): number {
  return isActiveJob(job) ? JOB_POLL_MS : 0
}

export function useMyExports() {
  return useSWR<SubjectJobList>(myExportsKey(), () => subjectRightsService.listMyExports(), {
    revalidateOnFocus: false,
  })
}

export function useUserJobs(userId: string | null | undefined) {
  return useSWR<SubjectJobList>(
    userId ? userJobsKey(userId) : null,
    () => subjectRightsService.listUserJobs(userId as string),
    { revalidateOnFocus: false },
  )
}

export function useSubjectJob(jobId: string | null | undefined) {
  const key = jobId ? subjectJobKey(jobId) : null
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(key)?.data as SubjectJob | undefined) : undefined
  return useSWR<SubjectJob>(
    key,
    () => subjectRightsService.getJob(jobId as string),
    { refreshInterval: subjectJobRefreshInterval(cached), revalidateOnFocus: false },
  )
}

export function useErasurePreview(userId: string | null | undefined, enabled: boolean) {
  return useSWR<ErasurePreview>(
    enabled && userId ? erasurePreviewKey(userId) : null,
    () => subjectRightsService.getErasurePreview(userId as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}
