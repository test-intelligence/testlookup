import useSWR, { mutate } from 'swr'
import { apiKeyService } from '@/services/apiKeyService'

export function useApiKeys(projectId?: string | null) {
  return useSWR(['api-keys', projectId ?? 'me'], () => apiKeyService.list(projectId))
}

export function refreshApiKeys() {
  return mutate((key: unknown) => Array.isArray(key) && key[0] === 'api-keys')
}
