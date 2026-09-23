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
 */
import { useChartData, type ChartFetcher, type ChartKey } from '@/hooks/useChartData'
import { chartGet, type ChartFetchResult } from '@/services/chartApi'
import { validateChartResponse, type ChartResponse, type ChartState } from './chartState'
import { chartResponseFromFailureCategories, chartResponseFromTopFailing } from './BarChart.model'

export type CatalogSource = 'chart-data' | 'top-failing' | 'failure-categories'

export const CATALOG_URLS: Record<CatalogSource, string> = {
  'chart-data': '/api/v1/analytics/chart-data',
  'top-failing': '/api/v1/analytics/top-failing',
  'failure-categories': '/api/v1/analytics/failure-categories',
}

/** Query parameters, exactly as the caller means them (no client-side guessing). */
export type CatalogParams = Record<string, string | number | boolean | readonly string[] | undefined>

/** The adapter each source needs to reach the canonical `ChartResponse`. */
const ADAPTERS: Record<CatalogSource, (payload: unknown) => unknown> = {
  // Already C2 + C3: pass it through, and let the validator have the last word.
  'chart-data': (payload) => payload,
  'top-failing': (payload) => chartResponseFromTopFailing(payload) as unknown,
  'failure-categories': (payload) => chartResponseFromFailureCategories(payload) as unknown,
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
export function useCatalogChartData(
  source: CatalogSource,
  { params, everHadData }: CatalogChartDataOptions,
): ChartState<ChartResponse> {
  const key = params === null ? null : catalogKey(source, params)
  const fetcher = catalogFetcher(source, params ?? {})
  return useChartData<ChartResponse, ChartKey>(key, fetcher, {
    validate: validateChartResponse,
    everHadData,
  })
}
