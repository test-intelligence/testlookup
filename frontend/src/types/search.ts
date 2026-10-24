import type { PaginatedResponse } from './common'

export interface SearchResult {
  test_case_id: string
  test_run_id: string
  test_name: string
  suite_name?: string
  status: string
  failure_count: number
  last_run_date: string
  relevance_score?: number
  match_reasons?: string[]
  source_mode_used?: string
}

/**
 * How a search result relates to the global release filter.
 *
 * Search deliberately ignores that filter — it is a discovery tool, and
 * scoping it would return nothing for a test that exists but last ran in
 * another release, which reads as "that test does not exist". The backend
 * declares this in the payload so the UI can say it, rather than the UI
 * keeping a second copy of the sentence that drifts from the first.
 */
export interface SearchScope {
  release: 'not_applicable'
  note: string
}

// ── W12 SRCH-04: the parsed query (`parsed` on GET /search and GET /search/global) ──
// Enum-like fields are `string`; the vocabularies live server-side in
// backend/app/core/search_vocab.py. Every field below is React TEXT when shown.

export interface SearchParsedFilter {
  /** FILTER_KEYS: status | suite | branch | env | type | since | release | owner */
  key: string
  /** Unquoted, as typed. */
  value: string
  /** The exact source token, e.g. `suite:"API Regression"`. */
  raw: string
  /** [start, end) offsets into the stripped query the server parsed. */
  start: number
  end: number
}

export interface SearchIgnoredToken {
  token: string
  key?: string | null
  /** IGNORED_REASONS: unknown_key | invalid_value | too_long | too_many | repeated | unterminated_quote */
  reason: string
  /** The sentence the page shows verbatim. */
  message: string
  start: number
  end: number
}

export interface SearchParsed {
  /** The free text actually matched (keyword) or embedded (semantic). */
  text: string
  /** APPLIED filters, in source order. */
  filters: SearchParsedFilter[]
  ignored_tokens: SearchIgnoredToken[]
  /** entity_type -> the keys it cannot apply; that entity type was NOT searched. */
  unsupported_by: Record<string, string[]>
  /** [] = nothing was searched. */
  searched_entity_types: string[]
  release_unresolved?: string | null
  /** Every caption, rendered server-side, in order. */
  notes: string[]
}

export interface SearchResponse extends PaginatedResponse<SearchResult> {
  query: string
  /** What ANSWERED (a fallback reads `keyword`). */
  search_type: string
  scope?: SearchScope
  /** W12 SRCH-01: what was asked (keyword | semantic | hybrid). */
  requested_search_type?: string
  /** FALLBACK_REASONS: semantic_unavailable | no_semantic_match | no_text; null when nothing fell back. */
  fallback_reason?: string | null
  /** The banner sentence for `fallback_reason`, shown verbatim. */
  fallback_message?: string | null
  result_status?: 'complete' | 'partial'
  /** False means totals are lower bounds. */
  counts_are_exact?: boolean
  parsed?: SearchParsed
}

/** W12 SRCH-02: `{provider, model}` of the active embedding generation. */
export interface IndexStatusEmbedder {
  provider: string
  model: string
}

/** W12 SRCH-02: a re-embed in progress (or the last one that failed). */
export interface IndexStatusReembed {
  status: string
  target_provider: string
  target_model: string
  processed: number
  total: number | null
}

export interface IndexStatus {
  status: 'healthy' | 'not_configured' | 'unavailable' | 'unknown'
  document_count: number
  last_indexed_at: string | null
  /** W12: the ACTIVE test_case_search collection's name (null when unreadable). */
  collection?: string | null
  /** W12: the active generation tag ("" = the legacy generation). */
  generation?: string | null
  embedder?: IndexStatusEmbedder | null
  /** Bundled embedder: whether its weights are here; Ollama / OpenAI: null (not probed). */
  embedder_available?: boolean | null
  reembed?: IndexStatusReembed | null
  /** W12 SRCH-01: whether Hybrid / Semantic can answer (missing = offered). */
  semantic_available?: boolean
  /** SEMANTIC_UNAVAILABLE_REASONS; null when available. */
  semantic_reason?: string | null
  /** The sentence for `semantic_reason` (a disabled mode chip's title). */
  semantic_reason_text?: string | null
}

/** W12 SRCH-01: GET /search/similar/{testCaseId}. */
export interface SimilarFailuresResponse {
  items: SearchResult[]
  total: number
  query: string
  /** SIMILAR_SEMANTIC_STATUSES: ok | not_configured | unavailable (missing = ok). */
  semantic_status?: string
}

// ── W12 SRCH-03: recents, suggestions, saved searches ──

export interface RecentSearch {
  q: string
  /** SEARCH_SCOPES */
  scope: string
  /** SEARCH_MODES: the mode that ANSWERED. */
  mode: string
  /** null = searched in All Projects. */
  project_id?: string | null
  result_count: number
  counts_exact?: boolean
  ts: string
}

export interface RecentSearches {
  /** Newest first; filtered to the reader's current projects. */
  items: RecentSearch[]
  max: number
  retention_days: number
}

export interface SearchSuggestion {
  /** SUGGESTION_KINDS: failing_test | open_defect | active_release */
  kind: string
  label: string
  /** The query this runs. */
  q: string
  scope: string
  detail?: string | null
}

export interface SearchSuggestions {
  project_id: string
  window_days: number
  /** A top 5 (most failures first), not a count. */
  failing_tests: SearchSuggestion[]
  open_defects: SearchSuggestion[]
  active_release?: SearchSuggestion | null
}

/** `saved_views.filters` of a saved search (`page: 'search'`). */
export interface SavedSearchFilters {
  kind: 'search'
  q: string
  scope: string
  mode: string
}

// ── W12 SRCH-05: search usage ──

export interface SearchUsage {
  /** null = every project the caller can see. */
  project_id?: string | null
  /** USAGE_DAYS: 1 (today, UTC) | 7 | 30 */
  days: number
  since: string
  /** False when no search ran in the window: every rate is null ("Not measured yet", never 0 %). */
  measured: boolean
  searches: number
  zero_result_searches: number
  zero_result_rate?: number | null
  p50_latency_ms?: number | null
  p95_latency_ms?: number | null
  fallback_searches?: number
  retention_days: number
}

// ── Global Search (GS-2) ────────────────────────────────────────

export type SearchEntityType = 'test_case' | 'test_run' | 'suite' | 'defect' | 'flaky_test' | 'release'

export interface GlobalSearchResult {
  entity_type: SearchEntityType
  entity_id: string
  title: string
  subtitle: string
  project_id: string | null
  project_name: string | null
  navigation_url: string
  relevance_score: number
  match_reasons: string[]
  metadata: Record<string, unknown>
}

export interface GlobalSearchResponse {
  items: GlobalSearchResult[]
  total: number
  query: string
  search_type: string
  entity_counts: Record<string, number>
  page: number
  size: number
  pages: number
  /** False means totals are lower bounds because an adapter failed or hit its cap. */
  counts_are_exact?: boolean
  result_status?: 'complete' | 'partial'
  failed_entity_types?: SearchEntityType[]
  scope?: SearchScope
  /** W12 SRCH-04: the parsed query (chips, notes). */
  parsed?: SearchParsed
}
