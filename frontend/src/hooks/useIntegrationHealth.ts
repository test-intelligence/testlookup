import useSWR from 'swr'
import { appMutate } from '@/utils/swrCacheMutate'
import {
  type HealthTrend,
  type IntegrationStatus,
  type ProbeHistoryEntry,
  getAllStatus,
  getHealthTrends,
  getProviderHistory,
} from '@/services/integrationHealthService'

/**
 * Integration-health fetches as SWR hooks.
 *
 * Replaces a single load-on-mount `useEffect(() => { load() }, [load])` in
 * IntegrationHealthPage — the kind of effect the react-hooks `set-state-in-effect`
 * rule (correctly) discourages, since the effect drove `statuses`/`trends`/
 * `history`/`loading` state synchronously off the active tab. SWR now owns that
 * state declaratively, one hook per dataset.
 *
 * Status is always fetched (it feeds the status tab, the history-tab provider
 * dropdown, and the always-rendered workflow timeline). Trends and history are
 * gated by `enabled` so the heavier per-tab queries only run while their tab is
 * mounted — mirroring the old `if (tab === ...)` branches in `load()`. History
 * also requires a selected provider, matching the old `selectedProvider` guard.
 * `shouldRetryOnError: false` surfaces a failed load immediately, like the old
 * single-shot `try/catch`.
 */
const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

const STATUS_KEY = 'integration-health-status'
const TRENDS_KEY = 'integration-health-trends'
const HISTORY_KEY = 'integration-health-history'

/** `enabled` false asks nothing: the probes are QA lead and above, and a 403 toasts. */
export function useIntegrationStatus(enabled = true) {
  const { data, error, isLoading } = useSWR<IntegrationStatus[]>(
    enabled ? ([STATUS_KEY] as const) : null,
    getAllStatus,
    OPTS,
  )
  return { statuses: data ?? [], isLoading, isError: !!error }
}

/**
 * W10 INT-04: the Integration Health page's read — `GET /status?scope=all`: the
 * deployment-wide rows, then every project's GitHub / GitLab row and the paused
 * webhooks. Its own key (`[STATUS_KEY, 'all']`) so the global read above keeps
 * its cache; `refreshIntegrationHealth` matches both on `key[0]`.
 */
export function useIntegrationStatusAll(enabled: boolean) {
  const { data, error, isLoading } = useSWR<IntegrationStatus[]>(
    enabled ? ([STATUS_KEY, 'all'] as const) : null,
    () => getAllStatus('all'),
    OPTS,
  )
  return { statuses: data ?? [], isLoading, isError: !!error }
}

export function useHealthTrends(enabled: boolean, days = 7) {
  const { data, error, isLoading } = useSWR<HealthTrend[]>(
    enabled ? ([TRENDS_KEY, days] as const) : null,
    ([, d]: readonly [string, number]) => getHealthTrends(d),
    OPTS,
  )
  return { trends: data ?? [], isLoading, isError: !!error }
}

/**
 * `projectId` (W10 INT-04) reads that project's row's history; without it the
 * deployment-wide history, called with exactly two arguments as before.
 */
export function useProviderHistory(provider: string, enabled: boolean, days = 7, projectId?: string) {
  const { data, error, isLoading } = useSWR<ProbeHistoryEntry[]>(
    enabled && provider ? ([HISTORY_KEY, provider, days, projectId ?? ''] as const) : null,
    ([, p, d, pid]: readonly [string, string, number, string]) =>
      pid ? getProviderHistory(p, d, pid) : getProviderHistory(p, d),
    OPTS,
  )
  return { history: data ?? [], isLoading, isError: !!error }
}

/**
 * Revalidate every integration-health dataset after a probe. Matches all three
 * SWR keys via the global mutate predicate, so whichever tab is mounted picks
 * up fresh data without the page threading individual mutators.
 */
export function refreshIntegrationHealth() {
  return appMutate(
    (key: unknown) =>
      Array.isArray(key) &&
      (key[0] === STATUS_KEY || key[0] === TRENDS_KEY || key[0] === HISTORY_KEY),
  )
}
