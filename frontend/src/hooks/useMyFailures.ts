import useSWR from 'swr'
import { useAuthStore } from '@/store/authStore'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { myFailuresService } from '@/services/myFailuresService'
import { useReleaseScope } from './useReleaseScope'

/**
 * Paginated list of the caller's auto-assigned failures.
 * Project-scoped via the active project store: ALL_PROJECTS_ID fans out
 * across every accessible project; a specific UUID narrows. Polls every
 * 30s (POLLING tier) so new ingests appear without a manual refresh.
 */
export function useMyFailures(params: { days?: number; page?: number; size?: number; scope?: 'mine' | 'team' }) {
  // `/me/assigned-failures` has accepted `release_id` since the epic wired it,
  // and nothing was sending it — the picker sat in the header changing nothing
  // on this page. An inert filter is worse than an absent one: it reads as
  // "these are 2.4.0's failures" when they are the project's.
  const releaseId = useReleaseScope()
  return useProjectScopedSWR(
    'my-failures',
    (projectId) =>
      myFailuresService.list({ project_id: projectId, release_id: releaseId, ...params }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    // In the deps as well as the params, or switching releases serves the
    // previous release's page from cache under the new release's name.
    [params.days, params.page, params.size, params.scope, releaseId],
  )
}

/**
 * Sidebar badge count. Independent SWR from the list so the badge stays
 * fresh even when the inbox page is closed. Same project scope as the
 * page — when the user switches project, the badge re-keys automatically.
 *
 * Note: the sidebar badge is intentionally scoped to "mine" (the default
 * server-side too). Otherwise an admin viewing a project would see every
 * unresolved failure across the team in their personal badge, which
 * isn't the at-a-glance "what's on my plate" signal the badge is for.
 */
export function useMyFailuresCount(params: { days?: number } = {}) {
  // Scoped with the list, not independently. The count endpoint's own comment
  // says a disagreement means "the badge advertises work the page cannot
  // show", and a release-scoped page beside a project-wide badge is exactly
  // that.
  const releaseId = useReleaseScope()
  return useProjectScopedSWR(
    'my-failures-count',
    (projectId) =>
      myFailuresService.count({ project_id: projectId, days: params.days, release_id: releaseId }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [params.days, releaseId],
  )
}

/**
 * Reassignment picker payload (suite owner + QA Engineers) for a single
 * failure. Lazily keyed on the test-case id so opening the Reassign modal
 * for a different row re-fetches; a `null` id skips the request entirely.
 * Fetched once per open (no focus revalidation) and surfaces a failed load
 * immediately (no retry) so the modal can show the permission/empty message
 * the old load-on-mount effect raised.
 */
export function useReassignOptions(testCaseId: string | null) {
  return useSWR(
    testCaseId ? ['reassign-options', testCaseId] : null,
    () => myFailuresService.getReassignOptions(testCaseId as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}

/**
 * Non-project-scoped variant for the top-level "all my work" sidebar badge.
 *
 * Deliberately NOT release-scoped, unlike its project-scoped sibling above. A
 * release belongs to one project, so filtering a badge that spans every project
 * by one project's release would answer a question nobody asked — and
 * `useReleaseScope` returns null without a single pinned project anyway.
 */
export function useMyFailuresCountUnscoped(params: { days?: number } = {}) {
  const userId = useAuthStore((state) => state.user?.id ?? null)
  return useSWR(
    userId ? ['my-failures-count-unscoped', userId, params.days] : null,
    () => myFailuresService.count({ days: params.days }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
  )
}
