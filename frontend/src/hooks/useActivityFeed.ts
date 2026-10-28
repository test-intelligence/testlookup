import { useCallback, useMemo, useState } from 'react'
import useSWR from 'swr'
import useSWRInfinite from 'swr/infinite'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { prependLive } from '@/hooks/useActivityLive'
import {
  type ActivityEvent,
  type ActivityEventDetail,
  type ActivityEventTypes,
  type ActivityPage,
  type ActivityQuery,
  getActivityEvent,
  listActivity,
  listActivityEventTypes,
} from '@/services/activityService'

/** A first page, stamped with when its request STARTED (client-side only). */
type StampedPage = ActivityPage & { fetchedFrom?: number }

/** Streamed rows kept on screen at most (the server's stream holds 1,000). */
const MAX_LIVE_ROWS = 1_000

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
  /**
   * `live` (ACT-02): the page's live tail is streaming, so the 30 s polling
   * stops; any other state of the tail (connecting, reconnecting,
   * unavailable, refused) keeps polling — a missing stream is never shown as
   * a quiet project.
   */
  options: { live?: boolean } = {},
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
    useSWRInfinite<StampedPage>(
      getKey,
      async ([, id, rawQuery, cursor]) => {
        const startedAt = Date.now()
        const parsed = JSON.parse(rawQuery as string) as ActivityQuery & {
          days?: number
        }
        const { days, ...rest } = parsed
        // `since` is resolved HERE, at fetch time, not during render. Calling
        // Date.now() in a render-phase useMemo is impure (the react-hooks
        // purity rule rejects it), and it is also wrong: a page left open for
        // hours would keep asking for a window anchored to its first render,
        // so newly-arrived events would silently fall outside it.
        const page = await listActivity(id as string, {
          ...rest,
          ...(days
            ? { since: new Date(Date.now() - days * 86_400_000).toISOString() }
            : {}),
          ...(cursor ? { cursor: cursor as string } : {}),
        })
        // The first page carries when its read began: a streamed row that
        // arrived before that is in this answer (or belongs to a later page).
        return cursor || !page ? page : { ...page, fetchedFrom: startedAt }
      },
      { ...OPTS, refreshInterval: options.live ? 0 : REFRESH_INTERVALS.POLLING, revalidateFirstPage: true },
    )

  // Rows the live tail delivered, newest first, each with when it arrived.
  // They are laid over page 0 at READ time (prependLive), not written into the
  // SWR cache: SWR discards a revalidation that started before a mutate, so a
  // frame landing during a page-0 re-read or a "Load older" would have thrown
  // that answer away. A row stays on top until a first-page read that began
  // after it arrived has answered (that read holds it, or it now belongs to an
  // older page); a row already on a loaded page is never shown twice.
  const liveScope = `${projectId ?? ''}\n${queryKey}`
  const [live, setLive] = useState<{ scope: string; rows: { row: ActivityEvent; at: number }[] }>(
    () => ({ scope: liveScope, rows: [] }),
  )
  const insertLive = useCallback((row: ActivityEvent) => {
    const at = Date.now()
    setLive((prev) => {
      const rows = prev.scope === liveScope ? prev.rows : []
      if (rows.some((r) => r.row.id === row.id)) return prev
      return { scope: liveScope, rows: [{ row, at }, ...rows].slice(0, MAX_LIVE_ROWS) }
    })
  }, [liveScope])

  // Memoised on `data` rather than on a `data ?? []` expression: the fallback
  // builds a NEW array every render, so the dependency would never compare
  // equal and every consumer would re-render on every tick.
  const pages = useMemo(() => {
    const loaded: ActivityPage[] = data ?? []
    const covered = data?.[0]?.fetchedFrom ?? Number.NEGATIVE_INFINITY
    // `>=`: a row that arrived in the read's own millisecond is kept (shown
    // once either way: prependLive skips an id the answer already holds).
    const fresh = live.scope === liveScope ? live.rows.filter((r) => r.at >= covered) : []
    // Oldest first, so the newest ends on top.
    return fresh.reduceRight((acc, r) => prependLive(acc, r.row), loaded)
  }, [data, live, liveScope])
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
    /** ACT-02: a streamed row that matched the page's filters (de-duplicated by id). */
    insertLive,
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
