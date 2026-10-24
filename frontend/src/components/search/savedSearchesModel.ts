/**
 * W12 SRCH-03 — what a saved search is and how the page reads one back. Pure.
 *
 * A saved search is a `saved_views` row with `page: 'search'` and
 * `filters: {kind: 'search', q, scope, mode}` — exactly those four keys (the
 * server refuses any other). Reading one back never trusts it: a row with a
 * malformed filter is not listed, and an unknown scope or mode reads as All /
 * Keyword.
 */
import type { SavedView } from '@/services/savedViewsService'
import type { SavedSearchFilters } from '@/types/search'
import { normalizeMode, parseMode, parseScope, type EntityScope, type RetrievalMode } from './searchModel'

export const SAVED_SEARCH_KIND = 'search'
export const SAVED_SEARCH_PAGE = 'search' as const
export const SAVE_NAME_MIN = 2
export const SAVE_NAME_MAX = 255
/** The Name field starts with the query's first 80 characters. */
export const SAVE_NAME_PREFILL = 80
/** ⌘1 / ⌘2 / ⌘3 run the first three saved searches, in the server's order (default first, then by name). */
export const SHORTCUT_SLOTS = 3
/** The server's query limit (`MAX_QUERY_CHARS`). */
export const MAX_QUERY_CHARS = 500

export interface SearchSpec {
  q: string
  scope: EntityScope
  mode: RetrievalMode
}

/** The `filters` a saved search is stored with. */
export function savedSearchFilters(q: string, scope: string, mode: string): SavedSearchFilters {
  return { kind: SAVED_SEARCH_KIND, q: q.trim(), scope, mode }
}

/** The Name field's first value: the query (80 characters); a one-character query reads "Search: x". */
export function defaultSaveName(q: string): string {
  const trimmed = q.trim()
  if (trimmed.length === 1) return `Search: ${trimmed}`
  return trimmed.slice(0, SAVE_NAME_PREFILL)
}

/** Lower-cased, whitespace collapsed: the server's `normalize_query`. */
export function normalizeQuery(q: string): string {
  return q.trim().toLowerCase().replace(/\s+/g, ' ')
}

/** The same search: the same normalised query, scope and mode. */
export function sameSearch(
  a: { q: string; scope: string; mode: string },
  b: { q: string; scope: string; mode: string },
): boolean {
  return normalizeQuery(a.q) === normalizeQuery(b.q) && a.scope === b.scope && a.mode === b.mode
}

export function isSavedSearch(view: Pick<SavedView, 'filters'>): boolean {
  return view.filters?.kind === SAVED_SEARCH_KIND
}

/** The search a saved row runs, or null when its filter is not a saved search's. */
export function readSavedSearch(view: Pick<SavedView, 'filters'>): SearchSpec | null {
  const filters = view.filters
  if (!filters || filters.kind !== SAVED_SEARCH_KIND) return null
  const q = typeof filters.q === 'string' ? filters.q.trim() : ''
  if (!q || q.length > MAX_QUERY_CHARS) return null
  const { scope, mode } = normalizeMode(parseScope(filters.scope), parseMode(filters.mode))
  return { q, scope, mode }
}

/** The saved searches the ⌘1-⌘3 shortcuts fire, in order: the first three runnable rows. */
export function shortcutSearches(views: readonly Pick<SavedView, 'filters'>[]): SearchSpec[] {
  return views.map(readSavedSearch).filter((s): s is SearchSpec => s !== null).slice(0, SHORTCUT_SLOTS)
}

/** A name the server accepts (2-255 characters). */
export function validSaveName(name: string): boolean {
  const trimmed = name.trim()
  return trimmed.length >= SAVE_NAME_MIN && trimmed.length <= SAVE_NAME_MAX
}
