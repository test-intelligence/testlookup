import type { MyPermissions } from '@/types/permissions'
import { getData } from './http'

/** Enterprise roles E00-S04: what the signed-in caller may do. Rides the shared Axios base. */
export const permissionsService = {
  /** Omit `projectId` for the global sets. A project the caller cannot read answers 403. */
  getMine: (projectId?: string | null) =>
    getData<MyPermissions>('/api/v1/me/permissions', {
      params: projectId ? { project_id: projectId } : undefined,
    }),
}
