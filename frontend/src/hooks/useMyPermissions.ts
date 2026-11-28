import useSWR from 'swr'
import { permissionsService } from '@/services/permissionsService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import type { MyPermissions, Permission } from '@/types/permissions'

export interface CanOptions {
  /** Ask about a write (default): instance and project roles intersect. `false` asks about a read. */
  write?: boolean
}

/** Pure: does `perms` grant `permission`? `false` while loading or on an error (nothing forbidden flashes). */
export function canWith(perms: MyPermissions | undefined, permission: Permission, options: CanOptions = {}): boolean {
  if (!perms) return false
  const held = options.write === false ? perms.read_permissions : perms.write_permissions
  return held.includes(permission)
}

/**
 * Enterprise roles E00-S04: the caller's permissions in one project (the active one by default).
 *
 * Kept apart from `usePermissions()`, which many components call and which answers by rank only (a job role
 * reads as VIEWER there): this hook fetches, so only a surface that needs a permission answer pays for it, and
 * it lives outside the eager shell bundle. One request per project (SWR de-duplicates); "All projects" asks
 * for the global sets.
 */
export function useMyPermissions(projectId?: string | null) {
  const activeProjectId = useProjectStore((state) => state.activeProjectId)
  const signedIn = useAuthStore((state) => Boolean(state.user))
  const chosen = projectId === undefined ? activeProjectId : projectId
  const scoped = chosen && chosen !== ALL_PROJECTS_ID ? chosen : null
  const { data, error, isLoading } = useSWR<MyPermissions>(
    signedIn ? ['me-permissions', scoped ?? 'global'] : null,
    () => permissionsService.getMine(scoped),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    permissions: data,
    error,
    isLoading,
    /** `can('release.write')` — false until loaded. Conditional grants count: the server checks the resource. */
    can: (permission: Permission, options?: CanOptions) => canWith(data, permission, options),
    /** The condition the server checks for a conditional grant, if any (e.g. `owns_failure`). */
    conditionFor: (permission: Permission) => data?.conditional?.[permission] ?? [],
  }
}
