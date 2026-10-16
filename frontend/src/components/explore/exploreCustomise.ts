/**
 * TRND-10 — the Explorer's Customise panel, as a spec for the kit's generic
 * side panel (`ChartCustomisePanel`). Pure: no React.
 *
 * Two controls of its own: a **Title** (a heading over the grid; none by
 * default) and **Lines per panel** (3 / 5 / 7, the busiest first and the rest
 * as "Other", sent as `top_n`; listed as unavailable, with `Pick "Lines"
 * first`, while no series is chosen). The metric, the axis, the lines and
 * the panels stay the Explorer's own pickers: the panel does not repeat them.
 * The kit's guard rails are listed as for every series chart (no pie, no
 * stacked rates, no log scale, at most 7 lines plus "Other"), each with its
 * reason, so a misleading chart is never one click away.
 *
 * Both values are part of the Explorer's configuration (`exploreUrl`), so the
 * URL and a saved view carry them; this file only describes and applies them.
 */
import { fixedField, type CustomiseField, type CustomiseSpec, type UnavailableOption } from '@/components/charts/chartCustomiseModel'
import { EXPLORE_METRICS, EXPLORE_TITLE_MAX, EXPLORE_TOP_N, type ExploreConfig, type ExploreTopN } from './exploreModel'

/** "No lines-per-panel cap" in the select: the server's default (up to 7 and "Other"). */
export const LINES_DEFAULT = 'default'
export const LINES_NEED_SERIES_REASON = 'Pick "Lines" first'
export const EXPLORE_TITLE_PLACEHOLDER = 'No heading'

/** The guard rails of the Explorer's charts, for this metric. */
export function exploreUnavailable(config: Pick<ExploreConfig, 'metric' | 'series'>): UnavailableOption[] {
  const rate = EXPLORE_METRICS[config.metric].kind === 'rate'
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
  if (config.series === null) options.unshift({ control: 'topN', label: 'Lines per panel', reason: LINES_NEED_SERIES_REASON })
  return options
}

/** Whether the panel has anything to reset: no title and no lines-per-panel cap. */
export const isDefaultExploreCustomise = (config: Pick<ExploreConfig, 'topN' | 'title'>): boolean =>
  config.topN === null && !config.title?.trim()

/** The panel's fields, guard rails and default state for this configuration. */
export function exploreCustomiseSpec(config: ExploreConfig): CustomiseSpec {
  const unavailable = exploreUnavailable(config)
  const noSeries = config.series === null
  const lines: CustomiseField = {
    id: 'topN',
    label: 'Lines per panel (busiest first, the rest as “Other”)',
    kind: 'select',
    value: config.topN === null ? LINES_DEFAULT : String(config.topN),
    options: [
      { value: LINES_DEFAULT, label: 'Default (up to 7)', disabled: noSeries, reason: noSeries ? LINES_NEED_SERIES_REASON : undefined },
      ...EXPLORE_TOP_N.map((n) => ({
        value: String(n),
        label: String(n),
        disabled: noSeries,
        reason: noSeries ? LINES_NEED_SERIES_REASON : undefined,
      })),
      ...unavailable
        .filter((option) => option.control === 'topN' && option.label === 'More than 7')
        .map((option) => ({ value: `unavailable:${option.label}`, label: option.label, disabled: true, reason: option.reason })),
    ],
  }
  return {
    fields: [
      {
        id: 'title',
        label: 'Title',
        kind: 'text',
        value: config.title ?? '',
        placeholder: EXPLORE_TITLE_PLACEHOLDER,
        maxLength: EXPLORE_TITLE_MAX,
      },
      lines,
      fixedField('chartType', 'Chart type', { value: 'line', label: 'Line' }, unavailable),
      fixedField('scale', 'Scale', { value: 'linear', label: 'Linear' }, unavailable),
    ],
    unavailable,
    isDefault: isDefaultExploreCustomise(config),
  }
}

/** `config` with the panel's field `id` set to `value`; anything this panel does not offer changes nothing. */
export function applyExploreField(config: ExploreConfig, id: string, value: string): ExploreConfig {
  if (id === 'title') {
    const title = value.slice(0, EXPLORE_TITLE_MAX)
    const next: ExploreConfig = { ...config }
    if (title.trim()) next.title = title
    else delete next.title
    return next
  }
  if (id === 'topN') {
    if (config.series === null) return config
    if (value === LINES_DEFAULT) return { ...config, topN: null }
    const n = Number(value)
    return (EXPLORE_TOP_N as readonly number[]).includes(n) && /^\d+$/.test(value) ? { ...config, topN: n as ExploreTopN } : config
  }
  return config
}

/** The configuration with the panel's two values back at their defaults. */
export function resetExploreCustomise(config: ExploreConfig): ExploreConfig {
  const next: ExploreConfig = { ...config, topN: null }
  delete next.title
  return next
}
