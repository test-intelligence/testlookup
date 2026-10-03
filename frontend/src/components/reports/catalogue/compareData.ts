/**
 * VIZ-605 — the comparison's data: ONE `useChartData` state over one or more
 * `chart-data` requests (one per release when comparing by suite and release),
 * so the frame has one loading / error / empty story and one Retry.
 *
 * Each slice is validated exactly as a single chart-data response is
 * (`catalogValidator('chart-data')`) and must be a day × series chart; one bad
 * slice fails the whole comparison rather than drawing a partial grid.
 */
import { useMemo } from 'react'
import {
  CATALOG_URLS,
  catalogValidator,
  chartResponseFromEnvelope,
} from '@/components/charts/chartCatalogSources'
import type { ChartAccessors, ChartState } from '@/components/charts/chartState'
import { useChartData, type ChartKey } from '@/hooks/useChartData'
import type { EnvelopeMeta, ValidationResult } from '@/lib/viz/contracts'
import { chartGet } from '@/services/chartApi'
import type { CompareRequest, CompareSlice } from './compareModel'

/** What the comparison's state holds: the slices, in request order, and one envelope for the frame. */
export interface CompareData {
  slices: CompareSlice[]
  meta: EnvelopeMeta | null
}

interface RawCompare {
  parts: { releaseId: string | null; payload: unknown }[]
}

/**
 * One envelope for the frame's footer and badges: the first slice's, with the
 * releases of every slice and the matched totals added up (a run has one
 * primary release, so the slices never count a run twice). The unfiltered
 * `total_*` are the same in every slice.
 */
export function mergeCompareMeta(slices: readonly CompareSlice[]): EnvelopeMeta | null {
  const metas = slices.map((slice) => slice.meta).filter((meta): meta is EnvelopeMeta => meta !== null)
  const first = metas[0]
  if (!first || metas.length === 1) return first ?? null
  const releases = new Map(first.scope.releases.map((r) => [r.id, r]))
  for (const meta of metas.slice(1)) for (const r of meta.scope.releases) if (!releases.has(r.id)) releases.set(r.id, r)
  const sum = (field: 'matched_runs' | 'matched_executions') => metas.reduce((total, meta) => total + meta.totals[field], 0)
  return {
    ...first,
    scope: { ...first.scope, releases: [...releases.values()] },
    totals: { ...first.totals, matched_runs: sum('matched_runs'), matched_executions: sum('matched_executions') },
  }
}

/** Every slice validated; any failure is the comparison's failure. */
export function validateCompare(input: unknown): ValidationResult<CompareData> {
  const raw = input as RawCompare
  if (!raw || !Array.isArray(raw.parts)) return { ok: false, errors: ['compare: no slices'] }
  const validate = catalogValidator('chart-data')
  const slices: CompareSlice[] = []
  for (const [i, part] of raw.parts.entries()) {
    const checked = validate(part.payload)
    if (!checked.ok) return { ok: false, errors: checked.errors.map((e) => `slice ${i}: ${e}`) }
    if (checked.value.series.kind !== 'series') {
      return { ok: false, errors: [`slice ${i}: expected a day x series chart, got ${checked.value.series.kind}`] }
    }
    slices.push({ releaseId: part.releaseId, chart: checked.value.series, meta: checked.value.meta })
  }
  return { ok: true, value: { slices, meta: mergeCompareMeta(slices) } }
}

export const COMPARE_ACCESSORS: ChartAccessors<CompareData> = {
  meta: (value) => value.meta,
  isEmpty: (value) => value.slices.every((slice) => slice.chart.series.every((s) => s.points.length === 0)),
  shown: (value) => value.slices.reduce((total, slice) => total + slice.chart.series.length, 0),
}

/** The comparison's state; `requests: null` asks nothing. */
export function useCompareData(requests: readonly CompareRequest[] | null, everHadData: boolean | null): ChartState<CompareData> {
  // A string identity: the key must not change while the requests do not.
  const identity = requests === null ? null : JSON.stringify(requests)
  const key = useMemo<ChartKey | null>(() => (identity === null ? null : ['compare', identity]), [identity])
  const fetcher = async (_key: ChartKey, { signal }: { signal: AbortSignal }) => {
    const list = requests ?? []
    const results = await Promise.all(list.map((r) => chartGet(CATALOG_URLS['chart-data'], { params: r.params, signal })))
    const data: RawCompare = {
      parts: results.map((result, i) => ({ releaseId: list[i].releaseId, payload: chartResponseFromEnvelope(result.data) })),
    }
    return { data, requestId: results[0]?.requestId ?? null }
  }
  return useChartData<CompareData, ChartKey>(key, fetcher, {
    validate: validateCompare,
    accessors: COMPARE_ACCESSORS,
    everHadData,
  })
}
