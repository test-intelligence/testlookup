import { useCallback, useMemo } from 'react'
import useSWRInfinite from 'swr/infinite'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import {
  type MyActivityItem,
  type MyActivityPage,
  listMyActivity,
} from '@/services/activityService'
import type { MineView } from '@/types/activityLive'

/**
 * Inbox › Activity (W21d ACT-02): the caller's own rows and the rows that
 * involve them, across their projects, paged by the server's cursor.
 *
 * The key is `null` while `enabled` is false, so the Inbox issues NO request
 * until its Activity tab is open. `projectId` pins the read to one project (the
 * Inbox's active project); null / undefined reads every reachable project and
 * sends no `project_id` at all.
 */

const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

export interface MyActivityOptions {
  view: MineView
  days: number
  projectId?: string | null
}

export function useMyActivity({ view, days, projectId = null }: MyActivityOptions, enabled = true) {
  const getKey = useCallback(
    (pageIndex: number, previous: MyActivityPage | null) => {
      if (!enabled) return null
      // A previous page without a cursor is the end; null stops SWRInfinite.
      if (pageIndex > 0 && !previous?.next_cursor) return null
      return [
        'activity-mine',
        view,
        days,
        projectId,
        pageIndex === 0 ? null : (previous?.next_cursor ?? null),
      ] as const
    },
    [enabled, view, days, projectId],
  )

  const { data, error, isLoading, isValidating, size, setSize, mutate } =
    useSWRInfinite<MyActivityPage>(
      getKey,
      ([, keyView, keyDays, keyProject, cursor]) =>
        listMyActivity({
          view: keyView as MineView,
          // `since` is resolved HERE, at fetch time (the useActivityFeed
          // rule): a render-phase Date.now() is impure, and a tab left open
          // for hours would keep a window anchored to its first render.
          since: new Date(Date.now() - (keyDays as number) * 86_400_000).toISOString(),
          ...(keyProject ? { project_id: keyProject as string } : {}),
          ...(cursor ? { cursor: cursor as string } : {}),
        }),
      { ...OPTS, refreshInterval: REFRESH_INTERVALS.POLLING, revalidateFirstPage: true },
    )

  // Memoised on `data`: a `data ?? []` fallback is a new array every render.
  const pages = useMemo(() => data ?? [], [data])
  // A page without `items` (a malformed answer) reads as empty, never a crash.
  const items = useMemo<MyActivityItem[]>(
    () => pages.flatMap(page => (Array.isArray(page?.items) ? page.items : [])),
    [pages],
  )
  const first = pages[0]
  const lastPage = pages[pages.length - 1]

  return {
    items,
    /** The server's count of the whole window (null until the first page lands). */
    total: typeof first?.total === 'number' ? first.total : null,
    /** Projects the read covered: null = every project (admin) or not loaded; 0 = no membership. */
    projects: first ? (first.projects ?? null) : null,
    hasMore: Boolean(lastPage?.next_cursor),
    loadMore: () => setSize(size + 1),
    isLoading,
    isValidating,
    /** Absence is not health: a failed read is an error, never an empty list. */
    error,
    size,
    refresh: mutate,
  }
}
