import { deleteData, getData, patchData, postData } from './http'
import type {
  CanonicalRunHistoryResponse,
  CanonicalTestCase,
  CanonicalTestCaseListResponse,
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

  listCanonicalRuns: (canonicalId: string) =>
    getData<CanonicalRunHistoryResponse>(
      `/api/v1/canonical-test-cases/${canonicalId}/runs`,
    ),
}
