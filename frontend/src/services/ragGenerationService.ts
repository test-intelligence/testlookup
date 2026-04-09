/** API service for RAG knowledge-grounded test generation. */
import { api } from './api'
import { getData, postData, deleteData } from './http'
import type {
  KnowledgeSource,
  KnowledgeSourceListResponse,
  RagRetrieveResponse,
  RagGenerateResponse,
  GenerationBatch,
  RequirementCoverage,
  RagStatus,
} from '@/types/rag-generation'

export const ragService = {
  // ── Knowledge Sources (RAG-1) ──────────────────────────────────────────────

  listSources: (projectId: string, params?: Record<string, unknown>) =>
    getData<KnowledgeSourceListResponse>('/api/v1/knowledge-sources', {
      params: { project_id: projectId, ...params },
    }),

  createSource: (projectId: string, data: {
    source_type: string
    title: string
    canonical_url: string
    external_id?: string
    classification?: string
  }) =>
    postData<KnowledgeSource, typeof data>(
      `/api/v1/knowledge-sources?project_id=${projectId}`, data,
    ),

  deleteSource: (sourceId: string) =>
    deleteData(`/api/v1/knowledge-sources/${sourceId}`),

  syncSource: (sourceId: string) =>
    postData<{ source_id: string; task_id: string; sync_status: string }, Record<string, never>>(
      `/api/v1/knowledge-sources/${sourceId}/sync`, {},
    ),

  getSourceFreshness: (sourceId: string) =>
    getData<Record<string, unknown>>(`/api/v1/knowledge-sources/${sourceId}/freshness`),

  getSyncHistory: (sourceId: string) =>
    getData<Record<string, unknown>[]>(`/api/v1/knowledge-sources/${sourceId}/sync-history`),

  // ── RAG Retrieval (RAG-7) ──────────────────────────────────────────────────

  retrieve: (data: {
    project_id: string
    query_text: string
    source_ids?: string[]
    top_k?: number
  }) =>
    api.post<RagRetrieveResponse>(
      '/api/v1/test-management/cases/rag-retrieve', data,
      { timeout: 180_000 },  // 3 min — ChromaDB may be slow on first query
    ).then(r => r.data),

  // ── RAG Generation (RAG-8) ────────────────────────────────────────────────

  generate: (data: {
    project_id: string
    prompt_text: string
    source_ids: string[]
    persist?: boolean
    generation_config?: Record<string, unknown>
  }) =>
    api.post<RagGenerateResponse>(
      '/api/v1/test-management/cases/rag-generate', data,
      { timeout: 180_000 },  // 3 min — LLM inference can be slow
    ).then(r => r.data),

  // ── Batch Review (RAG-10) ─────────────────────────────────────────────────

  getBatch: (batchId: string) =>
    getData<GenerationBatch>(`/api/v1/test-management/batches/${batchId}`),

  acceptCase: (batchId: string, caseId: string, edits?: Record<string, unknown>) =>
    postData(`/api/v1/test-management/batches/${batchId}/cases/${caseId}/accept`, { edits }),

  rejectCase: (batchId: string, caseId: string, reason?: string) =>
    postData(`/api/v1/test-management/batches/${batchId}/cases/${caseId}/reject`, { reason }),

  bulkAccept: (batchId: string, caseIds: string[]) =>
    postData(`/api/v1/test-management/batches/${batchId}/accept`, { case_ids: caseIds }),

  // ── Coverage (RAG-9) ──────────────────────────────────────────────────────

  getBatchCoverage: (batchId: string) =>
    getData<RequirementCoverage[]>(`/api/v1/test-management/batches/${batchId}/coverage`),

  // ── Citations (RAG-11) ────────────────────────────────────────────────────

  getCaseCitations: (caseId: string) =>
    getData<Record<string, unknown>[]>(`/api/v1/test-management/cases/${caseId}/citations`),

  // ── Stale Cases (RAG-12) ──────────────────────────────────────────────────

  getStaleCases: (projectId: string, page?: number) =>
    getData<{ items: Record<string, unknown>[]; total: number }>(
      '/api/v1/test-management/cases/stale',
      { params: { project_id: projectId, page: page ?? 1 } },
    ),

  dismissStale: (caseId: string) =>
    postData(`/api/v1/test-management/cases/${caseId}/dismiss-stale`, {}),

  // ── Status (RAG-14) ───────────────────────────────────────────────────────

  getStatus: () =>
    getData<RagStatus>('/api/v1/test-management/rag/status'),

  getBatchEval: (batchId: string) =>
    getData<Record<string, unknown>>(`/api/v1/test-management/batches/${batchId}/eval`),
}
