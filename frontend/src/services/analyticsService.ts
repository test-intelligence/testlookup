import type {
  CoverageResponse,
  DefectResponse,
  FailureCategoryItem,
  FlakyTestItem,
  SuiteDetailResponse,
  TopFailingItem,
} from '@/types/analytics'
import { getData } from './http'

function projectParam(projectId: string | null): Record<string, string> {
  return projectId ? { project_id: projectId } : {}
}

export const analyticsService = {
  getFlakyTests: (projectId: string | null, days = 30) =>
    getData<{ items: FlakyTestItem[] }>('/api/v1/analytics/flaky-tests', {
      params: { ...projectParam(projectId), days },
    }),

  getFailureCategories: (projectId: string | null, days = 30) =>
    getData<{ items: FailureCategoryItem[] }>('/api/v1/analytics/failure-categories', {
      params: { ...projectParam(projectId), days },
    }),

  getTopFailing: (projectId: string | null, days = 30) =>
    getData<{ items: TopFailingItem[] }>('/api/v1/analytics/top-failing', {
      params: { ...projectParam(projectId), days },
    }),

  getCoverage: (projectId: string | null, days = 30) =>
    getData<CoverageResponse>('/api/v1/analytics/coverage', {
      params: { ...projectParam(projectId), days },
    }),

  getDefects: (projectId: string | null, params?: Record<string, unknown>) =>
    getData<DefectResponse>('/api/v1/analytics/defects', {
      params: { ...projectParam(projectId), ...params },
    }),

  getAiSummary: (projectId: string | null, days = 30) =>
    getData('/api/v1/analytics/ai-summary', {
      params: { ...projectParam(projectId), days },
    }),

  getSuiteDetail: (projectId: string | null, suiteName: string, days = 30) =>
    getData<SuiteDetailResponse>('/api/v1/analytics/suite-detail', {
      params: { ...projectParam(projectId), suite_name: suiteName, days },
    }),
}
