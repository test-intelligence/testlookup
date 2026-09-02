import type { DeletedProjectsStorage, ProjectStorage } from '@/types/storage'
import { getData } from './http'

/**
 * Storage footprints (S3) — how much a project actually costs, and what
 * deleted projects are still holding.
 *
 * Both endpoints are ADMIN-only on the backend and read-only: they report what
 * a purge would reclaim, they never delete.
 *
 * Rides the shared Axios base in `services/api.ts` via the `http` helpers.
 */
export const storageService = {
  project: (projectId: string) =>
    getData<ProjectStorage>(`/api/v1/projects/${projectId}/storage`),

  /** Deployment-wide, not project-scoped. */
  deletedProjects: () =>
    getData<DeletedProjectsStorage>('/api/v1/admin/storage/deleted-projects'),
}
