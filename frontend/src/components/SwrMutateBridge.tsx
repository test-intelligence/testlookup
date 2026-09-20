import { useEffect } from 'react'
import { useSWRConfig } from 'swr'
import { registerAppMutate, resetAppMutate } from '@/utils/swrCacheMutate'

/**
 * Publishes the provider-bound `mutate` so module-scope helpers can invalidate
 * the cache the app actually uses. Renders nothing.
 *
 * Must be INSIDE `<SWRConfig>`: `useSWRConfig()` reads the nearest provider,
 * and it is remounted per session generation along with the cache it belongs
 * to, so a stale binding cannot outlive its Map.
 */
export function SwrMutateBridge() {
  const { mutate } = useSWRConfig()
  useEffect(() => {
    registerAppMutate(mutate)
    return () => resetAppMutate()
  }, [mutate])
  return null
}
