import useSWR from 'swr'
import { appMutate } from '@/utils/swrCacheMutate'
import { apiKeyService } from '@/services/apiKeyService'

export function useApiKeys(projectId?: string | null) {
  return useSWR(['api-keys', projectId ?? 'me'], () => apiKeyService.list(projectId))
}

/**
 * The single refresher for `/api/v1/keys`.
 *
 * There used to be a second `useApiKeys`/`refreshApiKeys` pair in
 * `hooks/useUserManagement`, keyed on the bare string `'/api/v1/keys'` while
 * this one keys on `['api-keys', projectId]`. Neither matcher could ever match
 * the other's key, so revoking a key on one page left the other page's list
 * showing it — on a security-relevant list. One key space now.
 *
 * `appMutate`, not the `swr` module's mutate: this app supplies its own cache
 * provider, so the module-level mutate is bound to a different, empty cache and
 * matches nothing (see `utils/swrCacheMutate`).
 */
export function refreshApiKeys() {
  return appMutate((key: unknown) => Array.isArray(key) && key[0] === 'api-keys')
}
