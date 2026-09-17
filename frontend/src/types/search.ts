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

export interface SearchResponse extends PaginatedResponse<SearchResult> {
  query: string
  search_type: string
  scope?: SearchScope
}

export interface IndexStatus {
  status: 'healthy' | 'unavailable' | 'unknown'
  document_count: number
  last_indexed_at: string | null
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
}
