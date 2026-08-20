import useSWR, { mutate } from 'swr'
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

export function useApiKeys() {
  return useSWR('/api/v1/keys', userManagementService.listApiKeys)
}

export function refreshUsers() {
  // Match every users key, not just the bare string. ``useUsers`` keys on
  // ``['/api/v1/users', params]`` once filters are passed, so mutating the
  // literal string refreshed nothing on a filtered view -- a role change
  // would appear to succeed and the row would keep its old value until the
  // next revalidation.
  return mutate(
    key =>
      key === '/api/v1/users' ||
      (Array.isArray(key) && key[0] === '/api/v1/users'),
    undefined,
    { revalidate: true },
  )
}

export function refreshApiKeys() {
  return mutate('/api/v1/keys')
}