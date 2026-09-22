import { getData, putData } from './http'
import type { MyFailureItem, MyFailureListResponse, TriageStatus } from '@/types/myFailures'
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'

export interface ReassignmentOption {
  user_id: string
  email: string
  username: string
  full_name: string | null
}

export interface ReassignmentOptions {
  project_id: string
  suite_name: string | null
  suite_owner: ReassignmentOption | null
  qa_engineers: ReassignmentOption[]
}

export const myFailuresService = {
  /**
   * List the caller's auto-assigned failures (FAILED/BROKEN TestCase rows).
   * ``project_id`` is optional; the backend honours ``"all"`` and missing as
   * "no project filter." Default time window is 30 days (server-side default).
   */
  list: (params: {
    project_id?: string | null
    days?: number
    page?: number
    size?: number
    scope?: 'mine' | 'team'
    release_id?: ScopeValue
    suite_name?: ScopeValue
  }) =>
    getData<MyFailureListResponse>('/api/v1/me/assigned-failures', {
      params: {
        // null → omit so the server runs the unscoped path; sending the
        // string "null" would 400 on a stricter validator.
        ...(params.project_id ? { project_id: params.project_id } : {}),
        ...(params.days != null ? { days: params.days } : {}),
        ...(params.page != null ? { page: params.page } : {}),
        ...(params.size != null ? { size: params.size } : {}),
        ...(params.scope ? { scope: params.scope } : {}),
        // Omitted entirely when absent, like every param above: the backend's
        // release fragment is conditional so the SQL stays byte-identical for
        // callers that send none. One value is the legacy scalar; several go
        // out as a repeated key (C1 wire rule, `lib/scopeParams`).
        ...scopeParam('release_id', params.release_id),
        ...scopeParam('suite_name', params.suite_name),
      },
    }),

  /** Lightweight count for the sidebar badge — no row hydration. */
  count: (params: {
    project_id?: string | null
    days?: number
    scope?: 'mine' | 'team'
    release_id?: ScopeValue
    suite_name?: ScopeValue
  }) =>
    getData<{ count: number }>('/api/v1/me/assigned-failures/count', {
      params: {
        ...(params.project_id ? { project_id: params.project_id } : {}),
        ...(params.days != null ? { days: params.days } : {}),
        ...(params.scope ? { scope: params.scope } : {}),
        ...scopeParam('release_id', params.release_id),
        ...scopeParam('suite_name', params.suite_name),
      },
    }),

  /**
   * Fetch the reassignment picker payload (suite owner + QA Engineers).
   * Returns 403 if the caller isn't QA_LEAD or ADMIN on the project —
   * surface that as a "you don't have permission" message; the modal
   * should never have been opened in that case.
   */
  getReassignOptions: (testCaseId: string) =>
    getData<ReassignmentOptions>(
      `/api/v1/me/assigned-failures/${testCaseId}/reassign-options`,
    ),

  /**
   * Move a FAILED/BROKEN test case to a new assignee. The backend
   * validates that the new assignee is either the suite owner or a
   * QA_ENGINEER member of the project.
   */
  reassign: (testCaseId: string, newAssigneeUserId: string) =>
    putData<MyFailureItem, { new_assignee_user_id: string }>(
      `/api/v1/me/assigned-failures/${testCaseId}/reassign`,
      { new_assignee_user_id: newAssigneeUserId },
    ),

  /**
   * Update the triage status of a failure. Any value other than
   * ``PENDING_REVIEW`` removes the row from the assignee's inbox.
   * Authorisation: the assignee themselves OR QA_LEAD/ADMIN on the
   * project.
   */
  updateTriageStatus: (
    testCaseId: string,
    payload: { status: TriageStatus; notes?: string | null },
  ) =>
    putData<MyFailureItem, { status: TriageStatus; notes?: string | null }>(
      `/api/v1/me/assigned-failures/${testCaseId}/triage`,
      payload,
    ),
}
