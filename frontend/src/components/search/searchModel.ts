/**
 * W12 SRCH-01 — the /search page's retrieval modes and scopes. Pure: no React,
 * no fetching; the page and its tests import the copy from here.
 *
 * Hybrid and Semantic exist for test results only (`GET /api/v1/search`, the
 * one path that implements them); every other kind is searched by keyword
 * (`GET /api/v1/search/global`). So a semantic mode and a scope other than
 * Tests never stand together: choosing one switches the other, and the page
 * says so in one line.
 */
import type { SearchType } from '@/services/searchService'
import type {
  GlobalSearchResponse, GlobalSearchResult, IndexStatus, SearchResponse, SearchResult,
} from '@/types/search'

export type RetrievalMode = SearchType
/** = the page's `SCOPES` ids = the server's `SEARCH_SCOPES` (pinned by a pytest). */
export const ENTITY_SCOPES = ['all', 'tests', 'runs', 'suites', 'defects', 'flaky', 'releases'] as const
export type EntityScope = typeof ENTITY_SCOPES[number]

/** The only scope a semantic mode searches. */
export const SEMANTIC_SCOPE: EntityScope = 'tests'

/** Shown when a semantic mode moved the scope to Tests. */
export const SCOPE_SWITCH_LINE = 'Semantic and hybrid search cover test results only, so the scope is now Tests.'
/** Shown when a scope other than Tests moved a semantic mode back to Keyword. */
export const MODE_SWITCH_LINE = 'Keyword search covers every kind; semantic covers test results only.'
/** An enabled semantic chip's hint while another scope is chosen. */
export const SEMANTIC_SCOPE_HINT = 'Covers test results only: choosing it switches the scope to Tests.'
/** A disabled semantic chip's hint when the server sent no reason sentence. */
export const SEMANTIC_UNAVAILABLE_HINT = 'The semantic index status could not be read.'

const MODE_LABELS: Record<RetrievalMode, string> = { keyword: 'Keyword', hybrid: 'Hybrid', semantic: 'Semantic' }
const MODE_ORDER: readonly RetrievalMode[] = ['keyword', 'hybrid', 'semantic']

export function isSemanticMode(mode: string | null | undefined): mode is 'semantic' | 'hybrid' {
  return mode === 'semantic' || mode === 'hybrid'
}

/** A mode read from a URL, a saved search or a recent entry; anything unknown reads as Keyword. */
export function parseMode(value: unknown): RetrievalMode {
  return value === 'semantic' || value === 'hybrid' || value === 'keyword' ? value : 'keyword'
}

export function isEntityScope(value: unknown): value is EntityScope {
  return typeof value === 'string' && (ENTITY_SCOPES as readonly string[]).includes(value)
}

/** A scope read from a URL, a saved search or a recent entry; anything unknown reads as All. */
export function parseScope(value: unknown): EntityScope {
  return isEntityScope(value) ? value : 'all'
}

export interface ModeOption {
  id: RetrievalMode
  label: string
  available: boolean
  /** The chip's title: why it is disabled, or what choosing it does. */
  why?: string
}

/**
 * The three Mode chips. Hybrid and Semantic are offered unless the index
 * status says `semantic_available: false` (a status still loading, or one
 * from an older server, offers them: the server falls back honestly).
 */
export function modeAvailability(scope: EntityScope, indexStatus: IndexStatus | null | undefined): ModeOption[] {
  const semanticOff = indexStatus?.semantic_available === false
  return MODE_ORDER.map((id) => {
    if (!isSemanticMode(id)) return { id, label: MODE_LABELS[id], available: true }
    if (semanticOff) {
      return { id, label: MODE_LABELS[id], available: false, why: indexStatus?.semantic_reason_text || SEMANTIC_UNAVAILABLE_HINT }
    }
    return scope === SEMANTIC_SCOPE
      ? { id, label: MODE_LABELS[id], available: true }
      : { id, label: MODE_LABELS[id], available: true, why: SEMANTIC_SCOPE_HINT }
  })
}

/**
 * A (scope, mode) pair the page can run: a semantic mode on another scope
 * moves the scope to Tests, with the line that says so.
 */
export function normalizeMode(scope: EntityScope, mode: RetrievalMode): { scope: EntityScope; mode: RetrievalMode; note: string | null } {
  if (isSemanticMode(mode) && scope !== SEMANTIC_SCOPE) return { scope: SEMANTIC_SCOPE, mode, note: SCOPE_SWITCH_LINE }
  return { scope, mode, note: null }
}

/** What a scope chip click does to the mode: another scope takes a semantic mode back to Keyword. */
export function modeForScope(scope: EntityScope, mode: RetrievalMode): { mode: RetrievalMode; note: string | null } {
  if (scope !== SEMANTIC_SCOPE && isSemanticMode(mode)) return { mode: 'keyword', note: MODE_SWITCH_LINE }
  return { mode, note: null }
}

/** A `GET /search` row as the page's result row (the global search's shape). */
export function toResultRow(item: SearchResult): GlobalSearchResult {
  const suite = item.suite_name || 'No suite'
  return {
    entity_type: 'test_case',
    entity_id: item.test_case_id,
    title: item.test_name,
    subtitle: `${suite} · ${item.status}`,
    project_id: null,
    project_name: null,
    navigation_url: `/runs/${encodeURIComponent(item.test_run_id)}/tests/${encodeURIComponent(item.test_case_id)}`,
    relevance_score: typeof item.relevance_score === 'number' && Number.isFinite(item.relevance_score) ? item.relevance_score : 0,
    match_reasons: item.match_reasons ?? [],
    metadata: { status: item.status, suite_name: item.suite_name ?? null },
  }
}

/** The page's response: the global search's shape, plus the semantic path's fallback fields. */
export interface PageSearchResponse extends GlobalSearchResponse {
  requested_search_type?: string
  fallback_reason?: string | null
  fallback_message?: string | null
}

/** A `GET /search` answer in the page's shape (rows through `toResultRow`). */
export function fromSemanticResponse(data: SearchResponse): PageSearchResponse {
  const items = (data.items ?? []).map(toResultRow)
  return {
    items,
    total: data.total ?? items.length,
    query: data.query ?? '',
    search_type: data.search_type,
    entity_counts: { test_case: data.total ?? items.length },
    page: data.page ?? 1,
    size: data.size ?? items.length,
    pages: data.pages ?? 0,
    counts_are_exact: data.counts_are_exact,
    result_status: data.result_status,
    scope: data.scope,
    parsed: data.parsed,
    requested_search_type: data.requested_search_type,
    fallback_reason: data.fallback_reason ?? null,
    fallback_message: data.fallback_message ?? null,
  }
}

/**
 * The server's own sentence for a refused request (FastAPI `detail`: a
 * string, or a 422's first `msg`), else `fallback`. Shown as React text.
 */
export function errorSentence(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const first = detail.find((item) => item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string')
    const msg = first ? String((first as { msg: string }).msg) : ''
    if (msg.trim()) return msg
  }
  return fallback
}

/** The typeahead's rows (it asks for 15 candidates: `useSearchTypeahead`'s TYPEAHEAD_FETCH_SIZE). */
export const TYPEAHEAD_ROWS = 5

export interface DistinctMatch {
  /** The FIRST row of its entity type + title, in the server's order (what a click opens). */
  row: GlobalSearchResult
  /** How many of the candidates it stands for (one per run for a test result). */
  count: number
}

/**
 * The typeahead's rows: up to `max` DISTINCT entities. One test that ran in
 * many runs comes back once per run (the same title, "Suite · FAILED",
 * "Suite · PASSED", …); the list keeps the first row per (entity type, title)
 * in the server's order and counts the rest.
 */
export function distinctMatches(items: readonly GlobalSearchResult[], max = TYPEAHEAD_ROWS): DistinctMatch[] {
  const byKey = new Map<string, DistinctMatch>()
  for (const row of items) {
    const key = `${row.entity_type}\u0000${row.title}`
    const seen = byKey.get(key)
    if (seen) seen.count += 1
    else byKey.set(key, { row, count: 1 })
  }
  return Array.from(byKey.values()).slice(0, max)
}

/** "5 runs" for a test result standing for several runs ("5+ runs" when the candidates were capped), else "3 matches". */
export function matchCountLabel(match: DistinctMatch, capped: boolean): string | null {
  if (match.count < 2) return null
  const n = `${match.count}${capped ? '+' : ''}`
  return match.row.entity_type === 'test_case' ? `${n} runs` : `${n} matches`
}

/** The full search for `q`: exactly what Enter in the top bar opens (`TopBar.tsx`). */
export function fullSearchHref(q: string): string {
  return `/search?q=${encodeURIComponent(q.trim())}`
}

/** "12s ago" / "5m ago" / "3h ago" / "2d ago" from a ms timestamp and `now` (useNow). */
export function relativeTime(ts: number, now: number): string {
  const ms = now - ts
  if (Number.isNaN(ms) || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
