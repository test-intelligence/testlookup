import type {
  CoverageResponse,
  DefectIntakePayload,
  DefectIntakeResponse,
  DefectEscapeRateResponse,
  DefectResponse,
  DefectTrendResponse,
  FailureCategoriesResponse,
  FlakyTestItem,
  KindEvidenceResponse,
  SuiteDetailResponse,
  TimeToFixResponse,
  TopFailingItem,
} from '@/types/analytics'
import { getData, postData } from './http'
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'

function projectParam(projectId: string | null): Record<string, string> {
  return projectId ? { project_id: projectId } : {}
}

/** Release scoping, OMITTED when absent rather than sent as null or empty.
 *
 *  NFR1: a caller that asks for no release must produce the request it produced
 *  before the release axis existed — same params, same cache key, same SQL. The
 *  backend mirrors this exactly (`_add_release_param` appends nothing rather
 *  than emitting an `IS NULL OR` predicate that would defeat the index). Both
 *  halves have to agree or the guarantee is only half true.
 *
 *  VIZ-303 (contract C1): one release stays the scalar `release_id=R1`;
 *  several go out as a repeated `release_id` (see `lib/scopeParams`). */
function releaseParam(releaseId: ScopeValue): Record<string, string | string[]> {
  return scopeParam('release_id', releaseId)
}

export const analyticsService = {
  getFlakyTests: (
    projectId: string | null,
    days = 30,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    // `total` counts every flaky test; `items` is the top of the list.
    getData<{ items: FlakyTestItem[]; total?: number }>('/api/v1/analytics/flaky-tests', {
      params: {
        ...projectParam(projectId),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),

  getFailureCategories: (
    projectId: string | null,
    days = 30,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    getData<FailureCategoriesResponse>('/api/v1/analytics/failure-categories', {
      params: {
        ...projectParam(projectId),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),

  getTopFailing: (
    projectId: string | null,
    days = 30,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    // `total` counts every failing test, `repeat_total` those failing twice or
    // more; `items` is the top of the list.
    getData<{ items: TopFailingItem[]; total?: number; repeat_total?: number }>('/api/v1/analytics/top-failing', {
      params: {
        ...projectParam(projectId),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),

  /**
   * FAIL-02: the median time from a test's first failure to its next pass over
   * the window (`days` 1-90 on the server). `measured: false` below the floor
   * of fixes; the hours are then null, never 0.
   */
  getTimeToFix: (
    projectId: string | null,
    { days, releaseIds, suiteName }: { days: number; releaseIds?: ScopeValue; suiteName?: ScopeValue },
  ) =>
    getData<TimeToFixResponse>('/api/v1/analytics/time-to-fix', {
      params: {
        ...projectParam(projectId),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseIds),
      },
    }),

  getCoverage: (
    projectId: string | null,
    days = 30,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    getData<CoverageResponse>('/api/v1/analytics/coverage', {
      params: {
        ...projectParam(projectId),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),

  getDefects: (projectId: string | null, params?: Record<string, unknown>) =>
    getData<DefectResponse>('/api/v1/analytics/defects', {
      params: { ...projectParam(projectId), ...params },
    }),

  /**
   * DEF-04: one point per UTC day of the window, plus totals over EVERY defect
   * in scope (the tiles' values; the list is capped). `releaseMatch` is sent
   * only with a release, and only when given (the default is the server's).
   */
  getDefectTrend: (
    projectId: string | null,
    {
      days,
      releaseIds,
      releaseMatch,
      suiteName,
    }: { days: number; releaseIds?: readonly string[]; releaseMatch?: 'found_in' | 'affects'; suiteName?: string | null },
  ) =>
    getData<DefectTrendResponse>('/api/v1/analytics/defects/trend', {
      params: {
        ...projectParam(projectId),
        days,
        ...releaseParam(releaseIds),
        ...(releaseMatch && releaseIds && releaseIds.length > 0 ? { release_match: releaseMatch } : {}),
        ...scopeParam('suite_name', suiteName),
      },
    }),

  /** DEF-03: the escape rate is counted per release, so it takes no suite. */
  getDefectEscapeRate: (
    projectId: string | null,
    { days, releaseIds }: { days: number; releaseIds?: readonly string[] },
  ) =>
    getData<DefectEscapeRateResponse>('/api/v1/analytics/defects/escape-rate', {
      params: { ...projectParam(projectId), days, ...releaseParam(releaseIds) },
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
    releaseId?: ScopeValue,
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
