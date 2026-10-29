import { getData, postData } from './http'
import type { Insight, InsightRange } from '@/types/insights'

/**
 * W23a FAIL-11: cross-run insights of one project.
 *
 * - list: any member of the project (a project-bound key too) — reads only; the answer is the RAW
 *   `InsightListResponse`, read through `parseInsightList` by `hooks/useInsights`;
 * - dismiss: QA engineer and above; project-wide (hidden for everyone for the rest of its episode).
 *
 * Every request path is written out in full in its call (the SPA wire-contract guard reads them). The
 * write passes `suppressToast: true`: the caller shows `extractErrorMessage(err)` inline, where the
 * refusal happened (`detail: {code, message}` with one of `INSIGHT_ERRORS`).
 */

export function listInsights(
  projectId: string,
  range: InsightRange,
  includeDismissed: boolean,
  limit?: number,
): Promise<unknown> {
  const params: Record<string, string | number | boolean> = { range, include_dismissed: includeDismissed }
  if (limit !== undefined) params.limit = limit
  return getData<unknown>(`/api/v1/projects/${projectId}/insights`, { params })
}

export function dismissInsight(projectId: string, insightId: string): Promise<Insight> {
  return postData<Insight>(`/api/v1/projects/${projectId}/insights/${insightId}/dismiss`, undefined, {
    suppressToast: true,
  })
}
