import useSWR from 'swr'
import {
  type IdentityEvent,
  type IdentitySyncStatus,
  type SCIMToken,
  type SSOConfig,
  getIdentitySyncStatus,
  listIdentityEvents,
  listSCIMTokens,
  listSSOConfigs,
} from '@/services/ssoService'

export type SSOTab = 'config' | 'scim' | 'events' | 'sync'

export interface SSOTabData {
  configs: SSOConfig[]
  scimTokens: SCIMToken[]
  events: IdentityEvent[]
  syncStatus: IdentitySyncStatus | null
}

const EMPTY: SSOTabData = { configs: [], scimTokens: [], events: [], syncStatus: null }

async function fetchTab(tab: SSOTab): Promise<SSOTabData> {
  switch (tab) {
    case 'config':
      return { ...EMPTY, configs: await listSSOConfigs() }
    case 'scim':
      return { ...EMPTY, scimTokens: await listSCIMTokens() }
    case 'events': {
      const result = await listIdentityEvents({ days: 30, page_size: 50 })
      return { ...EMPTY, events: result.items }
    }
    case 'sync':
      return { ...EMPTY, syncStatus: await getIdentitySyncStatus() }
  }
}

/**
 * SSO settings tab data as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { loadData() })` in SSOSettingsPage
 * that drove the per-tab `configs`/`scimTokens`/`events`/`syncStatus`/`loading`
 * state from inside the effect — the pattern the react-hooks
 * `set-state-in-effect` rule (correctly) discourages. SWR owns the
 * loading/data state declaratively, keyed on the active `tab` so switching tabs
 * fetches exactly the slice the old effect did (only the active tab's data is
 * populated; the others stay empty, matching the original render guards).
 *
 * `refresh` (SWR's `mutate`) revalidates the active tab, exactly as the old
 * `loadData()` calls after a create/toggle/delete/revoke mutation did. `error`
 * surfaces a failed load as a string for the inline banner, matching the prior
 * `setError(...)` in the effect's `catch`.
 */
export function useSSOTabData(tab: SSOTab) {
  const { data, isLoading, error, mutate } = useSWR<SSOTabData>(
    ['sso-tab', tab],
    () => fetchTab(tab),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return {
    data: data ?? EMPTY,
    isLoading,
    error: error instanceof Error ? error.message : error ? 'Failed to load data' : null,
    refresh: mutate,
  }
}
