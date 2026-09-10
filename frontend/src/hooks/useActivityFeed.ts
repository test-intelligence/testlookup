import { useCallback, useMemo } from 'react'
import useSWR from 'swr'
import useSWRInfinite from 'swr/infinite'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import {
  type ActivityEventDetail,
  type ActivityEventTypes,
  type ActivityPage,
  type ActivityQuery,
  getActivityEvent,
  listActivity,
  listActivityEventTypes,
} from '@/services/activityService'

/**
 * SWR hooks for the activity ledger.
 *
 * `useSWRInfinite` rather than `useSWR` because the feed pages by cursor: each
 * page's key depends on the PREVIOUS page's `next_cursor`, which is exactly the
 * shape SWRInfinite exists for. Plain `useSWR` with a page number would have to
 * re-request from the top on every extension.
 */

const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

export function useActivityEventTypes() {
  const { data, error, isLoading } = useSWR<ActivityEventTypes>(
    ['activity-event-types'],
    listActivityEventTypes,
    // The registry only changes on deploy — no point re-fetching it.
    { ...OPTS, revalidateIfStale: false },
  )
  return { eventTypes: data, error, isLoading }
}

export function useActivityFeed(
  projectId: string | null,
  query: ActivityQuery & { days?: number },
  enabled = true,
) {
  // Serialised so the key is stable across renders that rebuild the object.
  const queryKey = useMemo(() => JSON.stringify(query), [query])

  const getKey = useCallback(
    (pageIndex: number, previous: ActivityPage | null) => {
      if (!enabled || !projectId) return null
      // A previous page that came back with no cursor is the end of the
      // ledger; returning null here is what stops SWRInfinite asking again.
      if (pageIndex > 0 && !previous?.next_cursor) return null
      return [
        'activity-feed',
        projectId,
        queryKey,
        pageIndex === 0 ? null : previous?.next_cursor,
      ] as const
    },
    [enabled, projectId, queryKey],
  )

  const { data, error, isLoading, isValidating, size, setSize, mutate } =
    useSWRInfinite<ActivityPage>(
      getKey,
      ([, id, rawQuery, cursor]) => {
        const parsed = JSON.parse(rawQuery as string) as ActivityQuery & {
          days?: number
        }
        const { days, ...rest } = parsed
        // `since` is resolved HERE, at fetch time, not during render. Calling
        // Date.now() in a render-phase useMemo is impure (the react-hooks
        // purity rule rejects it), and it is also wrong: a page left open for
        // hours would keep asking for a window anchored to its first render,
        // so newly-arrived events would silently fall outside it.
        return listActivity(id as string, {
          ...rest,
          ...(days
            ? { since: new Date(Date.now() - days * 86_400_000).toISOString() }
            : {}),
          ...(cursor ? { cursor: cursor as string } : {}),
        })
      },
      { ...OPTS, refreshInterval: REFRESH_INTERVALS.POLLING, revalidateFirstPage: true },
    )

  // Memoised on `data` rather than on a `data ?? []` expression: the fallback
  // builds a NEW array every render, so the dependency would never compare
  // equal and every consumer would re-render on every tick.
  const pages = useMemo(() => data ?? [], [data])
  const events = useMemo(() => pages.flatMap(p => p.items), [pages])
  const lastPage = pages[pages.length - 1]

  return {
    events,
    /** Null until the first page lands; null AFTER it lands means an empty ledger. */
    ledgerStartedAt: pages[0]?.ledger_started_at ?? null,
    hasMore: Boolean(lastPage?.next_cursor),
    loadMore: () => setSize(size + 1),
    isLoading,
    isValidating,
    /**
     * Distinguishes "this project has no activity" from "we could not load
     * it". Absence is not health: rendering an outage as an empty feed is how
     * a broken backend gets read as a quiet project.
     */
    error,
    size,
    refresh: mutate,
  }
}

export function useActivityEvent(
  projectId: string | null,
  eventId: string | null,
) {
  const { data, error, isLoading } = useSWR<ActivityEventDetail>(
    projectId && eventId ? ['activity-event', projectId, eventId] : null,
    ([, pid, eid]) => getActivityEvent(pid as string, eid as string),
    OPTS,
  )
  return { event: data, error, isLoading }
}
