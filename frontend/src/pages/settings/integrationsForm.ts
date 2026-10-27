/**
 * Settings › Integrations, the form's model (W10 INT-01 / INT-02). Pure: the
 * page and its tests import the same strings and the same diff.
 *
 * The page used to PUT every prefilled field, so a value the server showed from
 * the deployment environment was saved back as if an admin had typed it, and a
 * later change of that environment variable never reached the app again
 * (contract §0.3). It now sends only what the reader changed.
 */
import type { IntegrationsConfigRead, IntegrationsConfigUpdate } from '@/services/appSettingsService'

/** On/off switches: sent when they differ from what was loaded. */
export const INTEGRATION_BOOL_FIELDS = [
  'jira_enabled',
  'splunk_enabled',
  'ocp_enabled',
  'slack_enabled',
  'teams_enabled',
] as const

/** Text fields: sent when they differ; a cleared one is sent as "" (the server hands that key back to the environment). */
export const INTEGRATION_TEXT_FIELDS = [
  'jira_domain',
  'jira_email',
  'jira_default_project_key',
  'splunk_base_url',
  'ocp_api_url',
  'ocp_default_namespace',
] as const

/** Secrets: never shown, so only a typed (non-blank) value is sent; an empty input sends nothing. */
export const INTEGRATION_SECRET_FIELDS = [
  'jira_api_token',
  'splunk_api_token',
  'ocp_sa_token',
  'slack_webhook_url',
  'teams_webhook_url',
] as const

/** Every field the page edits: the names a field-level 422 may carry. */
export const INTEGRATION_FORM_FIELDS = [
  ...INTEGRATION_BOOL_FIELDS,
  ...INTEGRATION_TEXT_FIELDS,
  ...INTEGRATION_SECRET_FIELDS,
] as const

export type IntegrationFormField = (typeof INTEGRATION_FORM_FIELDS)[number]

/** Under a text field whose shown value comes from the deployment's environment. */
export const ENV_HINT = 'From the deployment environment'
/** Beside a secret's label: stored here. */
export const SECRET_SET = '(set)'
/** Beside a secret's label: not stored here, the environment supplies it. */
export const SECRET_SET_IN_ENV = '(set in the environment)'
/** W20 ADM-10: the stored value is a Vault reference; the secret itself lives in Vault. */
export const SECRET_SET_VAULT = '(set — Vault)'
/** The inline line when Save finds nothing changed (and sends nothing). */
export const NO_CHANGES = 'No changes to save'
export const SAVED = 'Integrations saved'
/** Every failure that is not a field-level 422 (one toast). */
export const SAVE_FAILED = 'Could not save integrations.'

/**
 * INT-02 (D21): the Slack card's help line under the webhook URL. The page
 * renders `SLACK_CHANNEL_HELP_LEAD`, a link named `SLACK_CHANNEL_HELP_LINK` to
 * `SLACK_CHANNEL_HELP_TO`, then "."; `SLACK_CHANNEL_HELP` is the whole sentence
 * as it reads.
 */
export const SLACK_CHANNEL_HELP_LEAD = 'The channel is fixed by the incoming webhook. Route other channels per team in '
export const SLACK_CHANNEL_HELP_LINK = 'Team channels'
export const SLACK_CHANNEL_HELP_TO = '/settings/team-channels'
export const SLACK_CHANNEL_HELP = `${SLACK_CHANNEL_HELP_LEAD}${SLACK_CHANNEL_HELP_LINK}.`

/** Which `*_set` flag of the read says a secret is stored. */
const SECRET_SET_FLAG: Record<(typeof INTEGRATION_SECRET_FIELDS)[number], keyof IntegrationsConfigRead> = {
  jira_api_token: 'jira_token_set',
  splunk_api_token: 'splunk_token_set',
  ocp_sa_token: 'ocp_token_set',
  slack_webhook_url: 'slack_webhook_set',
  teams_webhook_url: 'teams_webhook_set',
}

/** The form as loaded: every switch and text value the server shows; no secret (they are never read back). */
export function initialIntegrationForm(cfg: IntegrationsConfigRead): IntegrationsConfigUpdate {
  return {
    jira_enabled: cfg.jira_enabled,
    jira_domain: cfg.jira_domain ?? '',
    jira_email: cfg.jira_email ?? '',
    jira_default_project_key: cfg.jira_default_project_key ?? '',
    splunk_enabled: cfg.splunk_enabled,
    splunk_base_url: cfg.splunk_base_url ?? '',
    ocp_enabled: cfg.ocp_enabled,
    ocp_api_url: cfg.ocp_api_url ?? '',
    ocp_default_namespace: cfg.ocp_default_namespace ?? '',
    slack_enabled: cfg.slack_enabled,
    teams_enabled: cfg.teams_enabled,
  }
}

/**
 * Only what the reader changed, as the PUT body. An untouched form is `{}`: a
 * value echoed from the environment is never sent back.
 */
export function changedIntegrationFields(
  initial: IntegrationsConfigUpdate,
  form: IntegrationsConfigUpdate,
): IntegrationsConfigUpdate {
  const out: Record<string, string | boolean> = {}
  for (const key of INTEGRATION_BOOL_FIELDS) {
    const value = form[key]
    if (typeof value === 'boolean' && value !== (initial[key] ?? false)) out[key] = value
  }
  for (const key of INTEGRATION_TEXT_FIELDS) {
    const value = form[key] ?? ''
    if (value !== (initial[key] ?? '')) out[key] = value
  }
  for (const key of INTEGRATION_SECRET_FIELDS) {
    const value = form[key]
    if (typeof value === 'string' && value.trim() !== '') out[key] = value
  }
  return out as IntegrationsConfigUpdate
}

/** The read's `environment_fields` (an older server sends none: nothing comes from the environment, as far as the page can tell). */
export function environmentFields(cfg: IntegrationsConfigRead | null | undefined): ReadonlySet<string> {
  return new Set(cfg?.environment_fields ?? [])
}

/** The text field shows the deployment environment's value: the key is env-sourced and the reader has not changed it. */
export function showsEnvHint(
  key: (typeof INTEGRATION_TEXT_FIELDS)[number],
  envFields: ReadonlySet<string>,
  initial: IntegrationsConfigUpdate,
  form: IntegrationsConfigUpdate,
): boolean {
  return envFields.has(key) && (form[key] ?? '') === (initial[key] ?? '')
}

/** The qualifier beside a secret's label: `(set in the environment)`, `(set)`, or null when none is stored. */
export function secretStateLabel(
  key: (typeof INTEGRATION_SECRET_FIELDS)[number],
  cfg: IntegrationsConfigRead,
  envFields: ReadonlySet<string>,
): string | null {
  if (cfg.vault_fields?.includes(key)) return SECRET_SET_VAULT
  if (envFields.has(key)) return SECRET_SET_IN_ENV
  return cfg[SECRET_SET_FLAG[key]] === true ? SECRET_SET : null
}
