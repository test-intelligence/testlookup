import { deleteData, getData, patchData, postData } from './http'
import type {
  CanonicalRunHistoryResponse,
  CanonicalTestCase,
  CanonicalTestCaseBulkLinkResponse,
  CanonicalTestCaseListResponse,
  CanonicalPromotionResponse,
  TestSuite,
  TestSuiteCreatePayload,
  TestSuiteListResponse,
  TestSuiteUpdatePayload,
} from '@/types/suites'

export const suitesService = {
  list: (projectId: string | null) =>
    getData<TestSuiteListResponse>('/api/v1/suites', {
      params: { ...(projectId ? { project_id: projectId } : {}) },
    }),

  get: (suiteId: string) =>
    getData<TestSuite>(`/api/v1/suites/${suiteId}`),

  create: (payload: TestSuiteCreatePayload) =>
    postData<TestSuite>('/api/v1/suites', payload),

  update: (suiteId: string, payload: TestSuiteUpdatePayload) =>
    patchData<TestSuite>(`/api/v1/suites/${suiteId}`, payload),

  remove: (suiteId: string) =>
    deleteData(`/api/v1/suites/${suiteId}`),

  setDefault: (suiteId: string) =>
    postData<TestSuite>(`/api/v1/suites/${suiteId}/set-default`),

  listSuiteCases: (suiteId: string) =>
    getData<CanonicalTestCaseListResponse>(
      `/api/v1/suites/${suiteId}/test-cases`,
    ),

  listCanonicalCases: (
    projectId: string | null,
    params?: { suite_id?: string; status?: string },
  ) =>
    getData<CanonicalTestCaseListResponse>('/api/v1/canonical-test-cases', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        ...(params || {}),
      },
    }),

  getCanonicalCase: (canonicalId: string) =>
    getData<CanonicalTestCase>(
      `/api/v1/canonical-test-cases/${canonicalId}`,
    ),

  linkCanonicalToSuite: (canonicalId: string, suiteId: string) =>
    postData<CanonicalTestCase>(
      `/api/v1/canonical-test-cases/${canonicalId}/link`,
      { test_suite_id: suiteId },
    ),

  bulkLinkCanonicals: (targetSuiteId: string, canonicalIds: string[]) =>
    postData<CanonicalTestCaseBulkLinkResponse>(
      '/api/v1/canonical-test-cases/bulk-link',
      { target_test_suite_id: targetSuiteId, canonical_ids: canonicalIds },
    ),

  listCanonicalRuns: (canonicalId: string) =>
    getData<CanonicalRunHistoryResponse>(
      `/api/v1/canonical-test-cases/${canonicalId}/runs`,
    ),

  promoteCanonical: (canonicalId: string) =>
    postData<CanonicalPromotionResponse>(
      `/api/v1/canonical-test-cases/${canonicalId}/promote`,
      {},
    ),

  unlinkManagedCase: (canonicalId: string, reason: string) =>
    deleteData<CanonicalTestCase, { reason: string }>(
      `/api/v1/canonical-test-cases/${canonicalId}/managed-link`,
      undefined,
      { reason },
    ),

  confirmRetirement: (canonicalId: string, reason: string) =>
    postData<CanonicalTestCase, { reason: string }>(
      `/api/v1/canonical-test-cases/${canonicalId}/confirm-retirement`,
      { reason },
    ),

  listOrphanedCanonicals: (projectId: string | null) =>
    getData<CanonicalTestCaseListResponse>('/api/v1/canonical-test-cases/orphaned', {
      params: { ...(projectId ? { project_id: projectId } : {}) },
    }),
}
