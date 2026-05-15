import useSWR, { mutate } from 'swr'
import { metricsService } from '@/services/metricsService'
import { analyticsService } from '@/services/analyticsService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function refreshDefects() {
  return mutate((key: unknown) => Array.isArray(key) && key[0] === 'analytics-defects')
}

export function useDashboardSummary(days = 7, suiteName?: string | null) {
  return useProjectScopedSWR(
    'metrics-summary',
    (projectId) => metricsService.getSummary(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [days, suiteName],
  )
}

export function useTrendData(days = 7, suiteName?: string | null) {
  return useProjectScopedSWR(
    'metrics-trends',
    (projectId) => metricsService.getTrends(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, suiteName],
  )
}

export function useFlakyTests(days = 30, suiteName?: string | null) {
  return useProjectScopedSWR(
    'analytics-flaky',
    (projectId) => analyticsService.getFlakyTests(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, suiteName],
  )
}

export function useFailureCategories(days = 30, suiteName?: string | null) {
  return useProjectScopedSWR(
    'analytics-categories',
    (projectId) => analyticsService.getFailureCategories(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, suiteName],
  )
}

export function useTopFailing(days = 30, suiteName?: string | null) {
  return useProjectScopedSWR(
    'analytics-top-failing',
    (projectId) => analyticsService.getTopFailing(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, suiteName],
  )
}

export function useCoverage(days = 30, suiteName?: string | null) {
  return useProjectScopedSWR(
    'analytics-coverage',
    (projectId) => analyticsService.getCoverage(projectId, days, suiteName),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [days, suiteName],
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

export function useSuiteDetail(suiteName: string | null, days = 30) {
  const projectId = useActiveProjectId()
  const fetchProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  return useSWR(
    projectId && suiteName ? ['analytics-suite-detail', projectId, suiteName, days] : null,
    () => analyticsService.getSuiteDetail(fetchProjectId, suiteName as string, days),
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
