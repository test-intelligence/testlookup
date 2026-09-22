import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { myFailuresService } from '@/services/myFailuresService'
import { useReleaseScope } from './useReleaseScope'
import { keyPart, scopeArg } from '@/lib/scopeParams'
import { useSuiteScope } from './useSuiteScope'
// Scoped reads opt into superseded-scope aborting (services/scopeAbort.ts).
import { scopedFetch } from '@/services/scopeAbort'

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
  //
  // Both axes repeat on the endpoint (VIZ-303 / E3): every selected release
  // and every globally selected suite is sent — none → no parameter, one →
  // the legacy scalar, several → a repeated key. The suite scope is `null`
  // with the multi-filter flag off, so a flag-off request is unchanged.
  const release = scopeArg(useReleaseScope())
  const suites = scopeArg(useSuiteScope())
  return useProjectScopedSWR(
    'my-failures',
    (projectId) =>
      scopedFetch(() => myFailuresService.list({ project_id: projectId, release_id: release, suite_name: suites, ...params })),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    // In the deps as well as the params, or switching releases serves the
    // previous release's page from cache under the new release's name. Lists
    // as their joined strings: never an array rebuilt per render.
    [params.days, params.page, params.size, params.scope, keyPart(release), ...suiteDeps(suites)],
  )
}

/** The suite axis in a dep list: nothing at all when none is selected, so a
 *  flag-off key is byte-identical to the key before the axis existed. */
function suiteDeps(suites: string | string[] | null): string[] {
  const key = keyPart(suites)
  return key ? [`suites:${key}`] : []
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
  // that. Same axes, same wire rule as the list.
  const release = scopeArg(useReleaseScope())
  const suites = scopeArg(useSuiteScope())
  return useProjectScopedSWR(
    'my-failures-count',
    (projectId) =>
      scopedFetch(() => myFailuresService.count({ project_id: projectId, days: params.days, release_id: release, suite_name: suites })),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [params.days, keyPart(release), ...suiteDeps(suites)],
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

// The sidebar badge's unscoped count lives in its own module so the eager
// Sidebar does not pull this module's scope machinery onto the critical path.
export { useMyFailuresCountUnscoped } from './useMyFailuresCountUnscoped'
