/**
 * Where the catalogue's charts get their data (VIZ-401 / VIZ-402).
 *
 * Three sources, one shape. `chart-data` (VIZ-203) already answers in contract
 * C3; the two older analytics endpoints answer in their own shapes, and a THIN
 * adapter turns each into the same `ChartResponse` inside the fetcher — before
 * `useChartData` validates it. So one pipeline
 *
 *     chartGet → adapt → validateChartResponse → ChartState → model → plot
 *                                                          ↘ table view
 *
 * serves both, and a chart that moves from `/analytics/top-failing` to
 * `chart-data` changes one string.
 *
 * Every request goes through `chartGet`, so it is abortable, un-toasted,
 * request-id-carrying and inside the 4-at-a-time cap (VIZ-209).
 *
 * Wave 3 adds four sources that answer in C2 + C3 already (`heatmap`,
 * `coverage-map`, `failure-groups`, `test-scatter`). Each is validated over
 * ALL of C3 and then held to the ONE kind it answers in, so its reader is
 * typed on that kind (`useCatalogChartData('heatmap', ...)` is a
 * `ChartResponse<MatrixChart>`) and can never be handed another.
 */
import { useChartData, type ChartFetcher, type ChartKey } from '@/hooks/useChartData'
import { chartGet, type ChartFetchResult } from '@/services/chartApi'
import type { AnyChartSeries, ChartSeries, GraphChart, MatrixChart, PointsChart, TreeChart, ValidationResult } from '@/lib/viz/contracts'
import { validateAnyChartResponse, validateChartResponse, type ChartResponse, type ChartState } from './chartState'
import { chartResponseFromFailureCategories, chartResponseFromTopFailing } from './BarChart.model'

export type CatalogSource =
  | 'chart-data'
  | 'top-failing'
  | 'failure-categories'
  | 'heatmap'
  | 'coverage-map'
  | 'failure-groups'
  | 'test-scatter'

export const CATALOG_URLS: Record<CatalogSource, string> = {
  'chart-data': '/api/v1/analytics/chart-data',
  'top-failing': '/api/v1/analytics/top-failing',
  'failure-categories': '/api/v1/analytics/failure-categories',
  heatmap: '/api/v1/analytics/heatmap',
  'coverage-map': '/api/v1/analytics/coverage-map',
  'failure-groups': '/api/v1/analytics/failure-groups',
  'test-scatter': '/api/v1/analytics/test-scatter',
}

/** The C3 kind each source's reader is handed. */
export interface CatalogSeriesBySource {
  'chart-data': ChartSeries
  'top-failing': ChartSeries
  'failure-categories': ChartSeries
  heatmap: MatrixChart
  'coverage-map': TreeChart
  /** The graph, plus the endpoint's additive keys (`groups`, `singletons`, …), kept on the object. */
  'failure-groups': GraphChart
  'test-scatter': PointsChart
}

/** The validated response of one source. */
export type CatalogResponse<S extends CatalogSource> = ChartResponse<CatalogSeriesBySource[S]>

/** The one kind each Wave-3 source answers in. The three older sources keep the four-kind check. */
export const CATALOG_KINDS: Partial<Record<CatalogSource, AnyChartSeries['kind']>> = {
  heatmap: 'matrix',
  'coverage-map': 'tree',
  'failure-groups': 'graph',
  'test-scatter': 'points',
}

/**
 * The wire's envelope as a `ChartResponse`. The analytics routes answer with
 * the C3 series' keys at the TOP level and `meta` beside them
 * (`with_meta(payload, meta)` = `{kind, ..., meta}`), while every chart reads
 * `{meta, series}`. A body that is already `{meta, series: {kind, ...}}` (the
 * hermetic fixtures, the gallery) passes through as the same object; anything
 * else is returned untouched for the validator to reject.
 */
export function chartResponseFromEnvelope(payload: unknown): unknown {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return payload
  const body = payload as Record<string, unknown>
  const wrapped = body.series
  if (wrapped && typeof wrapped === 'object' && !Array.isArray(wrapped)) return payload
  if (typeof body.kind !== 'string') return payload
  const { meta, ...series } = body
  return { meta: meta ?? null, series }
}

/** Query parameters, exactly as the caller means them (no client-side guessing). */
export type CatalogParams = Record<string, string | number | boolean | readonly string[] | undefined>

/** The adapter each source needs to reach the canonical `ChartResponse`. */
const ADAPTERS: Record<CatalogSource, (payload: unknown) => unknown> = {
  // Already C2 + C3: only the envelope is reshaped, and the validator has the last word.
  'chart-data': chartResponseFromEnvelope,
  'top-failing': (payload) => chartResponseFromTopFailing(payload) as unknown,
  'failure-categories': (payload) => chartResponseFromFailureCategories(payload) as unknown,
  heatmap: chartResponseFromEnvelope,
  'coverage-map': chartResponseFromEnvelope,
  'failure-groups': chartResponseFromEnvelope,
  'test-scatter': chartResponseFromEnvelope,
}

/**
 * The validator for one source: the four-kind check for the older sources
 * (byte-for-byte what they always had), all of C3 and then the source's one
 * kind for a Wave-3 source.
 */
export function catalogValidator<S extends CatalogSource>(source: S): (input: unknown) => ValidationResult<CatalogResponse<S>> {
  const kind = CATALOG_KINDS[source]
  if (!kind) return validateChartResponse as (input: unknown) => ValidationResult<CatalogResponse<S>>
  return (input) => {
    const checked = validateAnyChartResponse(input)
    if (!checked.ok) return checked
    if (checked.value.series.kind !== kind) {
      return { ok: false, errors: [`series: kind_enum: ${source} answers a ${kind} series, got ${checked.value.series.kind}`] }
    }
    return checked as ValidationResult<CatalogResponse<S>>
  }
}

/** The SWR key for a source and its parameters. */
export function catalogKey(source: CatalogSource, params: CatalogParams): ChartKey {
  return [source, params] as const
}

/** A `useChartData` fetcher for one source. */
export function catalogFetcher(source: CatalogSource, params: CatalogParams): ChartFetcher<ChartKey> {
  return async (_key, { signal }): Promise<ChartFetchResult> => {
    const result = await chartGet(CATALOG_URLS[source], { params, signal })
    return { data: ADAPTERS[source](result.data), requestId: result.requestId }
  }
}

export interface CatalogChartDataOptions {
  /** The UNFILTERED existence probe. Never derived from this chart's payload. */
  everHadData: boolean | null
  /** `null` while the caller has nothing to ask for yet. */
  params: CatalogParams | null
}

/** One chart's data, validated and normalised into a `ChartState`. */
export function useCatalogChartData<S extends CatalogSource>(
  source: S,
  { params, everHadData }: CatalogChartDataOptions,
): ChartState<CatalogResponse<S>> {
  const key = params === null ? null : catalogKey(source, params)
  const fetcher = catalogFetcher(source, params ?? {})
  return useChartData<CatalogResponse<S>, ChartKey>(key, fetcher, {
    validate: catalogValidator(source),
    everHadData,
  })
}
