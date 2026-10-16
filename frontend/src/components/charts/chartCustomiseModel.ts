/**
 * VIZ-604 — the chart customisation model for a series-over-time chart that
 * reads `chart-data` (Trends' "Pass rate by suite"): the metric, the series
 * dimension, the time bucket, how many series, and the title. Pure: no React.
 *
 * Guard rails: an option that would produce a misleading chart is listed as
 * UNAVAILABLE with its reason, never silently missing (pie for series over
 * time, stacked rates, a log scale for rates or zeros, more lines than stay
 * readable).
 *
 * A stored config is versioned and validated on read: one that no longer
 * fits (a value this build does not offer) falls back to the default and the
 * reader is told once.
 *
 * TRND-10: the Customise panel is generic. A chart describes its controls as
 * a `CustomiseSpec` (fields, guard rails, whether it is at its default) and
 * applies a field change itself; `seriesCustomiseSpec` / `applySeriesField`
 * are this chart's, `compareChartConfig.ts` has Compare's.
 */
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { MultiSeriesMetricKind } from '@/components/charts/multiSeriesModel'
import type { ChartConfigModel } from './useChartConfig'

export const CHART_CONFIG_VERSION = 1

export type SeriesMetric = 'pass_rate' | 'failures' | 'failed' | 'executions'
export type SeriesDimension = 'suite' | 'environment' | 'branch' | 'release'
export type TimeBucket = 'day' | 'week'
export type SeriesTopN = 3 | 5 | 7

export interface SeriesChartConfig {
  version: typeof CHART_CONFIG_VERSION
  metric: SeriesMetric
  seriesBy: SeriesDimension
  bucket: TimeBucket
  topN: SeriesTopN
  /** The reader's title; absent = the title the settings imply. */
  title?: string
}

export const SERIES_METRICS: Record<SeriesMetric, { label: string; kind: MultiSeriesMetricKind; title: string }> = {
  pass_rate: { label: 'Pass rate', kind: 'rate', title: 'Pass rate %' },
  failures: { label: 'Failures', kind: 'count', title: 'Failures' },
  failed: { label: 'Failed tests', kind: 'count', title: 'Failed tests' },
  executions: { label: 'Executions', kind: 'count', title: 'Executions' },
}

export const SERIES_DIMENSIONS: Record<SeriesDimension, { label: string; plural: string }> = {
  suite: { label: 'Suite', plural: 'suites' },
  environment: { label: 'Environment', plural: 'environments' },
  branch: { label: 'Branch', plural: 'branches' },
  release: { label: 'Release', plural: 'releases' },
}

export const TIME_BUCKETS: Record<TimeBucket, string> = { day: 'Day', week: 'Week' }

export const TOP_N_OPTIONS: readonly SeriesTopN[] = [3, 5, 7]

export const DEFAULT_SERIES_CONFIG: SeriesChartConfig = {
  version: CHART_CONFIG_VERSION,
  metric: 'pass_rate',
  seriesBy: 'suite',
  bucket: 'day',
  topN: 7,
}

export const TITLE_MAX = 80

/** The title the settings imply: "Pass rate by suite", "Failures by environment, weekly". */
export function impliedTitle(config: Pick<SeriesChartConfig, 'metric' | 'seriesBy' | 'bucket'>): string {
  const base = `${SERIES_METRICS[config.metric].label} by ${SERIES_DIMENSIONS[config.seriesBy].label.toLowerCase()}`
  return config.bucket === 'week' ? `${base}, weekly` : base
}

export function titleOf(config: SeriesChartConfig): string {
  const own = config.title?.trim()
  return own ? own : impliedTitle(config)
}

/** The `chart-data` parameters (before the page scope is added). */
export function seriesParams(config: SeriesChartConfig): CatalogParams {
  return { metric: config.metric, group_by: [config.bucket, config.seriesBy], top_n: config.topN }
}

export function isDefault(config: SeriesChartConfig): boolean {
  return (
    config.metric === DEFAULT_SERIES_CONFIG.metric &&
    config.seriesBy === DEFAULT_SERIES_CONFIG.seriesBy &&
    config.bucket === DEFAULT_SERIES_CONFIG.bucket &&
    config.topN === DEFAULT_SERIES_CONFIG.topN &&
    !config.title?.trim()
  )
}

/** An option the panel lists but does not offer, and why. */
export interface UnavailableOption {
  /** The field it belongs to (`chartType`, `scale`, `topN`, or a chart's own, e.g. Compare's `lines`). */
  control: 'chartType' | 'scale' | 'topN' | (string & {})
  label: string
  reason: string
}

// ── The generic panel spec (TRND-10) ─────────────────────────────────────────

/** One option of a select field. A `disabled` option is listed "(unavailable)", with its `reason`. */
export interface CustomiseOption {
  value: string
  label: string
  disabled?: boolean
  reason?: string
}

/**
 * One control of the Customise panel. `id` is its `data-customise` hook
 * (`title`, `metric`, `series`, `bucket`, `topN`, `chartType`, `scale`, and a
 * chart's own), so tests and specs read the same DOM whichever chart hosts it.
 */
export interface CustomiseField {
  id: string
  label: string
  kind: 'text' | 'select'
  value: string
  options?: CustomiseOption[]
  placeholder?: string
  maxLength?: number
}

/** What the panel draws for one chart: its fields, the guard rails, and whether Reset has anything to do. */
export interface CustomiseSpec {
  fields: CustomiseField[]
  unavailable: UnavailableOption[]
  isDefault: boolean
}

/** A fixed control (chart type, scale): the one offered value, then each unavailable one, disabled. */
export function fixedField(id: string, label: string, offered: CustomiseOption, unavailable: readonly UnavailableOption[]): CustomiseField {
  return {
    id,
    label,
    kind: 'select',
    value: offered.value,
    options: [
      offered,
      ...unavailable
        .filter((option) => option.control === id)
        .map((option) => ({ value: `unavailable:${option.label}`, label: option.label, disabled: true, reason: option.reason })),
    ],
  }
}

/** The five VIZ-604 controls (title, metric, series, bucket, lines) plus chart type and scale, as a spec. */
export function seriesCustomiseSpec(config: SeriesChartConfig): CustomiseSpec {
  const unavailable = unavailableOptions(config)
  const lines = unavailable.filter((option) => option.control === 'topN')
  return {
    fields: [
      { id: 'title', label: 'Title', kind: 'text', value: config.title ?? '', placeholder: impliedTitle(config), maxLength: TITLE_MAX },
      {
        id: 'metric',
        label: 'Metric',
        kind: 'select',
        value: config.metric,
        options: (Object.keys(SERIES_METRICS) as SeriesMetric[]).map((key) => ({ value: key, label: SERIES_METRICS[key].label })),
      },
      {
        id: 'series',
        label: 'One line per',
        kind: 'select',
        value: config.seriesBy,
        options: (Object.keys(SERIES_DIMENSIONS) as SeriesDimension[]).map((key) => ({ value: key, label: SERIES_DIMENSIONS[key].label })),
      },
      {
        id: 'bucket',
        label: 'Time bucket',
        kind: 'select',
        value: config.bucket,
        options: (Object.keys(TIME_BUCKETS) as TimeBucket[]).map((key) => ({ value: key, label: TIME_BUCKETS[key] })),
      },
      {
        id: 'topN',
        label: 'Lines (busiest first, the rest as “Other”)',
        kind: 'select',
        value: String(config.topN),
        options: [
          ...TOP_N_OPTIONS.map((n) => ({ value: String(n), label: String(n) })),
          ...lines.map((option) => ({ value: `unavailable:${option.label}`, label: option.label, disabled: true, reason: option.reason })),
        ],
      },
      fixedField('chartType', 'Chart type', { value: 'line', label: 'Line' }, unavailable),
      fixedField('scale', 'Scale', { value: 'linear', label: 'Linear' }, unavailable),
    ],
    unavailable,
    isDefault: isDefault(config),
  }
}

/** `config` with the panel's field `id` set to `value`; a value this chart does not offer changes nothing. */
export function applySeriesField(config: SeriesChartConfig, id: string, value: string): SeriesChartConfig {
  switch (id) {
    case 'title':
      return { ...config, title: value || undefined }
    case 'metric':
      return oneOf(value, Object.keys(SERIES_METRICS) as SeriesMetric[]) ? { ...config, metric: value } : config
    case 'series':
      return oneOf(value, Object.keys(SERIES_DIMENSIONS) as SeriesDimension[]) ? { ...config, seriesBy: value } : config
    case 'bucket':
      return oneOf(value, Object.keys(TIME_BUCKETS) as TimeBucket[]) ? { ...config, bucket: value } : config
    case 'topN': {
      const n = Number(value)
      return oneOf(n, TOP_N_OPTIONS) ? { ...config, topN: n } : config
    }
    default:
      return config
  }
}

/** The guard rails for this chart and these settings. */
export function unavailableOptions(config: SeriesChartConfig): UnavailableOption[] {
  const rate = SERIES_METRICS[config.metric].kind === 'rate'
  const options: UnavailableOption[] = [
    { control: 'chartType', label: 'Pie', reason: 'A pie shows the parts of one whole, not series over time.' },
    rate
      ? { control: 'chartType', label: 'Stacked area', reason: 'Rates cannot be stacked: 40% on top of 60% is not 100% of anything.' }
      : { control: 'chartType', label: 'Stacked area', reason: 'Stacking hides each series’ own trend; compare the lines instead.' },
    rate
      ? { control: 'scale', label: 'Log', reason: 'A log scale exaggerates small differences between rates, and cannot show 0%.' }
      : { control: 'scale', label: 'Log', reason: 'A log scale cannot show a day with 0, which counts often have.' },
    { control: 'topN', label: 'More than 7', reason: 'More than 7 lines plus “Other” stop being readable on one chart.' },
  ]
  return options
}

const oneOf = <T extends string | number>(value: unknown, options: readonly T[]): value is T =>
  options.includes(value as T)

/**
 * A stored config, validated. Anything this build does not offer falls back
 * to the default with `notice` set (say it once); nothing stored is the
 * default with no notice.
 */
export function readSeriesConfig(raw: unknown): { config: SeriesChartConfig; notice: string | null } {
  if (raw === null || raw === undefined) return { config: DEFAULT_SERIES_CONFIG, notice: null }
  const value = raw as Partial<SeriesChartConfig>
  const valid =
    typeof raw === 'object' &&
    value.version === CHART_CONFIG_VERSION &&
    oneOf(value.metric, Object.keys(SERIES_METRICS) as SeriesMetric[]) &&
    oneOf(value.seriesBy, Object.keys(SERIES_DIMENSIONS) as SeriesDimension[]) &&
    oneOf(value.bucket, Object.keys(TIME_BUCKETS) as TimeBucket[]) &&
    oneOf(value.topN, TOP_N_OPTIONS) &&
    (value.title === undefined || (typeof value.title === 'string' && value.title.length <= TITLE_MAX))
  if (!valid) {
    return {
      config: DEFAULT_SERIES_CONFIG,
      notice: 'Your saved customisation of this chart no longer applies, so it shows the default.',
    }
  }
  return {
    config: {
      version: CHART_CONFIG_VERSION,
      metric: value.metric as SeriesMetric,
      seriesBy: value.seriesBy as SeriesDimension,
      bucket: value.bucket as TimeBucket,
      topN: value.topN as SeriesTopN,
      ...(value.title?.trim() ? { title: value.title.trim() } : {}),
    },
    notice: null,
  }
}

/** The series chart's model for `useChartConfig` (TRND-10). */
export const SERIES_CONFIG_MODEL: ChartConfigModel<SeriesChartConfig> = {
  read: readSeriesConfig,
  defaults: DEFAULT_SERIES_CONFIG,
  isDefault,
}
