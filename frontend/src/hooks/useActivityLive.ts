/**
 * The /activity Live toggle (W21d ACT-02): the hook that holds the project's
 * stream open while Live is on, and the pure helpers the page and the feed
 * hook share.
 *
 * The stream opens and stops on (project, enabled) only: a filter change does
 * not reconnect — each frame is judged against the page's CURRENT filters when
 * it arrives (`matchesActivityQuery`, the feed's own rules), and the
 * callbacks are read the same way, so a re-render never reopens the stream.
 * Leaving the page (unmount), switching project and turning Live off each
 * stop it.
 */
import { useEffect, useEffectEvent, useState } from 'react'

import type { ActivityEvent, ActivityPage, ActivityQuery } from '@/services/activityService'
import { openActivityStream } from '@/services/activityStream'
import type { LiveStatus } from '@/types/activityLive'

/** The viewer's Live choice in this browser (a convenience: OFF unless they turned it on). */
export const LIVE_PREFERENCE_KEY = 'testlookup.activity.live'

/** A missing, blocked or throwing store reads as OFF. */
export function readLivePreference(): boolean {
  try {
    return window.localStorage.getItem(LIVE_PREFERENCE_KEY) === 'on'
  } catch {
    return false
  }
}

export function writeLivePreference(on: boolean): void {
  try {
    window.localStorage.setItem(LIVE_PREFERENCE_KEY, on ? 'on' : 'off')
  } catch {
    // A blocked store only forgets the choice; Live still toggles.
  }
}

export type ActivityFeedQuery = ActivityQuery & { days?: number }

/**
 * Whether a streamed row belongs in the feed under its current filters — the
 * server's rules: category / event type lists, actor, entity, release, and a
 * search of 3+ characters as a case-insensitive substring of the summary (a
 * shorter one is ignored, as the API ignores it). The window is not checked:
 * a streamed row is new, so it is inside any "last N days".
 */
export function matchesActivityQuery(row: ActivityEvent, query: ActivityFeedQuery): boolean {
  if (query.category?.length && !query.category.includes(row.category)) return false
  if (query.event_type?.length && !query.event_type.includes(row.event_type)) return false
  if (query.actor_type && row.actor.type !== query.actor_type) return false
  if (query.actor_id && row.actor.id !== query.actor_id) return false
  if (query.entity_type && row.entity.type !== query.entity_type) return false
  if (query.entity_id && row.entity.id !== query.entity_id) return false
  if (query.release_id && row.release_id !== query.release_id) return false
  const needle = query.q?.trim() ?? ''
  if (needle.length >= 3 && !row.summary.toLowerCase().includes(needle.toLowerCase())) return false
  return true
}

/**
 * `pages` with `row` on top of page 0 — unless a loaded page already holds
 * that id (a reconnect replay, or a re-read that caught it first). Nothing
 * loaded yet → unchanged: the first read will carry the row itself.
 */
export function prependLive(pages: ActivityPage[], row: ActivityEvent): ActivityPage[] {
  if (pages.length === 0) return pages
  const itemsOf = (page: ActivityPage | undefined) => (Array.isArray(page?.items) ? page.items : [])
  if (pages.some((page) => itemsOf(page).some((item) => item.id === row.id))) return pages
  const [first, ...rest] = pages
  return [{ ...first, items: [row, ...itemsOf(first)] }, ...rest]
}

export interface LiveState {
  status: LiveStatus
  /** The server's sentence for `refused` / `unavailable`; null otherwise. */
  message: string | null
}

const OFF: LiveState = { status: 'off', message: null }
const CONNECTING: LiveState = { status: 'connecting', message: null }

export interface UseActivityLiveOptions {
  projectId: string | null
  enabled: boolean
  query: ActivityFeedQuery
  /** A matching row arrived (absent when the feed hook is mocked). */
  onInsert?: (row: ActivityEvent) => void
  /** Re-read the feed's first page. */
  onRefresh?: () => unknown
}

export function useActivityLive({ projectId, enabled, query, onInsert, onRefresh }: UseActivityLiveOptions): LiveState {
  const active = enabled && projectId ? projectId : null

  // Every (re)activation starts at Connecting: the status a previous project
  // or session ended on must not show for a frame (adjusted during render).
  const [tracked, setTracked] = useState(active)
  const [live, setLive] = useState<LiveState>(CONNECTING)
  if (tracked !== active) {
    setTracked(active)
    setLive(CONNECTING)
  }

  const insert = useEffectEvent((row: ActivityEvent) => {
    if (matchesActivityQuery(row, query)) onInsert?.(row)
  })
  const refresh = useEffectEvent(() => {
    void onRefresh?.()
  })

  useEffect(() => {
    if (!active) return undefined
    const stream = openActivityStream(active, {
      onActivity: (row) => insert(row),
      onRefresh: () => refresh(),
      // A fresh stream starts at the server's head: one re-read of page 0
      // closes the gap between the last poll and that head. A resumed stream
      // replays its own gap (or asks for a refresh).
      onReady: ({ resumed }) => {
        if (!resumed) refresh()
      },
      onStatus: (status, message) => setLive((prev) => (
        prev.status === status && prev.message === (message ?? null) ? prev : { status, message: message ?? null }
      )),
    })
    return () => stream.stop()
  }, [active])

  return active ? live : OFF
}
