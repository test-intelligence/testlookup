/**
 * W23d AGT-14 / AGT-15: the decision report's `contract_findings` (the API contract stage) and `log_findings`
 * (the log stage, now with application metrics) as the `Specialist evidence` row renders them.
 *
 * Both travel inside `structured_summary.decision_intelligence` as free-form dicts (`decision_report_agent.py`
 * copies them); a report written before W23d carries neither, or carries them without the new keys — so every
 * field is OPTIONAL and the readers below never throw: an unknown shape is `null`.
 *
 * Every string here was built by the server's code (violation sentences, metric sentences, ADMIN template
 * labels, a regex-checked service name) — still rendered as React text, never as HTML.
 */
import { OUTCOMES, POINTS_MAX, TEMPLATES_PER_CALL_MAX, type MetricsOutcome } from './prometheus'

// ── AGT-14: the API contract stage ──────────────────────────────────────────

/** One violation (`validate_api_contract` → `contract_agent`). */
export interface ContractViolation {
  /** `schema_drift` (a stable field is gone) or `type_drift` (its JSON type changed). */
  violation_type?: string
  severity?: 'critical' | 'warning' | string
  field_path?: string
  expected?: string
  actual?: string
  method?: string
  endpoint?: string
  /** The newest baseline run's build number ("drift since build 41"). */
  baseline_build?: string
  /** The server's sentence (`CONTRACT_SENTENCES.missing_field` / `type_changed`); field paths sit in backticks. */
  sentence?: string
}

/** `ContractAgentOutput` as the decision report carries it. */
export interface ContractFindings {
  status?: 'complete' | 'not_enough_evidence' | 'failed' | string
  violations?: ContractViolation[]
  violation_count?: number
  critical_count?: number
  drift_count?: number
  endpoints_checked?: string[]
  summary?: string
  suggests_product_bug?: boolean
}

// ── AGT-15: the log stage's application metrics ─────────────────────────────

/** `[epoch seconds, value]`; a `null` value = not measured (the spark's line breaks). */
export type MetricPoint = [number, number | null]

/** One template's series for one service (numbers, template id / label only — no Prometheus string). */
export interface AppMetricsSeries {
  template_id?: string
  label?: string
  /** One of TEMPLATE_UNITS. */
  unit?: string
  /** Oldest first, ≤ POINTS_MAX. */
  points?: MetricPoint[]
  baseline_mean?: number | null
  window_mean?: number | null
  window_max?: number | null
  /** window_mean / baseline_mean (`null` without a baseline). */
  ratio?: number | null
  /** ratio ≥ RATIO_ROSE. */
  rose?: boolean
  /** `FINDING_SENTENCES.rose` / `steady` / `no_baseline`, filled in by the server. */
  sentence?: string
}

/** One cluster's `app_metrics` block: an outcome, its sentence, and ≤ TEMPLATES_PER_CALL_MAX series. */
export interface AppMetricsFinding {
  outcome: MetricsOutcome
  /** `OUTCOME_SENTENCES[outcome]` for a degrade; the finding sentences joined for `ok`. */
  sentence?: string
  service?: string
  series?: AppMetricsSeries[]
}

/** `LogIntelligenceAgentOutput` (extra keys allowed) as the decision report carries it. */
export interface LogFindings {
  status?: 'complete' | 'not_enough_evidence' | 'failed' | string
  log_summary?: string
  /** The distinct metric sentences joined (≤ 500). */
  metrics_summary?: string
  /** The first queried cluster's block; absent on reports before W23d. */
  app_metrics?: AppMetricsFinding | null
  cluster_count?: number
}

// ── the readers ─────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function optText(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function optCount(value: unknown): number | undefined {
  const n = finite(value)
  return n !== null && n >= 0 ? Math.floor(n) : undefined
}

/** Drops `undefined` keys, so a reader's answer carries only what the report had. */
function compact<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T
}

const CONTRACT_VIOLATIONS_MAX = 200
const VIOLATION_KEYS = [
  'violation_type', 'severity', 'field_path', 'expected', 'actual', 'method', 'endpoint', 'baseline_build', 'sentence',
] as const

function readViolation(raw: Record<string, unknown>): ContractViolation {
  const out: Record<string, string> = {}
  for (const key of VIOLATION_KEYS) {
    const value = raw[key]
    if (typeof value === 'string') out[key] = value
    else if (typeof value === 'number' && Number.isFinite(value)) out[key] = String(value)
  }
  return out as ContractViolation
}

/** `contract_findings` → typed; anything but an object → `null` (the row shows no contract card). */
export function parseContractFindings(raw: unknown): ContractFindings | null {
  if (!isRecord(raw)) return null
  const violations = Array.isArray(raw.violations)
    ? raw.violations.filter(isRecord).slice(0, CONTRACT_VIOLATIONS_MAX).map(readViolation)
    : undefined
  const endpoints = Array.isArray(raw.endpoints_checked)
    ? raw.endpoints_checked.filter((e): e is string => typeof e === 'string')
    : undefined
  return compact<ContractFindings>({
    status: optText(raw.status),
    violations,
    violation_count: optCount(raw.violation_count),
    critical_count: optCount(raw.critical_count),
    drift_count: optCount(raw.drift_count),
    endpoints_checked: endpoints,
    summary: optText(raw.summary),
    suggests_product_bug: typeof raw.suggests_product_bug === 'boolean' ? raw.suggests_product_bug : undefined,
  })
}

function readPoint(raw: unknown): MetricPoint | null {
  if (!Array.isArray(raw) || raw.length !== 2) return null
  const at = finite(raw[0])
  if (at === null) return null
  return [at, finite(raw[1])]
}

function readSeries(raw: Record<string, unknown>): AppMetricsSeries {
  const points = Array.isArray(raw.points)
    ? raw.points.slice(0, POINTS_MAX).map(readPoint).filter((p): p is MetricPoint => p !== null)
    : []
  return compact<AppMetricsSeries>({
    template_id: optText(raw.template_id),
    label: optText(raw.label),
    unit: optText(raw.unit),
    points,
    baseline_mean: finite(raw.baseline_mean),
    window_mean: finite(raw.window_mean),
    window_max: finite(raw.window_max),
    ratio: finite(raw.ratio),
    rose: typeof raw.rose === 'boolean' ? raw.rose : undefined,
    sentence: optText(raw.sentence),
  })
}

function isOutcome(value: unknown): value is MetricsOutcome {
  return typeof value === 'string' && (OUTCOMES as readonly string[]).includes(value)
}

/**
 * One `app_metrics` block → typed, bounded to ≤ TEMPLATES_PER_CALL_MAX series × ≤ POINTS_MAX points. An outcome
 * outside OUTCOMES (a newer server, a broken block) is `null`: nothing is guessed, no chart is drawn.
 */
export function parseAppMetrics(raw: unknown): AppMetricsFinding | null {
  if (!isRecord(raw) || !isOutcome(raw.outcome)) return null
  const series = Array.isArray(raw.series)
    ? raw.series.filter(isRecord).slice(0, TEMPLATES_PER_CALL_MAX).map(readSeries)
    : []
  return compact<AppMetricsFinding>({
    outcome: raw.outcome,
    sentence: optText(raw.sentence),
    service: optText(raw.service),
    series,
  })
}

/** `log_findings` → typed; anything but an object → `null`; an unknown metrics outcome → `app_metrics: null`. */
export function parseLogFindings(raw: unknown): LogFindings | null {
  if (!isRecord(raw)) return null
  return compact<LogFindings>({
    status: optText(raw.status),
    log_summary: optText(raw.log_summary),
    metrics_summary: optText(raw.metrics_summary),
    app_metrics: raw.app_metrics === undefined ? undefined : parseAppMetrics(raw.app_metrics),
    cluster_count: optCount(raw.cluster_count),
  })
}
