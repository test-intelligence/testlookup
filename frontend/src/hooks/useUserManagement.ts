import useSWR, { mutate } from 'swr'
import { userManagementService, type UserRole } from '@/services/userManagementService'

export function useUsers(params?: { is_active?: boolean; role?: UserRole }) {
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
  return mutate('/api/v1/users')
}

export function refreshApiKeys() {
  return mutate('/api/v1/keys')
}