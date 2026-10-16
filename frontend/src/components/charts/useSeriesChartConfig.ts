/**
 * VIZ-604 — one series chart's customisation (Trends' "Pass rate by suite").
 *
 * Since TRND-10 a thin wrapper over the generic `useChartConfig` with the
 * series model (`SERIES_CONFIG_MODEL`): `config` follows every change at once,
 * `applied` 300 ms after the last change (what the chart requests with); a
 * config comes from an applied saved view, else this browser's
 * `localStorage`, else the default, validated on read (`readSeriesConfig`) —
 * one that no longer fits falls back to the default and the reader is told
 * once (`notice`, consumed by the caller).
 */
import { SERIES_CONFIG_MODEL, type SeriesChartConfig } from './chartCustomiseModel'
import { useChartConfig, type ChartConfigState } from './useChartConfig'

export { CONFIG_DEBOUNCE_MS } from './useChartConfig'

export type SeriesChartConfigState = ChartConfigState<SeriesChartConfig>

export function useSeriesChartConfig(chartKey: string): SeriesChartConfigState {
  return useChartConfig(chartKey, SERIES_CONFIG_MODEL)
}
