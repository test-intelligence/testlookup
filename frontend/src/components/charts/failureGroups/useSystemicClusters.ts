/**
 * The systemic clusters request (VIZ-504): `chartGet` (abortable, toast-free,
 * request-id-carrying, inside the chart request cap) + `useChartData` (one
 * `ChartState`, 429 waits, Retry), checked by `validateClustersResponse`.
 */
import { useChartData, type ChartFetcher, type ChartKey } from '@/hooks/useChartData'
import { chartGet } from '@/services/chartApi'
import type { CatalogParams } from '../chartCatalogSources'
import type { ChartAccessors } from '../chartState'
import type { ChartState } from '../chartStateCore'
import { CLUSTERS_URL, validateClustersResponse, type SystemicClustersResponse } from './systemicClusters.model'

export const CLUSTER_ACCESSORS: ChartAccessors<SystemicClustersResponse> = {
  meta: (value) => value.meta,
  // An empty list is an ANSWER (most projects have none): drawn, with the server's sentence.
  isEmpty: () => false,
  shown: (value) => value.clusters.length,
}

const fetcher: ChartFetcher<ChartKey> = async (key, { signal }) => {
  const params = (key as readonly unknown[])[1] as CatalogParams
  return chartGet(CLUSTERS_URL, { params, signal })
}

/** The clusters request's state; `params` `null` = ask nothing. */
export function useSystemicClusters(params: CatalogParams | null): ChartState<SystemicClustersResponse> {
  const key: ChartKey | null = params === null ? null : (['systemic-clusters', params] as const)
  return useChartData<SystemicClustersResponse, ChartKey>(key, fetcher, {
    validate: validateClustersResponse,
    everHadData: null,
    accessors: CLUSTER_ACCESSORS,
  })
}
