/**
 * VIZ-604 — one chart's customisation, kept in this browser.
 *
 * `config` follows every change at once (the panel's controls); `applied` is
 * the same config 300 ms after the last change, which is what the chart
 * requests with, so a burst of edits is one request. A stored config is
 * validated on read (`readSeriesConfig`); one that no longer fits falls back
 * to the default and the reader is told once (`notice`, consumed by the
 * caller).
 */
import { useEffect, useState } from 'react'
import {
  DEFAULT_SERIES_CONFIG,
  readSeriesConfig,
  type SeriesChartConfig,
} from './chartCustomiseModel'

export const CONFIG_DEBOUNCE_MS = 300

const storageKeyOf = (chartKey: string) => `testlookup.chartConfig.${chartKey}`

function load(chartKey: string): { config: SeriesChartConfig; notice: string | null } {
  try {
    const raw = window.localStorage.getItem(storageKeyOf(chartKey))
    return readSeriesConfig(raw === null ? null : (JSON.parse(raw) as unknown))
  } catch {
    // Storage blocked, or a value that is not JSON: the default, said once.
    return { config: DEFAULT_SERIES_CONFIG, notice: null }
  }
}

function save(chartKey: string, config: SeriesChartConfig | null) {
  try {
    if (config === null) window.localStorage.removeItem(storageKeyOf(chartKey))
    else window.localStorage.setItem(storageKeyOf(chartKey), JSON.stringify(config))
  } catch {
    // Storage blocked: the customisation lasts for this page only.
  }
}

export interface SeriesChartConfigState {
  config: SeriesChartConfig
  applied: SeriesChartConfig
  notice: string | null
  setConfig: (next: SeriesChartConfig) => void
  reset: () => void
}

export function useSeriesChartConfig(chartKey: string): SeriesChartConfigState {
  const [initial] = useState(() => load(chartKey))
  const [config, setConfigState] = useState(initial.config)
  const [applied, setApplied] = useState(initial.config)
  const [notice, setNotice] = useState(initial.notice)

  useEffect(() => {
    if (config === applied) return
    const timer = setTimeout(() => setApplied(config), CONFIG_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [config, applied])

  // A config that fell back is rewritten as the default, so the notice is said once.
  useEffect(() => {
    if (initial.notice) save(chartKey, null)
  }, [chartKey, initial.notice])

  return {
    config,
    applied,
    notice,
    setConfig: (next) => {
      setNotice(null)
      setConfigState(next)
      save(chartKey, next)
    },
    reset: () => {
      setNotice(null)
      setConfigState(DEFAULT_SERIES_CONFIG)
      setApplied(DEFAULT_SERIES_CONFIG)
      save(chartKey, null)
    },
  }
}
