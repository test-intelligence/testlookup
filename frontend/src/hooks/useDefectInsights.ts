/**
 * W06 DEF-04 / DEF-03: the Defects page's two server aggregates, beside (not
 * inside) `useMetrics.ts`, whose fixed `vi.mock` factories in ten test files
 * would turn a new export there into `undefined`.
 *
 *   useDefectTrend      GET /analytics/defects/trend        one point per UTC day + totals over
 *                                                            EVERY defect in scope (the tiles' values)
 *   useDefectEscapeRate GET /analytics/defects/escape-rate  the escape rate (no suite: it is per release)
 *
 * Both take the page's `DefectScopeQuery`. Every part of the scope that changes
 * the request is in the SWR key too (a release in the params but not the key
 * would serve another release's numbers under this one's name), and an array
 * enters the key as `keyPart`'s joined string, never as a fresh array.
 */
import type { SWRResponse } from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { keyPart } from '@/lib/scopeParams'
import { analyticsService } from '@/services/analyticsService'
import type { DefectEscapeRateResponse, DefectScopeQuery, DefectTrendResponse } from '@/types/analytics'
import { appMutate } from '@/utils/swrCacheMutate'
import { useProjectScopedSWR } from './useProjectScopedSWR'

const TREND_KEY = 'defect-trend'
const ESCAPE_KEY = 'defect-escape-rate'
/** The server's default window (`ScopePolicy(default_days=30)`), for a caller that names none. */
const DEFAULT_DAYS = 30

function releaseIdsOf(scope: DefectScopeQuery): string[] | undefined {
  return scope.releaseIds && scope.releaseIds.length > 0 ? [...scope.releaseIds] : undefined
}

/** Key `['defect-trend', projectId, days, keyPart(releaseIds), releaseMatch, suite]`. */
export function useDefectTrend(scope: DefectScopeQuery): SWRResponse<DefectTrendResponse> {
  const days = scope.days ?? DEFAULT_DAYS
  const releaseIds = releaseIdsOf(scope)
  const releaseMatch = releaseIds ? scope.releaseMatch : undefined
  const suiteName = scope.suiteName || null
  return useProjectScopedSWR(
    TREND_KEY,
    (projectId) => analyticsService.getDefectTrend(projectId, { days, releaseIds, releaseMatch, suiteName }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(releaseIds) ?? null, releaseMatch ?? null, suiteName],
  )
}

/** Key `['defect-escape-rate', projectId, days, keyPart(releaseIds)]`; the suite is never sent. */
export function useDefectEscapeRate(scope: DefectScopeQuery): SWRResponse<DefectEscapeRateResponse> {
  const days = scope.days ?? DEFAULT_DAYS
  const releaseIds = releaseIdsOf(scope)
  return useProjectScopedSWR(
    ESCAPE_KEY,
    (projectId) => analyticsService.getDefectEscapeRate(projectId, { days, releaseIds }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(releaseIds) ?? null],
  )
}

/** Revalidates every trend and escape-rate entry (after a defect changed, or one was filed). */
export function refreshDefectInsights(): Promise<unknown> {
  return appMutate((key: unknown) => Array.isArray(key) && (key[0] === TREND_KEY || key[0] === ESCAPE_KEY))
}
