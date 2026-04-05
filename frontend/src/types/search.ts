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
