import useSWR from 'swr'
import { getAuthOptions } from '@/services/authOptions'

/**
 * Whether this server is the public read-only demo (`GET /api/v1/auth/options`
 * → `demo_mode`). The answer cannot change while the page is open, so it is
 * fetched once and never revalidated. Unknown (loading, failed, older server)
 * reads as false: no banner is the safe default for a real deployment.
 */
export function useDemoMode(): boolean {
  const { data } = useSWR('auth-options', getAuthOptions, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    revalidateIfStale: false,
    shouldRetryOnError: false,
  })
  return data?.demo_mode === true
}
