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

export interface SearchResponse extends PaginatedResponse<SearchResult> {
  query: string
  search_type: string
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
}
