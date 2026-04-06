import type { SearchResponse, SearchResult, IndexStatus, GlobalSearchResponse, SearchEntityType } from '@/types/search'
import { getData, postData } from './http'

export type SearchType = 'keyword' | 'semantic' | 'hybrid'

export const searchService = {
  search: (params: {
    q: string
    project_id?: string
    status?: string
    days?: number
    search_type?: SearchType
    page?: number
    size?: number
  }) => getData<SearchResponse>('/api/v1/search', { params }),

  /** GS-2: System-wide global search across multiple entity types. */
  globalSearch: (params: {
    q: string
    project_id?: string
    entity_types?: SearchEntityType[]
    days?: number
    page?: number
    size?: number
  }) => getData<GlobalSearchResponse>('/api/v1/search/global', {
    params: {
      ...params,
      entity_types: params.entity_types?.join(','),
    },
  }),

  getIndexStatus: () => getData<IndexStatus>('/api/v1/search/index-status'),

  triggerReindex: (projectId?: string) =>
    postData<{ task_id: string; status: string }>('/api/v1/search/reindex', { project_id: projectId }),

  findSimilar: (testCaseId: string, limit = 5) =>
    getData<{ items: SearchResult[]; total: number; query: string }>(`/api/v1/search/similar/${testCaseId}`, { params: { limit } }),
}
