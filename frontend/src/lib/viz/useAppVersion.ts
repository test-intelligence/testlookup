import useSWR, { type SWRConfiguration } from 'swr'
import { known } from '@/components/layout/buildInfo'
import type { HealthDetails } from '@/hooks/useSystemHealth'

/**
 * Read the cache, never fetch. A `null` fetcher alone does not promise that:
 * SWR 2 falls back to a GLOBAL `fetcher` from `SWRConfig` when the hook's own
 * is null, so the day one is configured this hook would start requesting
 * `system-health` from every chart frame. With every revalidation off, SWR
 * only reports what is cached (and keeps reporting it as the poll updates it).
 */
const CACHE_ONLY: SWRConfiguration = {
  revalidateOnMount: false,
  revalidateIfStale: false,
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
  refreshInterval: 0,
}

/**
 * The running app version for an export's provenance footer (VIZ-606), or
 * `null` when it is not known.
 *
 * It READS the cache entry `useSystemHealth` fills (`'system-health'`, the
 * poll the sidebar's version badge and the degraded banner already make) and
 * never fetches (`CACHE_ONLY`). One more consumer of the version must not be
 * one more request per chart frame — and a page without the sidebar simply
 * exports without a version, which the footer then leaves out rather than
 * guessing.
 */
export function useAppVersion(): string | null {
  const { data } = useSWR<HealthDetails>('system-health', null, CACHE_ONLY)
  return known(data?.version) ?? null
}
