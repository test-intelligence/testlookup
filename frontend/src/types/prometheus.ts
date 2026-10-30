/**
 * W23d AGT-15: the Prometheus application-metrics integration (Settings › Integrations › Prometheus) and the
 * log stage's metric evidence.
 *
 * The constants mirror `backend/app/services/prometheus_vocab.py` value for value (pinned by
 * `backend/tests/test_payload_metrics_vocab_parity.py`, which reads this file as TEXT — keep every constant a
 * plain literal on its own `export const` line, numbers without separators, strings in one piece, records as one
 * `key: 'value',` line per entry closed by a column-0 `} as const`).
 *
 * The model never writes PromQL: an ADMIN saves metric templates, the stage picks them by id, and the server
 * substitutes `{service}`. Every query is refused under offline mode (D19). Absence is not health: an
 * unconfigured Prometheus reads `OUTCOME_SENTENCES.not_configured`, never "no anomalies". Every sentence is
 * rendered as React text.
 */

export const SETTINGS_KEYS = ['prometheus_enabled', 'prometheus_base_url', 'prometheus_metric_templates'] as const
/** The write-only secret (stored in `secret_refs`; the read answers `prometheus_token_set` only). */
export const SECRET_KEY = 'prometheus_bearer_token'
/** Replaced LITERALLY by the server; every other brace is PromQL's. */
export const SERVICE_PLACEHOLDER = '{service}'
export const SERVICE_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
export const TEMPLATE_ID_RE = /^[a-z][a-z0-9_]{1,39}$/
export const TEMPLATES_MAX = 12
export const TEMPLATE_LABEL_MAX = 80
export const TEMPLATE_PROMQL_MAX = 1000
export const TEMPLATES_PER_CALL_MAX = 4
/** The spark draws at most this many points per series. */
export const POINTS_MAX = 120
export const CLUSTERS_QUERIED_MAX = 3
/** Failure-window mean / baseline mean ≥ this → the series "rose". */
export const RATIO_ROSE = 1.5

export const TEMPLATE_UNITS = ['seconds', 'bytes', 'ratio', 'per_second', 'count'] as const
export type TemplateUnit = (typeof TEMPLATE_UNITS)[number]

/** One admin-written metric template (`schemas.PrometheusMetricTemplate`). */
export interface PrometheusMetricTemplate {
  /** TEMPLATE_ID_RE. */
  id: string
  label: string
  /** Must contain SERVICE_PLACEHOLDER. */
  promql: string
  /** One of TEMPLATE_UNITS; the server defaults it to `count`. */
  unit?: TemplateUnit | string
}

/** The defaults the server uses while none are saved (`templates_source: "default"`) — ids and labels only. */
export const DEFAULT_METRIC_TEMPLATES = [
  { id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds' },
  { id: 'http_5xx_rate', label: '5xx error rate', unit: 'per_second' },
  { id: 'memory_working_set', label: 'Memory working set', unit: 'bytes' },
  { id: 'cpu_usage', label: 'CPU usage', unit: 'ratio' },
] as const

export const OUTCOMES = [
  'ok', 'no_data', 'timed_out', 'http_error', 'auth_error', 'offline_mode', 'disabled', 'not_configured',
  'blocked_target', 'invalid_service', 'tool_not_allowed', 'no_context', 'config_unavailable',
] as const
export type MetricsOutcome = (typeof OUTCOMES)[number]

/** The stage's degrade sentences (server-written; `{status}` / `{service}` are filled in by the server). */
export const OUTCOME_SENTENCES = {
  offline_mode: 'Prometheus lookup disabled: offline mode',
  disabled: 'metrics unavailable: the Prometheus integration is switched off',
  not_configured: 'metrics unavailable: Prometheus is not configured',
  blocked_target: 'metrics unavailable: the Prometheus host is not allowed',
  timed_out: 'metrics unavailable: Prometheus timed out',
  http_error: 'metrics unavailable: Prometheus answered HTTP {status}',
  auth_error: 'metrics unavailable: Prometheus refused the token (HTTP {status})',
  invalid_service: 'metrics unavailable: the service name is not a valid label value',
  tool_not_allowed: 'metrics unavailable: the frozen agent configuration does not allow fetch_app_metrics',
  no_context: 'metrics unavailable: the failures name no service and failure time',
  config_unavailable: 'metrics unavailable: the integration settings could not be read',
  no_data: 'No metric data for {service} in the failure window.',
} as const

/** One series' finding (server-written; ratio with one decimal, e.g. 3.1). */
export const FINDING_SENTENCES = {
  rose: '{label} for {service} rose {ratio}× in the failure window',
  steady: '{label} for {service} stayed within {ratio}× of its baseline in the failure window',
  no_baseline: '{label} for {service}: no data before the failure window to compare with',
} as const

/** `PUT /api/v1/settings/integrations` 422 sentences (loc `prometheus_metric_templates.N.field`). */
export const TEMPLATE_ERRORS = {
  placeholder: 'Each template must contain {service} exactly where the service name goes.',
  duplicate_id: 'Template ids must be unique.',
  too_many: 'At most 12 metric templates.',
} as const

/** The probe's messages (`POST /api/v1/integration-health/probe?provider=prometheus`). */
export const PROBE_SENTENCES = {
  healthy: 'Prometheus API reachable',
  skipped_off: 'Prometheus is switched off or has no URL',
} as const
