/**
 * W12 SRCH-03 — the one-time move of this browser's saved searches to the
 * server, on the first load of /search after the upgrade.
 *
 * `localStorage['tl.search.saved']` held up to three deliberate saves: each
 * entry whose (query, scope, mode) the server does not hold yet is POSTed
 * (one request each, `is_shared: false`, in the pinned project or none); an
 * entry is taken out of the stored array as soon as it is on the server, and
 * the key goes when nothing is left. A POST that fails keeps its entry for the
 * next load. `localStorage['tl.search.recent']` (this browser's history) is
 * dropped unread: recent searches now live on the server.
 *
 * Storage that throws (blocked) is treated as empty: nothing is moved.
 */
import type { SavedView } from '@/services/savedViewsService'
import type { SavedSearchCreate } from '@/hooks/useSavedSearches'
import { normalizeMode, parseMode, parseScope } from './searchModel'
import {
  MAX_QUERY_CHARS, SAVED_SEARCH_PAGE, SAVE_NAME_MAX, defaultSaveName, readSavedSearch, sameSearch,
  savedSearchFilters, validSaveName, type SearchSpec,
} from './savedSearchesModel'

export const LEGACY_SAVED_KEY = 'tl.search.saved'
export const LEGACY_RECENT_KEY = 'tl.search.recent'

interface LegacyEntry {
  raw: unknown
  search: SearchSpec
  name: string
}

function readLegacy(): unknown[] | null {
  try {
    const raw = localStorage.getItem(LEGACY_SAVED_KEY)
    if (raw === null) return null
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeLegacy(entries: unknown[]): void {
  try {
    if (entries.length === 0) localStorage.removeItem(LEGACY_SAVED_KEY)
    else localStorage.setItem(LEGACY_SAVED_KEY, JSON.stringify(entries))
  } catch {
    // Storage blocked: the next load tries again.
  }
}

/** A stored entry as a search, or null when it is not one (dropped). */
export function readLegacyEntry(value: unknown): LegacyEntry | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const q = typeof record.query === 'string' ? record.query.trim() : ''
  if (!q || q.length > MAX_QUERY_CHARS) return null
  const { scope, mode } = normalizeMode(parseScope(record.scope), parseMode(record.mode))
  const label = typeof record.label === 'string' ? record.label.trim().slice(0, SAVE_NAME_MAX) : ''
  return { raw: value, search: { q, scope, mode }, name: validSaveName(label) ? label : defaultSaveName(q) }
}

/** Drop this browser's local recent searches (the server keeps them now). */
export function dropLegacyRecents(): void {
  try {
    localStorage.removeItem(LEGACY_RECENT_KEY)
  } catch {
    // Storage blocked: nothing to drop.
  }
}

export interface MigrationResult {
  /** POSTs that succeeded. */
  posted: number
  /** Entries left in the browser (their POST failed). */
  kept: number
}

/**
 * Move the stored saved searches. `serverViews` is the server's list (the
 * page's saved searches), or null when it could not be read: then nothing is
 * posted (no duplicate can be ruled out) and the entries wait for the next
 * load. `create` is the POST.
 */
export async function migrateLegacySavedSearches({
  serverViews, projectId, create,
}: {
  serverViews: readonly Pick<SavedView, 'filters'>[] | null
  projectId: string | null
  create: (payload: SavedSearchCreate) => Promise<unknown>
}): Promise<MigrationResult> {
  dropLegacyRecents()
  const stored = readLegacy()
  if (stored === null) return { posted: 0, kept: 0 }
  if (serverViews === null) return { posted: 0, kept: stored.length }

  const onServer: SearchSpec[] = serverViews.map(readSavedSearch).filter((s): s is SearchSpec => s !== null)
  let remaining = [...stored]
  let posted = 0
  for (const value of stored) {
    const entry = readLegacyEntry(value)
    if (!entry || onServer.some((s) => sameSearch(s, entry.search))) {
      // Malformed, already on the server, or posted a moment ago from an earlier
      // entry of the same array: nothing to move.
      remaining = remaining.filter((item) => item !== value)
      writeLegacy(remaining)
      continue
    }
    try {
      await create({
        project_id: projectId,
        name: entry.name,
        page: SAVED_SEARCH_PAGE,
        filters: { ...savedSearchFilters(entry.search.q, entry.search.scope, entry.search.mode) },
        is_shared: false,
      })
      posted += 1
      onServer.push(entry.search)
      remaining = remaining.filter((item) => item !== value)
      writeLegacy(remaining)
    } catch {
      // Kept for the next load.
    }
  }
  writeLegacy(remaining)
  return { posted, kept: remaining.length }
}
