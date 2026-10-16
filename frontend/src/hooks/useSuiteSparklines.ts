/**
 * TRND-06 (a): the Suite pass rates card's sparklines — each suite's daily
 * pass rate over the page's window, the SAME line "Pass rate by suite" draws.
 *
 * It reads the SAME request: the parameters are built by the same function
 * (`useCatalogueParams`) with the same arguments as the catalogue's default
 * suite-series request (`SUITE_SERIES_PARAMS`, the row-grain window cap), so
 * the SWR key is identical and one `top_n=7` request serves both
 * (`rollout-trends.spec.ts` pins exactly one). Nothing is asked until the
 * card is near (`near`, from its `LazySection`), and a one-day window asks
 * nothing (one point per suite is not a trend).
 *
 * `lineOf(suite)` matches the card's suite name to a series key ignoring case
 * and surrounding space; a suite that is not among the 7 busiest the chart
 * draws is `undefined` (the card prints "—" with the reason). A day without a
 * measured rate is `null`: a break in the line, never 0 %.
 */
import { useMemo } from 'react'
import { useCatalogChartData } from '@/components/charts/chartCatalogSources'
import { hasChartData } from '@/components/charts/chartStateCore'
import { OTHER_KEY } from '@/components/charts/multiSeriesModel'
import {
  clampCatalogueDays,
  ROW_GRAIN_MAX_WINDOW_DAYS,
  useCatalogueParams,
} from '@/components/reports/catalogue/catalogueScope'
import { SUITE_SERIES_PARAMS } from '@/components/reports/catalogue/trendsCatalogueParams'
import type { ScopeValue } from '@/lib/scopeParams'

export type SuiteSparklinesState = 'idle' | 'loading' | 'ready' | 'unavailable'

export interface SuiteSparklines {
  lineOf: (suiteName: string) => (number | null)[] | undefined
  state: SuiteSparklinesState
}

const norm = (name: string) => name.trim().toLowerCase()

interface RawPoint {
  x?: unknown
  y?: unknown
  measured?: unknown
}

/** The series of a chart-data answer as `name -> daily values`, defensively (no `.map` on a raw answer). */
export function sparklineLines(series: unknown): Map<string, (number | null)[]> {
  const lines = new Map<string, (number | null)[]>()
  if (!Array.isArray(series)) return lines
  for (const entry of series) {
    if (!entry || typeof entry !== 'object') continue
    const { key, label, points } = entry as { key?: unknown; label?: unknown; points?: unknown }
    if (typeof key !== 'string' || key === OTHER_KEY || !Array.isArray(points)) continue
    const ordered = (points as RawPoint[])
      .filter((p): p is RawPoint & { x: string } => Boolean(p) && typeof p.x === 'string')
      .slice()
      .sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0))
    const values = ordered.map((p) =>
      p.measured === false || typeof p.y !== 'number' || !Number.isFinite(p.y) ? null : p.y,
    )
    for (const name of [key, typeof label === 'string' ? label : null]) {
      if (name && !lines.has(norm(name))) lines.set(norm(name), values)
    }
  }
  return lines
}

export function useSuiteSparklines(days: number, suiteFilter: ScopeValue, near: boolean): SuiteSparklines {
  const windowDays = clampCatalogueDays(days, ROW_GRAIN_MAX_WINDOW_DAYS)
  // Called unconditionally (the rules of hooks); the request is gated below.
  const params = useCatalogueParams(days, suiteFilter, SUITE_SERIES_PARAMS, { maxDays: ROW_GRAIN_MAX_WINDOW_DAYS })
  const asking = near && windowDays >= 2 && params !== null
  const chart = useCatalogChartData('chart-data', { params: asking ? params : null, everHadData: null })

  const drawn = hasChartData(chart) && chart.data.series.kind === 'series' ? chart.data.series : null
  const lines = useMemo(() => sparklineLines(drawn?.series), [drawn])

  const state: SuiteSparklinesState = !asking
    ? 'idle'
    : drawn
      ? 'ready'
      : chart.status === 'loading'
        ? 'loading'
        : 'unavailable'

  const lineOf = useMemo(
    () => (suiteName: string) => (state === 'ready' ? lines.get(norm(suiteName)) : undefined),
    [lines, state],
  )
  return { lineOf, state }
}
