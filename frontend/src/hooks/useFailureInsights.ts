/**
 * W07 FAIL-02: the Failures page's server-measured time to fix, beside (not
 * inside) `useMetrics.ts`, whose fixed `vi.mock` factories in ten test files
 * would turn a new export there into `undefined`.
 *
 *   useTimeToFix  GET /analytics/time-to-fix  the median time from a test's first
 *                                             failure to its next pass, over the window
 *
 * Scoped like the page's other analytics reads: the active project (none in
 * All Projects: the server answers over every accessible project), the
 * top-bar release and the page's suite. Every part of the scope that changes
 * the request is in the SWR key too (a release in the params but not the key
 * would serve another release's numbers under this one's name).
 */
import type { SWRResponse } from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { keyPart, scopeArg, type ScopeValue } from '@/lib/scopeParams'
import { analyticsService } from '@/services/analyticsService'
import type { TimeToFixResponse } from '@/types/analytics'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'

/** The SWR key's head: `['analytics-time-to-fix', projectId, days, suite, release]`. */
export const TIME_TO_FIX_KEY = 'analytics-time-to-fix'

export function useTimeToFix(days: number, suiteName?: ScopeValue): SWRResponse<TimeToFixResponse> {
  const releaseIds = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    TIME_TO_FIX_KEY,
    (projectId) => analyticsService.getTimeToFix(projectId, { days, releaseIds, suiteName }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteName), keyPart(releaseIds)],
  )
}
