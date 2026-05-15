import { getData } from './http'
import type { MyFailureListResponse } from '@/types/myFailures'

export const myFailuresService = {
  /**
   * List the caller's auto-assigned failures (FAILED/BROKEN TestCase rows).
   * ``project_id`` is optional; the backend honours ``"all"`` and missing as
   * "no project filter." Default time window is 30 days (server-side default).
   */
  list: (params: { project_id?: string | null; days?: number; page?: number; size?: number }) =>
    getData<MyFailureListResponse>('/api/v1/me/assigned-failures', {
      params: {
        // null → omit so the server runs the unscoped path; sending the
        // string "null" would 400 on a stricter validator.
        ...(params.project_id ? { project_id: params.project_id } : {}),
        ...(params.days != null ? { days: params.days } : {}),
        ...(params.page != null ? { page: params.page } : {}),
        ...(params.size != null ? { size: params.size } : {}),
      },
    }),

  /** Lightweight count for the sidebar badge — no row hydration. */
  count: (params: { project_id?: string | null; days?: number }) =>
    getData<{ count: number }>('/api/v1/me/assigned-failures/count', {
      params: {
        ...(params.project_id ? { project_id: params.project_id } : {}),
        ...(params.days != null ? { days: params.days } : {}),
      },
    }),
}
