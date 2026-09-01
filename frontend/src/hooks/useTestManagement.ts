import useSWR from 'swr'
import { testManagementService, usersService } from '@/services/testManagementService'
import type { UserSummary } from '@/services/testManagementService'
import type {
  DuplicateBand,
  DuplicateCandidateStatus,
  TestCaseEvidenceGapKind,
} from '@/types/test-management'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useTestCases(params?: Record<string, unknown>) {
  return useProjectScopedSWR(
    'tm-cases',
    (projectId) => testManagementService.listCases(projectId, params),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [params],
  )
}

export function useTestCase(id?: string) {
  return useSWR(
    id ? ['tm-case', id] : null,
    () => testManagementService.getCase(id as string),
    { refreshInterval: 0 }
  )
}

export function useTestCaseHistory(id?: string) {
  return useSWR(
    id ? ['tm-case-history', id] : null,
    () => testManagementService.getCaseHistory(id as string),
    { refreshInterval: 0 }
  )
}

export function useTestCaseReviews(id?: string) {
  return useSWR(
    id ? ['tm-case-reviews', id] : null,
    () => testManagementService.getCaseReviews(id as string),
    { refreshInterval: REFRESH_INTERVALS.POLLING }
  )
}

export function useTestCaseComments(id?: string) {
  return useSWR(
    id ? ['tm-case-comments', id] : null,
    () => testManagementService.getCaseComments(id as string),
    { refreshInterval: REFRESH_INTERVALS.POLLING }
  )
}

export function useAllowedTestCaseTransitions(id?: string) {
  return useSWR(
    id ? ['tm-case-allowed-transitions', id] : null,
    () => testManagementService.getAllowedTransitions(id as string),
    { refreshInterval: 0, shouldRetryOnError: false },
  )
}

export function useTestCaseEvidenceGaps(kind: TestCaseEvidenceGapKind) {
  return useProjectScopedSWR(
    'tm-case-evidence-gaps',
    (projectId) => testManagementService.getEvidenceGaps(projectId, kind),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [kind],
  )
}

export function useTestPlans(params?: Record<string, unknown>) {
  return useProjectScopedSWR(
    'tm-plans',
    (projectId) => testManagementService.listPlans(projectId, params),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [params],
  )
}

export function usePlan(id?: string) {
  return useSWR(
    id ? ['tm-plan', id] : null,
    () => testManagementService.getPlan(id as string),
    { refreshInterval: 0 }
  )
}

export function usePlanItems(planId?: string) {
  return useSWR(
    planId ? ['tm-plan-items', planId] : null,
    () => testManagementService.getPlanItems(planId as string),
    { refreshInterval: REFRESH_INTERVALS.POLLING }
  )
}

export function useStrategies() {
  return useProjectScopedSWR(
    'tm-strategies',
    (projectId) => testManagementService.listStrategies(projectId),
    { refreshInterval: REFRESH_INTERVALS.POLLING }
  )
}

export function useStrategy(id?: string) {
  return useSWR(
    id ? ['tm-strategy', id] : null,
    () => testManagementService.getStrategy(id as string),
    { refreshInterval: 0 }
  )
}

export function useUsers() {
  return useSWR<UserSummary[]>('users', () => usersService.listUsers(), {
    refreshInterval: 0,
    revalidateOnFocus: false,
  })
}

/**
 * Duplicate-candidate review queue for one project (Phase 4).
 *
 * Lazy: pass ``undefined`` (e.g. All-Projects mode or no selection) and the
 * hook short-circuits — no request fires until a concrete project id is given.
 * Reads only; mutations (detect/dismiss/merge) go through the service and the
 * caller revalidates via the returned ``mutate``.
 */
export function useDuplicateCandidates(
  projectId?: string,
  params?: { band?: DuplicateBand; status?: DuplicateCandidateStatus; page?: number; size?: number },
) {
  return useSWR(
    projectId ? ['tm-duplicates', projectId, params] : null,
    () => testManagementService.getDuplicateCandidates(projectId as string, params),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}

export function useAuditLog(params?: { entity_type?: string; action?: string; page?: number; size?: number }) {
  const projectId = useActiveProjectId()
  const fetchProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  return useSWR(
    projectId !== null ? ['tm-audit', projectId, params] : null,
    () => testManagementService.getAuditLog(fetchProjectId, params),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND }
  )
}
