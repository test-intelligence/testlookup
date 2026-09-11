import useSWR, { mutate } from 'swr'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { suitesService } from '@/services/suitesService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useSuites() {
  return useProjectScopedSWR(
    'suites',
    (projectId) => suitesService.list(projectId),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}

export function useSuite(suiteId?: string) {
  return useSWR(
    suiteId ? ['suite', suiteId] : null,
    ([, id]: readonly [string, string]) => suitesService.get(id),
  )
}

export function useSuiteTestCases(suiteId?: string) {
  return useSWR(
    suiteId ? ['suite', suiteId, 'cases'] : null,
    ([, id]: readonly [string, string, string]) => suitesService.listSuiteCases(id),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}

export function useCanonicalRuns(canonicalId?: string) {
  return useSWR(
    canonicalId ? ['canonical', canonicalId, 'runs'] : null,
    ([, id]: readonly [string, string, string]) => suitesService.listCanonicalRuns(id),
  )
}

export function useCanonicalCase(canonicalId?: string) {
  return useSWR(
    canonicalId ? ['canonical', canonicalId] : null,
    ([, id]: readonly [string, string]) => suitesService.getCanonicalCase(id),
  )
}

/** The orphaned-cases panel's page size (the backend's own default). */
export const ORPHANED_PAGE_SIZE = 25

/**
 * One page of orphaned canonicals. N16: this used to ask for no page at all,
 * got the backend's first 25, and the panel printed the full total above
 * them, so "60" sat over 25 rows with no way to reach the other 35.
 */
export function useOrphanedCanonicalCases(page = 1, size = ORPHANED_PAGE_SIZE) {
  return useProjectScopedSWR(
    'canonical-orphaned',
    (projectId) => suitesService.listOrphanedCanonicals(projectId, { page, size }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, keepPreviousData: true },
    [page, size],
  )
}

/** Invalidate every suite-keyed SWR entry — call after any mutation. */
export function refreshSuites() {
  return mutate(
    (key: unknown) =>
      Array.isArray(key) && (key[0] === 'suites' || key[0] === 'suite'),
  )
}
