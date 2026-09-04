import type {
  CoverageResponse,
  DefectIntakePayload,
  DefectIntakeResponse,
  DefectResponse,
  FailureCategoriesResponse,
  FlakyTestItem,
  KindEvidenceResponse,
  SuiteDetailResponse,
  TopFailingItem,
} from '@/types/analytics'
import { getData, postData } from './http'

function projectParam(projectId: string | null): Record<string, string> {
  return projectId ? { project_id: projectId } : {}
}

/** Release scoping, OMITTED when absent rather than sent as null or empty.
 *
 *  NFR1: a caller that asks for no release must produce the request it produced
 *  before the release axis existed — same params, same cache key, same SQL. The
 *  backend mirrors this exactly (`_add_release_param` appends nothing rather
 *  than emitting an `IS NULL OR` predicate that would defeat the index). Both
 *  halves have to agree or the guarantee is only half true. */
function releaseParam(releaseId: string | null | undefined): Record<string, string> {
  return releaseId ? { release_id: releaseId } : {}
}

export const analyticsService = {
  getFlakyTests: (
    projectId: string | null,
    days = 30,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<{ items: FlakyTestItem[] }>('/api/v1/analytics/flaky-tests', {
      params: {
        ...projectParam(projectId),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),

  getFailureCategories: (
    projectId: string | null,
    days = 30,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<FailureCategoriesResponse>('/api/v1/analytics/failure-categories', {
      params: {
        ...projectParam(projectId),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),

  getTopFailing: (
    projectId: string | null,
    days = 30,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<{ items: TopFailingItem[] }>('/api/v1/analytics/top-failing', {
      params: {
        ...projectParam(projectId),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),

  getCoverage: (
    projectId: string | null,
    days = 30,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<CoverageResponse>('/api/v1/analytics/coverage', {
      params: {
        ...projectParam(projectId),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),

  getDefects: (projectId: string | null, params?: Record<string, unknown>) =>
    getData<DefectResponse>('/api/v1/analytics/defects', {
      params: { ...projectParam(projectId), ...params },
    }),

  createDefect: (payload: DefectIntakePayload) =>
    postData<DefectIntakeResponse, DefectIntakePayload>('/api/v1/analytics/defects', payload),

  getAiSummary: (projectId: string | null, days = 30) =>
    getData('/api/v1/analytics/ai-summary', {
      params: { ...projectParam(projectId), days },
    }),

  getSuiteDetail: (
    projectId: string | null,
    suiteName: string,
    days = 30,
    releaseId?: string | null,
  ) =>
    getData<SuiteDetailResponse>('/api/v1/analytics/suite-detail', {
      params: {
        ...projectParam(projectId),
        suite_name: suiteName,
        days,
        ...releaseParam(releaseId),
      },
    }),

  /**
   * Evidence checklist behind an AI-classified failure kind (AI-4).
   * Lookup by test_case_id (run-detail rows) OR project_id + fingerprint
   * (/failures aggregates). Fetched lazily — only when a popover opens.
   */
  getKindEvidence: (
    lookup: { projectId?: string | null; testFingerprint?: string | null; testCaseId?: string | null },
  ) =>
    getData<KindEvidenceResponse>('/api/v1/analytics/kind-evidence', {
      params: {
        ...(lookup.testCaseId
          ? { test_case_id: lookup.testCaseId }
          : {
              ...projectParam(lookup.projectId ?? null),
              test_fingerprint: lookup.testFingerprint ?? '',
            }),
      },
    }),
}
