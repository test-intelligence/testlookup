import useSWR from 'swr'

import { appSettingsService, type IntegrationsConfigRead } from '@/services/appSettingsService'

/**
 * Workspace integration configuration.
 *
 * `GET /settings/integrations` requires QA_LEAD or above, so a developer or
 * viewer gets a 403 here. Callers must treat "we could not read it" as its own
 * state — not as "connected" and not as "disconnected".
 */
export function useIntegrationsConfig() {
  const { data, error, isLoading } = useSWR<IntegrationsConfigRead>(
    'settings/integrations',
    () => appSettingsService.getIntegrationsConfig(),
    { revalidateOnFocus: false },
  )
  return { config: data, error, isLoading }
}

export type JiraBridgeState = 'connected' | 'not_configured' | 'unknown'

/** Only the three fields that decide the answer, so callers can't over-supply. */
type JiraBridgeFields = Pick<IntegrationsConfigRead, 'jira_enabled' | 'jira_domain' | 'jira_token_set'>

/**
 * Whether Jira is actually wired up.
 *
 * "Enabled" alone is not a connection: the toggle can be on with no domain and
 * no credential, which is a bridge that cannot reach anything.
 */

export function jiraBridgeState(config: JiraBridgeFields | undefined): JiraBridgeState {
  // Still loading, or a 403 because the viewer isn't a QA lead. Either way the
  // page has not been told anything and must not claim it has.
  if (!config) return 'unknown'
  if (config.jira_enabled && config.jira_domain && config.jira_token_set) return 'connected'
  return 'not_configured'
}
