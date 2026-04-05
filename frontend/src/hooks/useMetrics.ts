import useSWR from 'swr'
import { metricsService } from '@/services/metricsService'
import { analyticsService } from '@/services/analyticsService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'

export function useDashboardSummary(days = 7) {
  return useProjectScopedSWR(
    'metrics-summary',
    (projectId) => metricsService.getSummary(projectId, days),
    { refreshInterval: 30_000 },
    [days],
  )
}

export function useTrendData(days = 7) {
  return useProjectScopedSWR(
    'metrics-trends',
    (projectId) => metricsService.getTrends(projectId, days),
    { refreshInterval: 60_000 },
    [days],
  )
}

export function useFlakyTests(days = 30) {
  return useProjectScopedSWR(
    'analytics-flaky',
    (projectId) => analyticsService.getFlakyTests(projectId, days),
    { refreshInterval: 120_000 },
    [days],
  )
}

export function useFailureCategories(days = 30) {
  return useProjectScopedSWR(
    'analytics-categories',
    (projectId) => analyticsService.getFailureCategories(projectId, days),
    { refreshInterval: 120_000 },
    [days],
  )
}

export function useTopFailing(days = 30) {
  return useProjectScopedSWR(
    'analytics-top-failing',
    (projectId) => analyticsService.getTopFailing(projectId, days),
    { refreshInterval: 120_000 },
    [days],
  )
}

export function useCoverage(days = 30) {
  return useProjectScopedSWR(
    'analytics-coverage',
    (projectId) => analyticsService.getCoverage(projectId, days),
    { refreshInterval: 120_000 },
    [days],
  )
}

export function useDefects(page = 1, resolutionStatus?: string) {
  return useProjectScopedSWR(
    'analytics-defects',
    (projectId) => analyticsService.getDefects(projectId, { page, resolution_status: resolutionStatus }),
    { refreshInterval: 60_000 },
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
    { refreshInterval: 120_000 },
    [days],
  )
}
