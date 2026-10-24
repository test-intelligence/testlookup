import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import type { RecentSearch, RecentSearches } from '@/types/search'

/**
 * W12 SRCH-03 — the reader's recent searches, kept server-side (Redis, the
 * last 20, 30 days) so they follow the person across browsers. A search the
 * page records is prepended locally at once (`withRecent` through the bound
 * `mutate`, no revalidation): the server writes it after its response.
 */
export const RECENT_SEARCHES_KEY = ['search-recent'] as const
export const RECENT_MAX_FALLBACK = 20
export const RECENT_RETENTION_FALLBACK = 30

function normalized(q: string): string {
  return q.trim().toLowerCase().replace(/\s+/g, ' ')
}

/** The server's dedupe rule: the same normalised query, scope, mode and project. */
export function sameRecent(a: RecentSearch, b: RecentSearch): boolean {
  return normalized(a.q) === normalized(b.q) && a.scope === b.scope && a.mode === b.mode
    && (a.project_id ?? null) === (b.project_id ?? null)
}

/** `entry` first, its older copy dropped, capped at the list's max. */
export function withRecent(prev: RecentSearches | undefined, entry: RecentSearch): RecentSearches {
  const max = prev?.max ?? RECENT_MAX_FALLBACK
  const rest = (prev?.items ?? []).filter((item) => !sameRecent(item, entry))
  return {
    items: [entry, ...rest].slice(0, max),
    max,
    retention_days: prev?.retention_days ?? RECENT_RETENTION_FALLBACK,
  }
}

/** The list after Clear history. */
export function withoutRecents(prev: RecentSearches | undefined): RecentSearches {
  return { items: [], max: prev?.max ?? RECENT_MAX_FALLBACK, retention_days: prev?.retention_days ?? RECENT_RETENTION_FALLBACK }
}

export function useRecentSearches(enabled = true) {
  return useSWR<RecentSearches>(enabled ? RECENT_SEARCHES_KEY : null, () => searchService.recent(), {
    revalidateOnFocus: false,
  })
}
