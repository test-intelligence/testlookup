// Fallback windows default to 30 to agree with
// ``DEFAULT_TIME_WINDOW_DAYS``. Every page passes an explicit value from
// the store, so these only apply to a caller that omits it — but a
// fallback that disagrees with the store is a trap for the next one.
import useSWR, { mutate } from 'swr'
import { metricsService } from '@/services/metricsService'
import { analyticsService } from '@/services/analyticsService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'
import { keyPart, scopeArg, type ScopeValue } from '@/lib/scopeParams'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
// Scoped reads opt into superseded-scope aborting (VIZ-303): a request built
// from a scope the user has since moved off is cancelled. Only these fetchers
// are marked; see services/scopeAbort.ts.
import { scopedFetch } from '@/services/scopeAbort'

export function refreshDefects() {
  return mutate((key: unknown) => Array.isArray(key) && key[0] === 'analytics-defects')
}

export function useDashboardSummary(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'metrics-summary',
    (projectId) => scopedFetch(() => metricsService.getSummary(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useTrendData(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'metrics-trends',
    (projectId) => scopedFetch(() => metricsService.getTrends(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useFlakyTests(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'analytics-flaky',
    (projectId) => scopedFetch(() => analyticsService.getFlakyTests(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    // `releaseId` belongs in the deps, which become the SWR key. In the params
    // alone it would change the request without changing the cache entry, so
    // switching releases would render the previous one's numbers under the new
    // one's name.
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useFailureCategories(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'analytics-categories',
    (projectId) => scopedFetch(() => analyticsService.getFailureCategories(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useTopFailing(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'analytics-top-failing',
    (projectId) => scopedFetch(() => analyticsService.getTopFailing(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useCoverage(days = 30, suiteName?: ScopeValue) {
  const releaseId = scopeArg(useReleaseScope())
  return useProjectScopedSWR(
    'analytics-coverage',
    (projectId) => scopedFetch(() => analyticsService.getCoverage(projectId, days, suiteName, releaseId)),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, keyPart(suiteName), keyPart(releaseId)],
  )
}

export function useDefects(page = 1, resolutionStatus?: string) {
  return useProjectScopedSWR(
    'analytics-defects',
    (projectId) => analyticsService.getDefects(projectId, { page, resolution_status: resolutionStatus }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [page, resolutionStatus],
  )
}

/**
 * Run-level aggregates for one suite.
 *
 * `releaseScoped: false` opts a caller out of the global release filter, for
 * questions that are not release-shaped. The motivating case is a data-INTEGRITY
 * diagnostic: `SuiteCasesPage` compares these run-level totals against an
 * unscoped list of test cases to tell "this suite never ingested anything" from
 * "runs landed but per-test rows were dropped". Scoping one side of that
 * comparison and not the other lets a release selection zero the totals and
 * silently retract the warning — the filter would be hiding an ingestion bug
 * rather than narrowing a result. A comparison must have both halves on the
 * same scope.
 */
export function useSuiteDetail(
  suiteName: string | null,
  days = 30,
  options?: { releaseScoped?: boolean },
) {
  const projectId = useActiveProjectId()
  const scopedReleaseId = scopeArg(useReleaseScope())
  const releaseId = options?.releaseScoped === false ? null : scopedReleaseId
  const fetchProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  return useSWR(
    // This hook builds its key by hand rather than through
    // `useProjectScopedSWR`, so the release has to be added in BOTH places
    // explicitly — the key here and the argument below. `keyPart`: several
    // releases (VIZ-303) enter the key as ONE sorted joined string, never an
    // array, and a single release stays exactly the legacy scalar.
    projectId && suiteName
      ? ['analytics-suite-detail', projectId, suiteName, days, keyPart(releaseId)]
      : null,
    () => analyticsService.getSuiteDetail(fetchProjectId, suiteName as string, days, releaseId),
    { revalidateOnFocus: false },
  )
}

export function useAiSummary(days = 30) {
  return useProjectScopedSWR(
    'analytics-ai-summary',
    (projectId) => analyticsService.getAiSummary(projectId, days),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days],
  )
}

/**
 * Evidence checklist behind an AI-classified failure kind (AI-4).
 * Fetches lazily — pass `enabled=false` until the popover opens.
 * Fingerprint lookups need a concrete project (fingerprints are only
 * unique per tenant); test_case_id lookups work in any project mode.
 */
export function useKindEvidence(
  lookup: { testFingerprint?: string | null; testCaseId?: string | null },
  enabled: boolean,
) {
  const projectId = useActiveProjectId()
  const concreteProjectId = projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
  const canFetch = enabled && (
    lookup.testCaseId
      ? true
      : Boolean(lookup.testFingerprint && concreteProjectId)
  )
  return useSWR(
    canFetch
      ? ['analytics-kind-evidence', concreteProjectId, lookup.testCaseId ?? '', lookup.testFingerprint ?? '']
      : null,
    () => analyticsService.getKindEvidence({
      projectId: concreteProjectId,
      testFingerprint: lookup.testFingerprint,
      testCaseId: lookup.testCaseId,
    }),
    { revalidateOnFocus: false },
  )
}
