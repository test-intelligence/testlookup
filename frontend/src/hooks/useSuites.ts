import useSWR, { mutate } from 'swr'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { suitesService } from '@/services/suitesService'

export function useSuites() {
  return useProjectScopedSWR(
    'suites',
    (projectId) => suitesService.list(projectId),
    { refreshInterval: 60_000 },
  )
}

export function useSuite(suiteId?: string) {
  return useSWR(
    suiteId ? ['suite', suiteId] : null,
    () => suitesService.get(suiteId!),
  )
}

export function useSuiteTestCases(suiteId?: string) {
  return useSWR(
    suiteId ? ['suite', suiteId, 'cases'] : null,
    () => suitesService.listSuiteCases(suiteId!),
    { refreshInterval: 60_000 },
  )
}

export function useCanonicalRuns(canonicalId?: string) {
  return useSWR(
    canonicalId ? ['canonical', canonicalId, 'runs'] : null,
    () => suitesService.listCanonicalRuns(canonicalId!),
  )
}

/** Invalidate every suite-keyed SWR entry — call after any mutation. */
export function refreshSuites() {
  return mutate(
    (key: unknown) =>
      Array.isArray(key) && (key[0] === 'suites' || key[0] === 'suite'),
  )
}
