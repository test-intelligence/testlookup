/**
 * W23a FAIL-11: the cross-run insights of ONE project (the Overview card and Inbox › Insights).
 *
 * Keyed by the request URL (`insightsKey`); no key — no request — without a single project (All Projects
 * asks nothing). The answer goes through `parseInsightList`: a malformed one comes back as `data: null`
 * WITH an `error`, so the reader says "Could not load the insights." and never crashes the page.
 *
 * No polling: insights change hourly (the beat), and SWR revalidates on focus. After a dismiss, call
 * `refreshInsights(projectId)` to revalidate every insights key of the project (both ranges, with and
 * without the dismissed rows).
 */
import { useMemo } from 'react'
import useSWR from 'swr'
import { listInsights } from '@/services/insightsService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { appMutate } from '@/utils/swrCacheMutate'
import { parseInsightList, type InsightList, type InsightRange } from '@/types/insights'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_INSIGHTS = 'The insights answer could not be read.'

function singleProject(projectId: string | null | undefined): string | null {
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

/**
 * The SWR key (= the request URL) of one insights read, or `null` (no request) without a single project.
 * `limit` is appended only when given (the Inbox tab asks 100; the card takes the server default).
 */
export function insightsKey(
  projectId: string | null | undefined,
  range: InsightRange,
  includeDismissed = false,
  limit?: number,
): string | null {
  const id = singleProject(projectId)
  if (!id) return null
  const base = `/api/v1/projects/${id}/insights?range=${range}&include_dismissed=${includeDismissed}`
  return limit === undefined ? base : `${base}&limit=${limit}`
}

export interface InsightsState {
  /** `null` while loading, on a failed read, or for a malformed answer. */
  data: InsightList | null
  /** Set on a failed read AND on a malformed answer. */
  error: unknown
  isLoading: boolean
  mutate: () => Promise<unknown>
}

export function useInsights(
  projectId: string | null,
  range: InsightRange,
  includeDismissed = false,
  limit?: number,
): InsightsState {
  const id = singleProject(projectId)
  const key = insightsKey(id, range, includeDismissed, limit)
  const swr = useSWR<unknown>(key, id ? () => listInsights(id, range, includeDismissed, limit) : null)
  const raw = swr.data
  const parsed = useMemo(() => (raw === undefined ? null : parseInsightList(raw)), [raw])
  const malformed = raw !== undefined && parsed === null
  return {
    data: parsed,
    error: swr.error ?? (malformed ? new Error(MALFORMED_INSIGHTS) : undefined),
    isLoading: Boolean(swr.isLoading),
    mutate: () => swr.mutate(),
  }
}

/** Revalidate every insights key of one project (`appMutate`: the app's SWR cache lives in its provider). */
export function refreshInsights(projectId: string | null): Promise<unknown> {
  const id = singleProject(projectId)
  if (!id) return Promise.resolve(undefined)
  const prefix = `/api/v1/projects/${id}/insights?`
  return appMutate((key: unknown) => typeof key === 'string' && key.startsWith(prefix))
}
