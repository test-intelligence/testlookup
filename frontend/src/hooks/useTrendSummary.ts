/**
 * TRND-06: the server-side trend summary (`GET /analytics/trend-summary`) —
 * the verdict, the confidence score and its four dimensions, the variance and
 * the run gap the Trends page shows. Keyed like `useTrendData` (window, suite,
 * release), so it follows the same scope; the answer goes through
 * `parseTrendSummary`, and a missing or malformed one is `summary: null` (the
 * page says "Score not available" and renders everything else).
 */
import { useMemo } from 'react'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from '@/hooks/useProjectScopedSWR'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { keyPart, scopeArg, type ScopeValue } from '@/lib/scopeParams'
import { trendsSummaryFetch } from '@/services/trendsReportService'
import { parseTrendSummary, type TrendSummary } from '@/types/trends'

export const TREND_SUMMARY_KEY = 'analytics-trend-summary'

export function useTrendSummary(
  days: number,
  suiteFilter: ScopeValue,
): { summary: TrendSummary | null; isLoading: boolean; error: unknown } {
  const release = scopeArg(useReleaseScope())
  const { data, isLoading, error } = useProjectScopedSWR<unknown>(
    TREND_SUMMARY_KEY,
    (pid) => trendsSummaryFetch(pid, days, suiteFilter, release),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteFilter), keyPart(release)],
  )
  const summary = useMemo(() => parseTrendSummary(data), [data])
  return { summary, isLoading: Boolean(isLoading), error }
}
