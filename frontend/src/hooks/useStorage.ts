import useSWR from 'swr'
import { storageService } from '@/services/storageService'
import type { DeletedProjectsStorage, ProjectStorage } from '@/types/storage'

/**
 * Storage footprint for one project.
 *
 * `revalidateOnFocus: false` — the figure costs a paginated object-store
 * listing on the backend, so it is not something to re-fetch every time the
 * window regains focus. The panel exposes an explicit refresh instead.
 */
export function useProjectStorage(projectId: string | null) {
  const id = projectId
  return useSWR<ProjectStorage>(
    id === null ? null : `/api/v1/projects/${id}/storage`,
    id === null ? null : () => storageService.project(id),
    { revalidateOnFocus: false },
  )
}

/**
 * Deployment-wide footprint of deleted projects. Not project-scoped — the
 * whole point is data whose project is gone.
 */
export function useDeletedProjectStorage(enabled: boolean) {
  return useSWR<DeletedProjectsStorage>(
    enabled ? '/api/v1/admin/storage/deleted-projects' : null,
    enabled ? () => storageService.deletedProjects() : null,
    { revalidateOnFocus: false },
  )
}
