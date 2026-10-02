/**
 * K7 (Wave 2.6): the Summary report's trend — `/metrics/trends`, the same
 * endpoint and day series Overview and Trends draw — read through the CHART
 * pipeline instead of the page hooks.
 *
 * Why not `useTrendData`: that hook goes through the shared axios interceptor,
 * which toasts a failure, and is outside the 4-in-flight chart cap. A chart
 * section must fail inside its own frame (VIZ-107: six failing charts are not
 * six toasts, and one failing chart does not blank the page), carry the
 * request id on its error, abort when superseded, and queue behind the cap —
 * which is exactly `chartGet` + `useChartData`. The payload is not a
 * `ChartResponse`, so it brings its own validator and accessors; no
 * `chartCatalogSources` entry is needed (K8: no change).
 *
 * The scope is `catalogueParams`: the window clamped to 90 days, nothing asked
 * before the project resolves, no `project_id` (and no release) in All
 * Projects, and one release as a scalar. Summary ignores the suite filter, so
 * this does too.
 */
import { useMemo } from 'react'
import type { ValidationResult } from '@/lib/viz/contracts'
import { chartGet } from '@/services/chartApi'
import { useChartData, type ChartFetcher, type ChartKey } from '@/hooks/useChartData'
import type { ChartAccessors, ChartState } from '@/components/charts/chartState'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { normalizeScope, SCOPE_KEY_SEP, scopeKey, type ScopeValue } from '@/lib/scopeParams'
import type { TrendPoint, TrendResponse } from '@/types/metrics'
import { catalogueParams } from './catalogueScope'

export const TRENDS_SERIES_URL = '/api/v1/metrics/trends'

const isDict = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0

const COUNTS = ['passed', 'failed', 'skipped', 'broken'] as const

/**
 * The `/metrics/trends` payload (`TrendResponse` in `backend/app/models/schemas.py`),
 * checked at the boundary: a point without a date or with a count that is not
 * a count would draw a wrong day, so the whole payload is refused and the
 * frame shows an error naming the request.
 */
export function validateTrendResponse(input: unknown): ValidationResult<TrendResponse> {
  if (!isDict(input)) return { ok: false, errors: ['invalid_type: a trends response must be an object'] }
  const errors: string[] = []
  if (!Array.isArray(input.data)) errors.push('required_field: data must be a list')
  if (typeof input.period_days !== 'number' || !Number.isFinite(input.period_days)) {
    errors.push('required_field: period_days must be a number')
  }
  const points = Array.isArray(input.data) ? input.data : []
  points.forEach((point, i) => {
    if (!isDict(point)) {
      errors.push(`invalid_type: data[${i}] must be an object`)
      return
    }
    if (typeof point.date !== 'string' || point.date === '') errors.push(`required_field: data[${i}].date`)
    for (const field of COUNTS) {
      if (!isCount(point[field])) errors.push(`invalid_type: data[${i}].${field} must be a count`)
    }
    if (point.total !== undefined && !isCount(point.total)) errors.push(`invalid_type: data[${i}].total must be a count`)
    if (typeof point.pass_rate !== 'number' || !Number.isFinite(point.pass_rate)) {
      errors.push(`invalid_type: data[${i}].pass_rate must be a number`)
    }
  })
  if (errors.length) return { ok: false, errors }
  return { ok: true, value: { data: points as TrendPoint[], period_days: input.period_days as number } }
}

/** How the chart state reads a trends payload: no envelope, empty with no days. */
export const TREND_RESPONSE_ACCESSORS: ChartAccessors<TrendResponse> = {
  meta: () => null,
  isEmpty: (value) => value.data.length === 0,
  shown: (value) => value.data.length,
}

const fetchTrends: ChartFetcher<ChartKey> = (key, { signal }) =>
  chartGet(TRENDS_SERIES_URL, { params: (key as readonly [string, CatalogParams])[1], signal })

export interface UseTrendsSeriesOptions {
  /**
   * The unfiltered existence probe. Default `true`: Summary mounts its charts
   * only inside its own has-data branch, so the project has had runs.
   */
  everHadData?: boolean | null
}

export function useTrendsSeries(
  days: number,
  releaseScope: ScopeValue,
  { everHadData = true }: UseTrendsSeriesOptions = {},
): ChartState<TrendResponse> {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const releaseKey = scopeKey(releaseScope)
  const key = useMemo(() => {
    const allProjects = activeProjectId === ALL_PROJECTS_ID
    const params = catalogueParams({
      projectId: allProjects ? null : activeProjectId,
      allProjects,
      days,
      releaseIds: releaseKey === null ? null : normalizeScope(releaseKey.split(SCOPE_KEY_SEP)),
    })
    return params === null ? null : (['catalogue-trends', params] as const)
  }, [activeProjectId, days, releaseKey])
  return useChartData<TrendResponse, ChartKey>(key, fetchTrends, {
    validate: validateTrendResponse,
    everHadData,
    accessors: TREND_RESPONSE_ACCESSORS,
  })
}
