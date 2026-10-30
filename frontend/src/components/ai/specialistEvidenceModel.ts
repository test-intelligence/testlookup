/**
 * W23d AGT-14 / AGT-15 — the decision report's `Specialist evidence` row, as
 * pure data: the API contract stage's violations and the log stage's
 * application-metric evidence (`contract_findings` / `log_findings`, which the
 * report carried before W23d and no screen showed — contract §0.20).
 *
 * Every string a row shows was written by the server's code (violation
 * sentences, metric sentences, template labels, a regex-checked service name)
 * and is rendered as React text. Absence is not health: a non-`ok` metrics
 * outcome shows ITS sentence ("Prometheus lookup disabled: offline mode"),
 * never an empty chart or "no anomalies".
 */
import type { SparklineTone } from '@/components/charts/Sparkline.model'
import { formatNumber } from '@/utils/formatters'
import { RATIO_ROSE } from '@/types/prometheus'
import type {
  AppMetricsSeries,
  ContractFindings,
  LogFindings,
} from '@/types/specialistEvidence'

export const SPECIALIST_TITLE = 'Specialist evidence'
export const CONTRACT_TITLE = 'API contract'
export const LOG_TITLE = 'Log intelligence'
export const METRICS_TITLE = 'Application metrics'
/** Violations listed before "+ N more". */
export const CONTRACT_ROWS_MAX = 5
/** Series drawn per report (the stage queries at most 4 templates per cluster). */
export const METRIC_SERIES_MAX = 4
/** prometheus_vocab.RATIO_ROSE (mirrored, parity-pinned): a window mean ≥ 1.5× its baseline "rose" (the warn tone). */
export { RATIO_ROSE }

export const moreLine = (n: number) => `+ ${n} more`
export const endpointsLine = (n: number) => `${n} ${n === 1 ? 'endpoint' : 'endpoints'} checked`

/** A piece of a server sentence; `code` = it sat between backticks (a field path), drawn mono. */
export interface SentencePart {
  text: string
  code: boolean
}

/**
 * ``field `price` missing …`` → text / code / text. Unbalanced backticks
 * read as plain text (nothing is guessed).
 */
export function sentenceParts(sentence: string): SentencePart[] {
  const parts = sentence.split('`')
  if (parts.length % 2 === 0) return [{ text: sentence, code: false }]
  return parts.map((text, i) => ({ text, code: i % 2 === 1 })).filter(p => p.text !== '')
}

function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export interface ContractRow {
  key: string
  sentence: string
  parts: SentencePart[]
}

export interface ContractView {
  count: number
  /** The stage's summary, without the violation sentences it appends (each is listed below it). */
  summary: SentencePart[]
  rows: ContractRow[]
  /** Violations beyond the first five ("+ N more"); 0 = none. */
  more: number
  endpointsChecked: number
}

/** The sentence of one violation: the server's own, else one built from its field (an older report). */
function violationSentence(v: Record<string, unknown>): string {
  const sentence = str(v.sentence)
  if (sentence) return sentence
  const field = str(v.field_path)
  if (!field) return ''
  const expected = str(v.expected)
  const actual = str(v.actual)
  return expected || actual
    ? `field \`${field}\`: expected ${expected || '—'}, found ${actual || '—'}`
    : `field \`${field}\``
}

export function contractRows(findings: ContractFindings | null | undefined): ContractView | null {
  if (!findings) return null
  const raw = Array.isArray(findings.violations) ? (findings.violations as unknown[]) : []
  const violations = raw.filter((v): v is Record<string, unknown> => v !== null && typeof v === 'object')
  const count = num(findings.violation_count) ?? violations.length
  // `not_enough_evidence` (no capture, no baseline): the summary only.
  const listed = findings.status === 'not_enough_evidence' ? [] : violations
  const rows: ContractRow[] = []
  for (const [index, v] of listed.entries()) {
    if (rows.length === CONTRACT_ROWS_MAX) break
    const sentence = violationSentence(v)
    if (sentence) rows.push({ key: `${index}-${str(v.field_path)}`, sentence, parts: sentenceParts(sentence) })
  }
  // The contract stage appends its first violation sentences to the summary (W23d P-5); the list shows them, so
  // the card does not say each twice.
  let summary = str(findings.summary)
  for (const row of rows) summary = summary.replace(row.sentence, ' ')
  summary = summary.replace(/\s{2,}/g, ' ').trim()
  const endpoints = Array.isArray(findings.endpoints_checked) ? findings.endpoints_checked.length : 0
  return {
    count,
    summary: summary ? sentenceParts(summary) : [],
    rows,
    // The count can exceed the listed violations (the stage keeps ≤ 200).
    more: listed.length > 0 ? Math.max(0, Math.max(count, listed.length) - rows.length) : 0,
    endpointsChecked: endpoints,
  }
}

const MB = 1024 * 1024

/**
 * A metric value in its template's unit: seconds → "620 ms" / "1.25 s",
 * bytes → "512.0 MB", ratio → "35.0%", per_second → "0.42/s", count → "1,284".
 * A missing / non-finite value is "—".
 */
export function formatMetric(value: number | null | undefined, unit: string): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  switch (unit) {
    case 'seconds':
      return Math.abs(value) < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`
    case 'bytes':
      return `${(value / MB).toFixed(1)} MB`
    case 'ratio':
      return `${(value * 100).toFixed(1)}%`
    case 'per_second':
      return `${formatNumber(value, { maximumFractionDigits: 2 })}/s`
    default:
      return formatNumber(value, { maximumFractionDigits: 2 })
  }
}

/** The spark's caption: "620 ms vs 200 ms before"; only a window mean → "… in the failure window". */
export function metricCaption(windowMean: number | null, baselineMean: number | null, unit: string): string | null {
  if (windowMean === null) return null
  if (baselineMean === null) return `${formatMetric(windowMean, unit)} in the failure window`
  return `${formatMetric(windowMean, unit)} vs ${formatMetric(baselineMean, unit)} before`
}

export interface MetricRow {
  key: string
  label: string
  service: string
  unit: string
  /** The values, oldest first; `null` = not measured (the line breaks). */
  values: (number | null)[]
  baselineMean: number | null
  windowMean: number | null
  tone: SparklineTone
  sentence: string
}

export interface LogView {
  /** `log_summary`, clamped to two lines by the card (the full text is its `title`). */
  summary: string
  /** `ok` with series → one row each; anything else → its outcome sentence; no `app_metrics` → neither. */
  metricRows: MetricRow[]
  metricSentence: string | null
}

function seriesRow(series: AppMetricsSeries, service: string, index: number): MetricRow {
  const points = Array.isArray(series.points) ? series.points : []
  const values = points.map(p => (Array.isArray(p) ? num(p[1]) : null))
  const ratio = num(series.ratio)
  return {
    key: `${str(series.template_id) || 'series'}-${index}`,
    label: str(series.label) || str(series.template_id),
    service,
    unit: str(series.unit) || 'count',
    values,
    baselineMean: num(series.baseline_mean),
    windowMean: num(series.window_mean),
    tone: ratio !== null && ratio >= RATIO_ROSE ? 'warn' : 'neutral',
    sentence: str(series.sentence),
  }
}

export function logMetricRows(findings: LogFindings | null | undefined): LogView | null {
  if (!findings) return null
  const metrics = findings.app_metrics
  const view: LogView = { summary: str(findings.log_summary), metricRows: [], metricSentence: null }
  if (!metrics) return view
  const service = str(metrics.service)
  const series = Array.isArray(metrics.series) ? metrics.series.slice(0, METRIC_SERIES_MAX) : []
  if (metrics.outcome === 'ok' && series.length > 0) {
    view.metricRows = series.map((s, i) => seriesRow(s, service, i))
  } else {
    view.metricSentence = str(metrics.sentence) || null
  }
  return view
}
