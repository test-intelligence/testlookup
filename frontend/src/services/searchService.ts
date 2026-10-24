import type {
  SearchResponse,
  IndexStatus,
  GlobalSearchResponse,
  SearchEntityType,
  SimilarFailuresResponse,
  RecentSearches,
  SearchSuggestions,
  SearchUsage,
} from '@/types/search'
import { deleteData, getData, postData } from './http'

export type SearchType = 'keyword' | 'semantic' | 'hybrid'

export const searchService = {
  /**
   * The test-result search: the only path that implements Hybrid and
   * Semantic (W12 SRCH-01). `record: true` only on the /search page's own
   * submitted page-1 searches (a recent entry + a usage row, after the
   * response); everything else leaves it out.
   */
  search: (params: {
    q: string
    project_id?: string
    status?: string
    days?: number
    search_type?: SearchType
    page?: number
    size?: number
    record?: boolean
  }) => getData<SearchResponse>('/api/v1/search', { params }),

  /**
   * GS-2: System-wide global search across multiple entity types. `record` as on `search`.
   * `options.suppressToast`: the caller renders a failure itself (the top-bar
   * typeahead's "Could not load matches."), so no global toast; omitted, the
   * request config is exactly as before.
   */
  globalSearch: (params: {
    q: string
    project_id?: string
    entity_types?: SearchEntityType[]
    days?: number
    page?: number
    size?: number
    record?: boolean
  }, options: { suppressToast?: boolean } = {}) => getData<GlobalSearchResponse>('/api/v1/search/global', {
    params: {
      ...params,
      entity_types: params.entity_types?.join(','),
    },
    ...(options.suppressToast ? { suppressToast: true } : {}),
  }),

  getIndexStatus: (projectId?: string) =>
    getData<IndexStatus>(
      '/api/v1/search/index-status',
      { params: projectId ? { project_id: projectId } : {} },
    ),

  /**
   * Project-scoped totals for the /search chip + Index Health panels.
   * Source-of-truth fallback for when no search query is active — the
   * search-response ``entity_counts`` field is empty until the user
   * types something.
   */
  getEntityCounts: (projectId?: string) =>
    getData<Record<SearchEntityType, number>>(
      '/api/v1/search/entity-counts',
      { params: projectId ? { project_id: projectId } : {} },
    ),

  /** Re-index now: a refusal is rendered in-page (the server's sentence), so no global toast. */
  triggerReindex: (projectId?: string, full = false) =>
    postData<{ task_id: string; status: string }>(
      '/api/v1/search/reindex',
      undefined,
      { params: { ...(projectId ? { project_id: projectId } : {}), full }, suppressToast: true },
    ),

  /** W12 SRCH-01: up to `limit` FAILED / BROKEN results of the same project, nearest first. */
  findSimilar: (testCaseId: string, limit = 5) =>
    getData<SimilarFailuresResponse>(
      `/api/v1/search/similar/${encodeURIComponent(testCaseId)}`,
      { params: { limit } },
    ),

  /** W12 SRCH-03: the caller's recent searches (server-side, every browser). */
  recent: () => getData<RecentSearches>('/api/v1/search/recent'),

  /** W12 SRCH-03: clear the caller's recent searches; a failure is rendered in-page. */
  clearRecent: () => deleteData<void>('/api/v1/search/recent', { suppressToast: true }),

  /** W12 SRCH-03: suggested searches for ONE project. */
  suggested: (projectId: string) =>
    getData<SearchSuggestions>('/api/v1/search/suggested', { params: { project_id: projectId } }),

  /** W12 SRCH-05: search usage (QA lead+); `projectId` null = every project the caller can see. */
  usage: (projectId: string | null, days = 1) =>
    getData<SearchUsage>('/api/v1/search/usage', {
      params: { ...(projectId ? { project_id: projectId } : {}), days },
    }),
}
