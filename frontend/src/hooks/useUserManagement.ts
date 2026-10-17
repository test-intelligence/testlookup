import useSWR from 'swr'
import { appMutate as mutate } from '@/utils/swrCacheMutate'
import { userManagementService, type UserRole } from '@/services/userManagementService'

export function useUsers(params?: { is_active?: boolean; role?: UserRole; page_size?: number }) {
  const key = params ? ['/api/v1/users', params] : '/api/v1/users'
  return useSWR(key, () => userManagementService.listUsers(params))
}

export function useProjectMembers(projectId: string | null) {
  return useSWR(
    projectId ? `/api/v1/projects/${projectId}/members` : null,
    () => userManagementService.listProjectMembers(projectId as string),
  )
}

export function refreshUsers() {
  // Match every users key, not just the bare string. ``useUsers`` keys on
  // ``['/api/v1/users', params]`` once filters are passed, so mutating the
  // literal string refreshed nothing on a filtered view -- a role change
  // would appear to succeed and the row would keep its old value until the
  // next revalidation.
  //
  // Revalidate WITHOUT clearing (W27b): the matcher alone re-reads every
  // matched key and keeps its data until the answer lands. The old
  // `mutate(matcher, undefined, { revalidate: true })` wrote `undefined` into
  // every users key first, so `UsersTab` fell back to its loading spinner and
  // unmounted the open Add User / Invite User form holding the one-time
  // temporary password or invitation link: the secret was never shown.
  return mutate(
    key =>
      key === '/api/v1/users' ||
      (Array.isArray(key) && key[0] === '/api/v1/users'),
  )
}

// ``useApiKeys``/``refreshApiKeys`` used to live here too, keyed on the bare
// string '/api/v1/keys' while hooks/useApiKeys keys on ['api-keys', projectId].
// Two key spaces for one resource: neither matcher could match the other's key,
// so a revoke on one page left the other page listing the revoked key. Both
// consumers now import from '@/hooks/useApiKeys'.