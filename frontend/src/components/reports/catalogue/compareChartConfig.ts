/**
 * TRND-10 — Compare's customisation (Trends › By suite): its title, its
 * metric, what it compares by, and the suites / releases the reader chose.
 * Pure: no React.
 *
 * The toolbar's "Compare by" and "Metric" selects MOVED into the Customise
 * panel (one place to change them); the toolbar keeps Suites and Releases.
 * `suites` / `releases` are present only when the reader chose them (the
 * `cmp_suite` / `cmp_release` URL keys, mirrored both ways), so a saved view
 * reproduces the chart a teammate saw. `by: null` is "the richest comparison
 * the selection supports" (`effectiveCompareBy`), the default.
 *
 * Guard rails, listed as unavailable with their reasons: a pie, a stacked
 * area (rates do not add up; stacking hides each line's own trend), a log
 * scale, and more than `COMPARE_MAX_SERIES` lines.
 */
import type { ChartConfigModel } from '@/components/charts/useChartConfig'
import {
  fixedField,
  TITLE_MAX,
  type CustomiseSpec,
  type UnavailableOption,
} from '@/components/charts/chartCustomiseModel'
import {
  COMPARE_BY_LABELS,
  COMPARE_MAX_SERIES,
  COMPARE_METRICS,
  COMPARE_TITLE,
  COMPARE_URL_KEYS,
  readCompareChoice,
  writeCompareChoice,
  type CompareBy,
  type CompareMetric,
} from './compareModel'

export { COMPARE_CHART_KEY } from './trendsCatalogueParams'

export const COMPARE_CONFIG_VERSION = 1

export interface CompareChartConfig {
  version: typeof COMPARE_CONFIG_VERSION
  /** `null`: the richest comparison the selection supports. */
  by: CompareBy | null
  metric: CompareMetric
  title?: string
  /** The reader's chosen suites (≤ 8), only when they chose. */
  suites?: string[]
  /** The reader's chosen releases (≤ 8), only when they chose. */
  releases?: string[]
}

export const DEFAULT_COMPARE_CONFIG: CompareChartConfig = { version: COMPARE_CONFIG_VERSION, by: null, metric: 'pass_rate' }

export const COMPARE_CONFIG_NOTICE = 'Your saved customisation of Compare no longer applies, so it shows the default.'

const BY_VALUES = Object.keys(COMPARE_BY_LABELS) as CompareBy[]
const METRIC_VALUES = Object.keys(COMPARE_METRICS) as CompareMetric[]

const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length <= COMPARE_MAX_SERIES && value.every((v) => typeof v === 'string' && v.length <= 500)

const cleanList = (values: readonly string[]) => [...new Set(values.map((v) => v.trim()).filter(Boolean))].sort()

/** A stored or applied config, validated: anything this build does not offer is the default, said once. */
export function readCompareConfig(raw: unknown): { config: CompareChartConfig; notice: string | null } {
  if (raw === null || raw === undefined) return { config: DEFAULT_COMPARE_CONFIG, notice: null }
  const value = raw as Partial<CompareChartConfig>
  const valid =
    typeof raw === 'object' &&
    !Array.isArray(raw) &&
    value.version === COMPARE_CONFIG_VERSION &&
    (value.by === null || value.by === undefined || BY_VALUES.includes(value.by)) &&
    METRIC_VALUES.includes(value.metric as CompareMetric) &&
    (value.title === undefined || (typeof value.title === 'string' && value.title.length <= TITLE_MAX)) &&
    (value.suites === undefined || isStringList(value.suites)) &&
    (value.releases === undefined || isStringList(value.releases))
  if (!valid) return { config: DEFAULT_COMPARE_CONFIG, notice: COMPARE_CONFIG_NOTICE }
  return {
    config: {
      version: COMPARE_CONFIG_VERSION,
      by: value.by ?? null,
      metric: value.metric as CompareMetric,
      ...(value.title?.trim() ? { title: value.title.trim() } : {}),
      ...(value.suites ? { suites: cleanList(value.suites) } : {}),
      ...(value.releases ? { releases: cleanList(value.releases) } : {}),
    },
    notice: null,
  }
}

export function isDefaultCompareConfig(config: CompareChartConfig): boolean {
  return (
    config.by === null &&
    config.metric === DEFAULT_COMPARE_CONFIG.metric &&
    !config.title?.trim() &&
    config.suites === undefined &&
    config.releases === undefined
  )
}

/** The whole config, as a saved view holds it (suites and releases included). */
export const COMPARE_CONFIG_MODEL: ChartConfigModel<CompareChartConfig> = {
  read: readCompareConfig,
  defaults: DEFAULT_COMPARE_CONFIG,
  isDefault: isDefaultCompareConfig,
}

/** The config without the reader's selection: the chart's own settings (title, metric, by). */
export function compareSettingsOf(config: CompareChartConfig): CompareChartConfig {
  const { suites: _s, releases: _r, ...settings } = config
  return settings
}

/**
 * What `CompareSection` keeps as ITS config (and in this browser's
 * `localStorage`): the settings only. The selection lives in the URL
 * (`cmp_suite` / `cmp_release`, unchanged since C1: a fresh visit starts from
 * the busiest suites) and is mirrored into the store for a saved view
 * (`useChartConfig`'s `mirror`); opening a view writes it back into the URL.
 */
export const COMPARE_SETTINGS_MODEL: ChartConfigModel<CompareChartConfig> = {
  read: (raw) => {
    const { config, notice } = readCompareConfig(raw)
    return { config: compareSettingsOf(config), notice }
  },
  defaults: DEFAULT_COMPARE_CONFIG,
  isDefault: isDefaultCompareConfig,
}

/** `config` with the URL's selection (already parsed; `null` = not chosen). */
export function withCompareChoice(
  config: CompareChartConfig,
  suites: readonly string[] | null,
  releases: readonly string[] | null,
): CompareChartConfig {
  return {
    ...compareSettingsOf(config),
    ...(suites !== null ? { suites: cleanList(suites).slice(0, COMPARE_MAX_SERIES) } : {}),
    ...(releases !== null ? { releases: cleanList(releases).slice(0, COMPARE_MAX_SERIES) } : {}),
  }
}

/** The frame's title: the reader's, else "Compare". */
export const compareTitleOf = (config: CompareChartConfig) => config.title?.trim() || COMPARE_TITLE

export const COMPARE_LINES_REASON = `More than ${COMPARE_MAX_SERIES} lines stop being readable on one chart; narrow the suites or releases instead.`
export const COMPARE_NO_BY_REASON = 'Pick two or more suites or releases first'

/** Compare's guard rails for this metric. */
export function compareUnavailable(metric: CompareMetric): UnavailableOption[] {
  const rate = COMPARE_METRICS[metric].kind === 'rate'
  return [
    { control: 'chartType', label: 'Pie', reason: 'A pie shows the parts of one whole, not series over time.' },
    rate
      ? { control: 'chartType', label: 'Stacked area', reason: 'Rates cannot be stacked: 40% on top of 60% is not 100% of anything.' }
      : { control: 'chartType', label: 'Stacked area', reason: 'Stacking hides each series’ own trend; compare the lines instead.' },
    rate
      ? { control: 'scale', label: 'Log', reason: 'A log scale exaggerates small differences between rates, and cannot show 0%.' }
      : { control: 'scale', label: 'Log', reason: 'A log scale cannot show a day with 0, which counts often have.' },
    { control: 'lines', label: `More than ${COMPARE_MAX_SERIES} lines`, reason: COMPARE_LINES_REASON },
  ]
}

export interface CompareSpecOptions {
  /** The comparisons the current selection supports (`compareOptions`). */
  byOptions: readonly CompareBy[]
  /** The comparison drawn now (`effectiveCompareBy`). */
  effectiveBy: CompareBy | null
}

/** The Customise panel for Compare: title, metric, compare by (only what the selection supports) and the guard rails. */
export function compareCustomiseSpec(config: CompareChartConfig, { byOptions, effectiveBy }: CompareSpecOptions): CustomiseSpec {
  const unavailable = compareUnavailable(config.metric)
  return {
    fields: [
      { id: 'title', label: 'Title', kind: 'text', value: config.title ?? '', placeholder: COMPARE_TITLE, maxLength: TITLE_MAX },
      {
        id: 'metric',
        label: 'Metric',
        kind: 'select',
        value: config.metric,
        options: METRIC_VALUES.map((key) => ({ value: key, label: COMPARE_METRICS[key].label })),
      },
      byOptions.length > 0 && effectiveBy
        ? {
            id: 'by',
            label: 'Compare by',
            kind: 'select' as const,
            value: effectiveBy,
            options: byOptions.map((key) => ({ value: key, label: COMPARE_BY_LABELS[key] })),
          }
        : {
            id: 'by',
            label: 'Compare by',
            kind: 'select' as const,
            value: '',
            options: [{ value: '', label: COMPARE_NO_BY_REASON, disabled: true, reason: COMPARE_NO_BY_REASON }],
          },
      fixedField('chartType', 'Chart type', { value: 'line', label: 'Line' }, unavailable),
      fixedField('scale', 'Scale', { value: 'linear', label: 'Linear' }, unavailable),
    ],
    unavailable,
    isDefault: config.by === null && config.metric === DEFAULT_COMPARE_CONFIG.metric && !config.title?.trim(),
  }
}

/** `config` with the panel's field set; a value Compare does not offer changes nothing. */
export function applyCompareField(config: CompareChartConfig, id: string, value: string): CompareChartConfig {
  if (id === 'title') {
    const { title: _old, ...rest } = config
    return value ? { ...rest, title: value } : rest
  }
  if (id === 'metric') return METRIC_VALUES.includes(value as CompareMetric) ? { ...config, metric: value as CompareMetric } : config
  if (id === 'by') return BY_VALUES.includes(value as CompareBy) ? { ...config, by: value as CompareBy } : config
  return config
}

/**
 * Reset from the panel: the chart's own settings (title, metric, by) to their
 * defaults; the reader's suites and releases are their selection, not a
 * customisation of the chart, and stay.
 */
export function resetCompareSettings(config: CompareChartConfig): CompareChartConfig {
  return {
    ...DEFAULT_COMPARE_CONFIG,
    ...(config.suites ? { suites: config.suites } : {}),
    ...(config.releases ? { releases: config.releases } : {}),
  }
}

// ── URL mirroring (cmp_suite / cmp_release) ──────────────────────────────────

/** The config with its suites / releases set to the URL's choice (absent key = not chosen). */
export function compareConfigWithUrl(config: CompareChartConfig, params: URLSearchParams): CompareChartConfig {
  return withCompareChoice(
    config,
    readCompareChoice(params, COMPARE_URL_KEYS.suite),
    readCompareChoice(params, COMPARE_URL_KEYS.release),
  )
}

/** `params` carrying the config's suites / releases (a config without them removes the keys). */
export function urlWithCompareConfig(params: URLSearchParams, config: CompareChartConfig): URLSearchParams {
  let next = new URLSearchParams(params)
  next.delete(COMPARE_URL_KEYS.suite)
  next.delete(COMPARE_URL_KEYS.release)
  if (config.suites !== undefined) next = writeCompareChoice(next, COMPARE_URL_KEYS.suite, config.suites)
  if (config.releases !== undefined) next = writeCompareChoice(next, COMPARE_URL_KEYS.release, config.releases)
  return next
}

/** Whether the URL's choice and the config's are the same selection. */
export function sameCompareChoice(config: CompareChartConfig, params: URLSearchParams): boolean {
  const key = (list: readonly string[] | null | undefined) => (list === null || list === undefined ? null : cleanList(list).join('\n'))
  return (
    key(config.suites) === key(readCompareChoice(params, COMPARE_URL_KEYS.suite)) &&
    key(config.releases) === key(readCompareChoice(params, COMPARE_URL_KEYS.release))
  )
}
