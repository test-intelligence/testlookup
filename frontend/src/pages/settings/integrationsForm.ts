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
import { TEMPLATES_MAX, type PrometheusMetricTemplate } from '@/types/prometheus'

/** On/off switches: sent when they differ from what was loaded. */
export const INTEGRATION_BOOL_FIELDS = [
  'jira_enabled',
  'splunk_enabled',
  'ocp_enabled',
  'slack_enabled',
  'teams_enabled',
  'prometheus_enabled',
] as const

/**
 * Text fields: sent when they differ; a cleared one is sent as "" (the server
 * hands that key back to the environment — except the Prometheus URL, which
 * is saved-only, W23d D-9: "" clears it).
 */
export const INTEGRATION_TEXT_FIELDS = [
  'jira_domain',
  'jira_email',
  'jira_default_project_key',
  'splunk_base_url',
  'ocp_api_url',
  'ocp_default_namespace',
  'prometheus_base_url',
] as const

/** Secrets: never shown, so only a typed (non-blank) value is sent; an empty input sends nothing. */
export const INTEGRATION_SECRET_FIELDS = [
  'jira_api_token',
  'splunk_api_token',
  'ocp_sa_token',
  'slack_webhook_url',
  'teams_webhook_url',
  'prometheus_bearer_token',
] as const

/** W23d AGT-15: the ADMIN's metric templates, sent whole when the list differs (compared by value). */
export const PROMETHEUS_TEMPLATES_FIELD = 'prometheus_metric_templates' as const

/** Every field the page edits: the names a field-level 422 may carry. */
export const INTEGRATION_FORM_FIELDS = [
  ...INTEGRATION_BOOL_FIELDS,
  ...INTEGRATION_TEXT_FIELDS,
  ...INTEGRATION_SECRET_FIELDS,
  PROMETHEUS_TEMPLATES_FIELD,
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
  prometheus_bearer_token: 'prometheus_token_set',
}

/** A template list as the form holds it: a copy, so editing a row never mutates what was loaded. */
function copyTemplates(list: readonly PrometheusMetricTemplate[] | null | undefined): PrometheusMetricTemplate[] {
  return (list ?? []).map(t => ({ id: t.id, label: t.label, promql: t.promql, unit: t.unit ?? 'count' }))
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
    prometheus_enabled: cfg.prometheus_enabled ?? false,
    prometheus_base_url: cfg.prometheus_base_url ?? '',
    prometheus_metric_templates: copyTemplates(cfg.prometheus_metric_templates),
  }
}

/** Same templates, same order, same fields: an untouched (or edited-back) list is not sent. */
function sameTemplates(a: readonly PrometheusMetricTemplate[] | undefined, b: readonly PrometheusMetricTemplate[] | undefined): boolean {
  return JSON.stringify(copyTemplates(a)) === JSON.stringify(copyTemplates(b))
}

/**
 * Only what the reader changed, as the PUT body. An untouched form is `{}`: a
 * value echoed from the environment is never sent back.
 */
export function changedIntegrationFields(
  initial: IntegrationsConfigUpdate,
  form: IntegrationsConfigUpdate,
): IntegrationsConfigUpdate {
  const out: Record<string, unknown> = {}
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
  // `[]` is meaningful ("Use the defaults"); only a list that differs is sent.
  const templates = form[PROMETHEUS_TEMPLATES_FIELD]
  if (Array.isArray(templates) && !sameTemplates(initial[PROMETHEUS_TEMPLATES_FIELD], templates)) {
    out[PROMETHEUS_TEMPLATES_FIELD] = copyTemplates(templates)
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

// ── W23d AGT-15: the Prometheus card's copy and probe line ─────────────────

export const PROMETHEUS_TITLE = 'Prometheus'
export const PROMETHEUS_INTRO =
  "Application metrics for the deep investigation's log stage. Queried only through the templates below; offline mode refuses every query."
export const PROMETHEUS_URL_PLACEHOLDER = 'http://prometheus.monitoring:9090'
/** Not "Base URL": the Splunk card has that label, and one label must name one input. */
export const PROMETHEUS_URL_LABEL = 'Prometheus URL'
export const PROMETHEUS_TOKEN_LABEL = 'Bearer token'
/** The saved block has no URL: absence is not health. */
export const PROMETHEUS_NOT_CONFIGURED = 'Not configured'
export const TEMPLATES_HEADING = 'Metric templates'
export const TEMPLATES_HELP = 'Each PromQL query names the service as {service}; the investigation fills it in.'
/** `prometheus_templates_source === 'default'` and the list is untouched. */
export const DEFAULTS_IN_USE = 'Defaults in use'
/** The list was emptied (Use the defaults): Save sends `[]`. */
export const DEFAULTS_AFTER_SAVE = 'The default templates are used after Save.'
export const ADD_TEMPLATE = 'Add template'
export const USE_DEFAULTS = 'Use the defaults'
export const REMOVE_TEMPLATE = 'Remove'
/** The unit select's words; the option VALUES stay the server's `TEMPLATE_UNITS` (`per_second`). */
export const TEMPLATE_UNIT_LABELS: Record<string, string> = {
  seconds: 'seconds', bytes: 'bytes', ratio: 'ratio', per_second: 'per second', count: 'count',
}
/** prometheus_vocab.TEMPLATES_MAX (mirrored in types/prometheus). */
export { TEMPLATES_MAX }
export const TEST_CONNECTION = 'Test connection'
export const TEST_SAVED_NOTE = 'Tests the saved settings — save first.'
export const TESTING = 'Testing…'
export const TEST_FAILED = 'Could not test the connection.'

/**
 * The line beside the templates heading: the list was emptied (defaults after
 * Save), or the server's defaults are shown untouched; else nothing.
 */
export function templatesNote(cfg: IntegrationsConfigRead, form: IntegrationsConfigUpdate): string | null {
  const list = form[PROMETHEUS_TEMPLATES_FIELD]
  if (Array.isArray(list) && list.length === 0) return DEFAULTS_AFTER_SAVE
  if (cfg.prometheus_templates_source === 'default' && sameTemplates(cfg.prometheus_metric_templates ?? [], list)) {
    return DEFAULTS_IN_USE
  }
  return null
}

/** A new, empty template row (the admin fills it in; the server checks it on Save). */
export function blankTemplate(): PrometheusMetricTemplate {
  return { id: '', label: '', promql: '', unit: 'count' }
}

/**
 * The probe's answer as one line: "Test connection: healthy (42 ms)";
 * anything else carries the probe's own sentence ("Test connection: skipped —
 * Prometheus is switched off or has no URL").
 */
export function probeLine(result: { status: string; response_ms?: number | null; message?: string | null }): string {
  const status = result.status || 'unknown'
  if (status === 'healthy') {
    const ms = typeof result.response_ms === 'number' && Number.isFinite(result.response_ms)
      ? ` (${Math.round(result.response_ms)} ms)`
      : ''
    return `Test connection: healthy${ms}`
  }
  const message = typeof result.message === 'string' ? result.message.trim() : ''
  return message ? `Test connection: ${status} — ${message}` : `Test connection: ${status}`
}
