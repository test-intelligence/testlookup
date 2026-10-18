/**
 * W28 test-case editing (TCM-01 edit, TCM-03 bulk, TCM-04 restore).
 *
 * A module of its own, not an addition to `testManagementService`: that module
 * is mocked by hand in many test files, where a function added to it would be
 * `undefined` (W09's lesson). Every call opts out of the global error toast
 * (`suppressToast`): each caller shows its failure in the page, where its
 * action lives — one inline sentence, never an inline sentence plus a toast.
 */
import type {
  ManagedTestCase,
  ManagedTestCaseUpdateBody,
  TestCaseBulkRequest,
  TestCaseBulkResponse,
  TestCaseRestoreRequest,
  TestCaseVersion,
} from '@/types/test-management'
import { getData, patchData, postData } from './http'

export const testCaseEditingService = {
  /** The in-page edit form's Save: expected_version + only the changed fields. */
  updateCase: (id: string, body: ManagedTestCaseUpdateBody): Promise<ManagedTestCase> =>
    patchData<ManagedTestCase, ManagedTestCaseUpdateBody>(`/api/v1/test-management/cases/${id}`, body, { suppressToast: true }),

  /** The 409 re-read; also the "Reload" of a stale restore. */
  getCaseFresh: (id: string): Promise<ManagedTestCase> =>
    getData<ManagedTestCase>(`/api/v1/test-management/cases/${id}`, { suppressToast: true }),

  getCaseHistoryFresh: (id: string): Promise<TestCaseVersion[]> =>
    getData<TestCaseVersion[]>(`/api/v1/test-management/cases/${id}/history`, { suppressToast: true }),

  bulkUpdate: (body: TestCaseBulkRequest): Promise<TestCaseBulkResponse> =>
    postData<TestCaseBulkResponse, TestCaseBulkRequest>('/api/v1/test-management/cases/bulk', body, { suppressToast: true }),

  restoreVersion: (id: string, body: TestCaseRestoreRequest): Promise<ManagedTestCase> =>
    postData<ManagedTestCase, TestCaseRestoreRequest>(`/api/v1/test-management/cases/${id}/restore`, body, { suppressToast: true }),
}

interface ErrorShape {
  response?: { status?: number; data?: { detail?: unknown } }
}

function detailOf(error: unknown): unknown {
  return (error as ErrorShape | null | undefined)?.response?.data?.detail
}

/**
 * A stale-version 409 (the PATCH's or a restore's body
 * `{ current_version, expected_version, message }`): the version the server
 * holds now. Anything else, null.
 */
export function conflictOf(error: unknown): { currentVersion: number } | null {
  const status = (error as ErrorShape | null | undefined)?.response?.status
  if (status !== 409) return null
  const detail = detailOf(error)
  if (!detail || typeof detail !== 'object') return null
  const current = (detail as { current_version?: unknown }).current_version
  return typeof current === 'number' && Number.isFinite(current) ? { currentVersion: current } : null
}

/** The server's sentence (`detail`, or `detail.message`), else the caller's fallback. */
export function errorSentence(error: unknown, fallback: string): string {
  const detail = detailOf(error)
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
  }
  return fallback
}
