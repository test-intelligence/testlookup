import useSWR from 'swr'
import { criteriaDeletionService } from '@/services/retentionService'
import type { DeletionJob } from '@/types/retention'

/**
 * Deletion jobs for a project, newest first.
 *
 * `refreshInterval` is deliberate: a criteria job runs asynchronously and the
 * operator is watching for it to finish. Polling stops once nothing is in
 * flight, so a settled list does not poll forever.
 */
export function useDeletionJobs(projectId: string | null, enabled = true) {
  const active = projectId !== null && enabled
  return useSWR<{ jobs: DeletionJob[] }>(
    active ? `/api/v1/projects/${projectId}/deletion/jobs` : null,
    active ? () => criteriaDeletionService.jobs(projectId) : null,
    {
      revalidateOnFocus: false,
      refreshInterval: (data) =>
        (data?.jobs ?? []).some((j) => j.status === 'running' || j.status === 'queued')
          ? 5000
          : 0,
    },
  )
}
