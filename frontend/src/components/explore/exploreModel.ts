/**
 * VIZ-505 — the Explorer's rules: one metric over time, split into lines by a
 * series dimension and into small multiples by a facet. Pure: no React, no
 * fetching.
 *
 * Two layers of rules, kept apart on purpose:
 *
 *   - `apiRefusal` is an EXACT mirror of the server's `parse_chart_spec`
 *     (`chart_data_service.py`): the same checks in the same order, the first
 *     failure wins. It is held to the server by a shared fixture
 *     (`contracts/viz/chart_data_combinations.json`, generated and checked by
 *     `backend/tests/test_chart_data_combinations.py`), so a rule that changes
 *     on one side fails a test on the other. The metric never decides
 *     acceptance: only `group_by`, `top_n` and the suite scope do.
 *   - `explorePlan` adds the Explorer's own rules on top (a time x, a facet
 *     the API can also FILTER by, a facet that is not the series, a release
 *     facet only inside one project) and turns a configuration into the two
 *     requests it needs: one discovery request that names the panels, and one
 *     request per panel. Every request it plans passes `apiRefusal`.
 *
 * A facet must be a dimension whose group-by key equals its filter value, so
 * a panel can be asked for with the key the discovery request returned. Only
 * two qualify: `suite` (the key is the lower-cased effective suite, and
 * `suite_name` filters on exactly that) and `release` (the key is the primary
 * release id or `unattributed`, and `release_id` filters on exactly that).
 * The `(none)` suite cannot be filtered back, so it is never a panel.
 */
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { MultiSeriesMetricKind, MultiSeriesModel } from '@/components/charts/multiSeriesModel'
import { zeroBasedScale } from '@/components/charts/niceScale'
import type { SeriesStyle } from '@/components/reports/catalogue/compareModel'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'

// ── The server's vocabularies (held to it by the shared fixture) ─────────────

/** `chart_data_service.METRICS`, in the server's order. */
export const API_METRICS = [
  'executions',
  'passed',
  'failed',
  'broken',
  'skipped',
  'unknown',
  'failures',
  'retried_tests',
  'pass_rate',
  'failure_rate',
  'flaky_tests',
  'unique_tests',
  'run_count',
  'duration_p50',
  'duration_p95',
  'duration_total',
] as const
export type ApiMetric = (typeof API_METRICS)[number]

/**
 * `chart_data_service.DIMENSIONS`: which are time axes and which are too large
 * to pair. Not `WIDGET_GROUP_BY_DIMENSIONS` (`lib/viz/contracts.ts`): that list
 * names `error_signature`, which the server does not group by.
 */
export const API_DIMENSIONS = {
  day: { time: true, high: false },
  week: { time: true, high: false },
  project: { time: false, high: false },
  release: { time: false, high: false },
  suite: { time: false, high: true },
  status: { time: false, high: false },
  failure_category: { time: false, high: false },
  branch: { time: false, high: false },
  environment: { time: false, high: false },
  ingestion_source: { time: false, high: false },
  test: { time: false, high: true },
} as const satisfies Record<string, { time: boolean; high: boolean }>
export type ApiDimension = keyof typeof API_DIMENSIONS
export const API_DIMENSION_KEYS = Object.keys(API_DIMENSIONS) as ApiDimension[]

/** `MAX_GROUP_BY`, `MAX_TOP_N_SERIES` (= `MAX_SERIES` - 1) and `MAX_TOP_N_POINTS` (= 366 - 1). */
export const MAX_GROUP_BY = 2
export const MAX_TOP_N_SERIES = 7
export const MAX_TOP_N_POINTS = 365

/** The bucket a row with no value falls into (`NO_VALUE`): it cannot be filtered back. */
export const NO_VALUE_KEY = '(none)'
/** The merged remainder's key (`OTHER_KEY`). */
const OTHER_KEY = '__other__'

export type RefusalCode =
  | 'missing_parameter'
  | 'group_by_cap'
  | 'dimension_enum'
  | 'unique_dimension'
  | 'high_cardinality_pair'
  | 'time_dimension_position'
  | 'top_n_range'
  | 'top_n_unsupported'
  | 'test_requires_scope'

const isDimension = (value: string): value is ApiDimension => Object.prototype.hasOwnProperty.call(API_DIMENSIONS, value)

/**
 * The refusal code `parse_chart_spec` would answer for this request, or
 * `null` when it accepts it. `suiteCount` is the number of DISTINCT suite
 * names in the scope (`suite_keys`); `topN` is `null` when absent.
 */
export function apiRefusal(groupBy: readonly string[], suiteCount: number, topN: number | null): RefusalCode | null {
  if (groupBy.length === 0) return 'missing_parameter'
  if (groupBy.length > MAX_GROUP_BY) return 'group_by_cap'
  const dims: ApiDimension[] = []
  for (const value of groupBy) {
    if (!isDimension(value)) return 'dimension_enum'
    if (dims.includes(value)) return 'unique_dimension'
    dims.push(value)
  }
  if (dims.length === 2 && dims.every((d) => API_DIMENSIONS[d].high)) return 'high_cardinality_pair'
  if (dims.length === 2 && API_DIMENSIONS[dims[0]].time && API_DIMENSIONS[dims[1]].time) return 'unique_dimension'
  if (dims.length === 2 && API_DIMENSIONS[dims[1]].time) return 'time_dimension_position'
  if (topN !== null) {
    // `_parse_top_n`: an integer; it names series with two dimensions, buckets
    // with one category dimension, and nothing on a lone time axis.
    if (!Number.isInteger(topN)) return 'top_n_range'
    if (dims.length === 2) {
      if (topN < 1 || topN > MAX_TOP_N_SERIES) return 'top_n_range'
    } else if (API_DIMENSIONS[dims[0]].time) {
      return 'top_n_unsupported'
    } else if (topN < 1 || topN > MAX_TOP_N_POINTS) {
      return 'top_n_range'
    }
  }
  if (dims.includes('test') && topN === null && suiteCount !== 1) return 'test_requires_scope'
  return null
}

// ── What the reader picks ───────────────────────────────────────────────────

export interface MetricInfo {
  label: string
  kind: MultiSeriesMetricKind
  /** The y-axis title. */
  title: string
}

export const EXPLORE_METRICS: Record<ApiMetric, MetricInfo> = {
  executions: { label: 'Executions', kind: 'count', title: 'Executions' },
  passed: { label: 'Passed', kind: 'count', title: 'Passed' },
  failed: { label: 'Failed', kind: 'count', title: 'Failed' },
  broken: { label: 'Broken', kind: 'count', title: 'Broken' },
  skipped: { label: 'Skipped', kind: 'count', title: 'Skipped' },
  unknown: { label: 'Unknown status', kind: 'count', title: 'Unknown status' },
  failures: { label: 'Failures (failed + broken)', kind: 'count', title: 'Failures' },
  retried_tests: { label: 'Retried tests', kind: 'count', title: 'Retried tests' },
  pass_rate: { label: 'Pass rate', kind: 'rate', title: 'Pass rate %' },
  failure_rate: { label: 'Failure rate', kind: 'rate', title: 'Failure rate %' },
  flaky_tests: { label: 'Flaky tests', kind: 'count', title: 'Flaky tests' },
  unique_tests: { label: 'Unique tests', kind: 'count', title: 'Unique tests' },
  run_count: { label: 'Runs', kind: 'count', title: 'Runs' },
  duration_p50: { label: 'Median duration (p50, ms)', kind: 'count', title: 'p50 duration (ms)' },
  duration_p95: { label: 'Slow-end duration (p95, ms)', kind: 'count', title: 'p95 duration (ms)' },
  duration_total: { label: 'Total duration (ms)', kind: 'count', title: 'Total duration (ms)' },
}

/** Each dimension's name in a picker, and what several of it are called. */
export const DIMENSION_LABELS: Record<ApiDimension, { label: string; plural: string }> = {
  day: { label: 'Day', plural: 'days' },
  week: { label: 'Week', plural: 'weeks' },
  project: { label: 'Project', plural: 'projects' },
  release: { label: 'Release', plural: 'releases' },
  suite: { label: 'Suite', plural: 'suites' },
  status: { label: 'Status', plural: 'statuses' },
  failure_category: { label: 'Failure category', plural: 'failure categories' },
  branch: { label: 'Branch', plural: 'branches' },
  environment: { label: 'Environment', plural: 'environments' },
  ingestion_source: { label: 'Ingestion source', plural: 'ingestion sources' },
  test: { label: 'Test', plural: 'tests' },
}

export type ExploreX = 'day' | 'week'
export type ExploreFacet = 'suite' | 'release'
export type ExploreYScale = 'shared' | 'independent'

/** The windows the Explorer offers, days. */
export const EXPLORE_DAYS = [7, 14, 30, 90] as const
export type ExploreDays = (typeof EXPLORE_DAYS)[number]

/** The most panels one grid draws. */
export const MAX_PANELS = 12

export interface ExploreConfig {
  metric: ApiMetric
  x: ExploreX
  series: ApiDimension | null
  facet: ExploreFacet | null
  yScale: ExploreYScale
  days: ExploreDays
}

/** The four fields that decide the requests, in the order each one narrows the next. */
export const PLAN_FIELDS = ['metric', 'x', 'series', 'facet'] as const
export type PlanField = (typeof PLAN_FIELDS)[number]

/** The planned fields as anything a URL or a saved view might hold: checked, never trusted. */
export type ExploreAxes = Record<PlanField, string | null>

export interface ExploreContext {
  /** One project is active (not All Projects): a release facet needs one. */
  pinnedProject: boolean
}

export const TIME_AXIS_REASON = 'the explorer draws a time axis'
export const FACET_FILTER_REASON = 'a facet must be a dimension the API can also filter by'
export const FACET_IS_SERIES_REASON = 'each panel would show one line'
export const RELEASE_NEEDS_PROJECT_REASON = 'releases belong to one project: pick a project to facet by release'

export interface PanelRequest {
  groupBy: ApiDimension[]
  /** 1 when each panel is filtered to one suite (`suite_name`), else 0. */
  suiteCount: number
  topN: number | null
}

export interface DiscoveryRequest {
  groupBy: ApiDimension[]
  topN: number | null
  /** The response's series keys colour every panel alike. */
  sharedLegend: boolean
}

export type ExplorePlan =
  | { ok: true; panel: PanelRequest; discovery: DiscoveryRequest | null }
  | { ok: false; field: PlanField; reason: string }

const refuse = (field: PlanField, reason: string): ExplorePlan => ({ ok: false, field, reason })

/** The requests a configuration needs, or the first rule it breaks (E0..E5, then the API's own). */
export function explorePlan(config: ExploreAxes, ctx: ExploreContext): ExplorePlan {
  const { metric, x, series, facet } = config
  if (metric === null || !(API_METRICS as readonly string[]).includes(metric)) return refuse('metric', 'not a metric the API knows')
  if (x !== 'day' && x !== 'week') return refuse('x', TIME_AXIS_REASON)
  if (series !== null && (!isDimension(series) || API_DIMENSIONS[series].time)) {
    return refuse('series', 'the series must be a dimension other than time')
  }
  if (facet !== null && facet !== 'suite' && facet !== 'release') return refuse('facet', FACET_FILTER_REASON)
  if (facet !== null && facet === series) return refuse('facet', FACET_IS_SERIES_REASON)
  if (facet === 'release' && !ctx.pinnedProject) return refuse('facet', RELEASE_NEEDS_PROJECT_REASON)

  const suiteCount = facet === 'suite' ? 1 : 0
  const panel: PanelRequest = {
    groupBy: series ? [x, series as ApiDimension] : [x],
    suiteCount,
    topN: series === 'test' && suiteCount !== 1 ? MAX_TOP_N_SERIES : null,
  }
  const panelRefusal = apiRefusal(panel.groupBy, panel.suiteCount, panel.topN)
  if (panelRefusal) return refuse(series ? 'series' : 'x', `the API refuses this request (${panelRefusal})`)

  let discovery: DiscoveryRequest | null = null
  if (facet !== null) {
    const pairable = series !== null && !(API_DIMENSIONS[facet].high && API_DIMENSIONS[series as ApiDimension].high)
    discovery = pairable
      ? { groupBy: [facet, series as ApiDimension], topN: series === 'test' ? MAX_TOP_N_SERIES : null, sharedLegend: true }
      : { groupBy: [facet], topN: null, sharedLegend: false }
    const discoveryRefusal = apiRefusal(discovery.groupBy, 0, discovery.topN)
    if (discoveryRefusal) return refuse('facet', `the API refuses this request (${discoveryRefusal})`)
  }
  return { ok: true, panel, discovery }
}

/** Every value a picker could hold, before the rules: `null` is "None". */
export const FIELD_CANDIDATES: Record<PlanField, readonly (string | null)[]> = {
  metric: API_METRICS,
  x: API_DIMENSION_KEYS,
  series: [null, ...API_DIMENSION_KEYS],
  facet: [null, ...API_DIMENSION_KEYS],
}

/**
 * The most permissive value of each field: whatever a field after the one
 * being judged holds, it is judged as if it held this. `day` and `week` are
 * interchangeable to every rule; "no series" and "no facet" refuse nothing.
 */
const NEUTRAL: ExploreAxes = { metric: 'executions', x: 'day', series: null, facet: null }

/** `config` with `field` set to `value` and every field after it neutral. */
function upTo(config: ExploreAxes, field: PlanField, value: string | null): ExploreAxes {
  const index = PLAN_FIELDS.indexOf(field)
  const next = { ...config, [field]: value }
  for (const later of PLAN_FIELDS.slice(index + 1)) next[later] = NEUTRAL[later]
  return next
}

/**
 * The values a picker offers: those whose plan is ok with the fields BEFORE
 * this one as they are (metric → x → series → facet), whatever the later ones
 * hold. A later field the change invalidates is reset by `coerceConfig`.
 */
export function allowedOptions(field: PlanField, config: ExploreAxes, ctx: ExploreContext): (string | null)[] {
  return FIELD_CANDIDATES[field].filter((value) => explorePlan(upTo(config, field, value), ctx).ok)
}

/** Why a value is not offered (for a disabled option), or `null` when it is. */
export function refusalReason(field: PlanField, value: string | null, config: ExploreAxes, ctx: ExploreContext): string | null {
  const plan = explorePlan(upTo(config, field, value), ctx)
  return plan.ok ? null : plan.reason
}

const fieldName: Record<PlanField, string> = { metric: 'Metric', x: 'X axis', series: 'Lines', facet: 'Panels' }

/** A value from a URL or a saved view, short enough to quote in a notice. */
export const quoted = (value: string) => (value.length > 40 ? `${value.slice(0, 40)}…` : value)

/** The value's name in a notice. */
function valueName(field: PlanField, value: string | null): string {
  if (value === null) return 'None'
  if (field === 'metric' && (API_METRICS as readonly string[]).includes(value)) return EXPLORE_METRICS[value as ApiMetric].label
  return isDimension(value) ? DIMENSION_LABELS[value].label : quoted(value)
}

/**
 * The configuration with every invalid field reset, walking metric → x →
 * series → facet: a metric or x that is not allowed falls back to `fallback`'s
 * (they always have a valid value), a series or facet to `null`, each with a
 * notice that says what changed and why.
 */
export function coerceConfig(
  config: ExploreAxes,
  ctx: ExploreContext,
  fallback: Pick<ExploreConfig, 'metric' | 'x'>,
): { axes: Pick<ExploreConfig, PlanField>; notices: string[] } {
  const notices: string[] = []
  let current = { ...config }
  for (const field of PLAN_FIELDS) {
    const allowed = allowedOptions(field, current, ctx)
    const value = current[field]
    if (allowed.includes(value)) continue
    const replacement = field === 'metric' || field === 'x' ? fallback[field] : null
    const reason = refusalReason(field, value, current, ctx)
    notices.push(
      `${fieldName[field]} "${valueName(field, value)}" is not available${reason ? ` (${reason})` : ''}; showing ${valueName(field, replacement)} instead.`,
    )
    current = { ...current, [field]: replacement }
  }
  return { axes: current as Pick<ExploreConfig, PlanField>, notices }
}

// ── Requests ────────────────────────────────────────────────────────────────

const withTopN = (params: CatalogParams, topN: number | null): CatalogParams => (topN === null ? params : { ...params, top_n: topN })

/** The discovery request: always `executions`, so the panel set does not move with the metric. */
export function discoveryParams(base: CatalogParams, discovery: DiscoveryRequest): CatalogParams {
  return withTopN({ ...base, metric: 'executions', group_by: [...discovery.groupBy] }, discovery.topN)
}

/** One panel's request: the page scope, the metric over time, filtered to the facet key. */
export function panelParams(
  base: CatalogParams,
  metric: ApiMetric,
  panel: PanelRequest,
  facet: ExploreFacet | null,
  facetKey: string | null,
): CatalogParams {
  const params = withTopN({ ...base, metric, group_by: [...panel.groupBy] }, panel.topN)
  if (facet === 'suite' && facetKey !== null) params.suite_name = facetKey
  if (facet === 'release' && facetKey !== null) params.release_id = facetKey
  return params
}

// ── Panels and their legend ─────────────────────────────────────────────────

export interface FacetPanel {
  key: string
  label: string
}

export interface FacetPanels {
  panels: FacetPanel[]
  /** How many facet values there are in all (`(none)` not counted). */
  total: number
  notices: string[]
}

/** The release key for runs with no primary release (`UNATTRIBUTED_RELEASE`). */
const UNATTRIBUTED = 'unattributed'

/**
 * The panels the discovery response names: the x keys in response order
 * (the server ranks a category axis by executions, largest first), less
 * `(none)`, at most `MAX_PANELS`. `unattributed` is a real release key and
 * stays. `meta.truncated_axes.x.total` is the true count when the axis was
 * capped.
 */
export function facetPanels(chart: SeriesChart | null | undefined, meta: EnvelopeMeta | null | undefined, facet: ExploreFacet): FacetPanels {
  const keys = (chart?.series[0]?.points ?? []).map((point) => point.x).filter((key) => key !== OTHER_KEY)
  const hasNone = keys.includes(NO_VALUE_KEY)
  const real = keys.filter((key) => key !== NO_VALUE_KEY)
  const total = Math.max(real.length, (meta?.truncated_axes?.x?.total ?? keys.length) - (hasNone ? 1 : 0))
  const labels = chart?.x_labels ?? {}
  const panels = real.slice(0, MAX_PANELS).map((key) => ({
    key,
    label: labels[key] ?? (facet === 'release' && key === UNATTRIBUTED ? 'Unattributed runs' : key),
  }))
  const notices: string[] = []
  const noun = DIMENSION_LABELS[facet].plural
  if (total > panels.length) notices.push(`Showing the ${panels.length} ${noun} with the most executions of ${total.toLocaleString('en-US')}.`)
  if (hasNone && facet === 'suite') notices.push('Runs with no suite are not shown.')
  return { panels, total, notices }
}

/**
 * The shared legend: the discovery response's i-th series key (not "Other")
 * gets colour and dash slot i, the first `MAX_TOP_N_SERIES` of them. Slot 7
 * stays "Other"'s.
 */
export function seriesStyles(chart: SeriesChart | null | undefined): Record<string, SeriesStyle> {
  const styles: Record<string, SeriesStyle> = {}
  const keys = (chart?.series ?? []).map((s) => s.key).filter((key) => key !== OTHER_KEY)
  keys.slice(0, MAX_TOP_N_SERIES).forEach((key, i) => {
    styles[key] = { colour: i, dash: i }
  })
  return styles
}

/**
 * One panel's styles: a key the shared legend knows keeps its slot; the
 * panel's other keys take the slots this panel leaves free, in sorted order,
 * so no two lines in one panel ever share a slot. Slot 7 is free only when
 * the panel has no "Other".
 */
export function panelStyles(global: Readonly<Record<string, SeriesStyle>>, keys: readonly string[]): Record<string, SeriesStyle> {
  const styles: Record<string, SeriesStyle> = {}
  const used = new Set<number>()
  const real = keys.filter((key) => key !== OTHER_KEY)
  for (const key of real) {
    const style = global[key]
    if (style && !used.has(style.colour)) {
      styles[key] = style
      used.add(style.colour)
    }
  }
  const lastSlot = keys.includes(OTHER_KEY) ? 6 : 7
  const free = Array.from({ length: lastSlot + 1 }, (_, i) => i).filter((slot) => !used.has(slot))
  const extra = real.filter((key) => !(key in styles)).sort()
  extra.forEach((key, i) => {
    if (i < free.length) styles[key] = { colour: free[i], dash: free[i] }
  })
  return styles
}

// ── The y scale ─────────────────────────────────────────────────────────────

export type YAxis = MultiSeriesModel['yAxis']

/** The largest measured value a model draws, or `null` when it draws none. */
export function ownMaxOf(model: Pick<MultiSeriesModel, 'lines'> | null): number | null {
  let max: number | null = null
  for (const line of model?.lines ?? []) for (const p of line.points) if (p.y !== null && (max === null || p.y > max)) max = p.y
  return max
}

/**
 * A panel's y axis. A rate shares 0–100 by construction; its own scale zooms
 * to the panel's values (never past 100). A count's shared scale is the
 * largest value of any panel loaded so far; its own is the model's.
 */
export function panelYAxis(
  kind: MultiSeriesMetricKind,
  mode: ExploreYScale,
  ownMax: number | null,
  sharedMax: number | null,
  modelAxis: YAxis,
): YAxis {
  if (kind === 'rate') {
    if (mode === 'shared') return modelAxis
    const { domain, ticks } = zeroBasedScale(Math.min(ownMax ?? 0, 100), { intervals: 4 })
    const top = Math.min(domain[1], 100)
    return { domain: [0, top], ticks: ticks.filter((tick) => tick <= top) }
  }
  if (mode === 'independent') return modelAxis
  const { domain, ticks } = zeroBasedScale(Math.max(sharedMax ?? 0, ownMax ?? 0), { intervals: 4, integer: true })
  return { domain, ticks }
}

const plain = (n: number) => n.toLocaleString('en-US')

/**
 * What a panel says about its scale: "Shared y-scale: 0–100%", "Own y-scale:
 * 0–35%", and for a shared count scale, how many panels it was taken from
 * while some are still to load ("from 5 of 12 panels; widens if a panel below
 * is larger").
 */
export function yScaleText(kind: MultiSeriesMetricKind, mode: ExploreYScale, axis: YAxis, measured: number, total: number): string {
  const range = `${plain(axis.domain[0])}–${plain(axis.domain[1])}${kind === 'rate' ? '%' : ''}`
  if (mode === 'independent') return `Own y-scale: ${range}`
  const partial = kind !== 'rate' && total > 1 && measured < total ? ` (from ${measured} of ${total} panels; widens if a panel below is larger)` : ''
  return `Shared y-scale: ${range}${partial}`
}
