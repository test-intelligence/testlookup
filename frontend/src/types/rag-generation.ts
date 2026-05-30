/** Types for Knowledge-Grounded Test Case Generation (RAG). */

export interface KnowledgeSource {
  id: string
  project_id: string
  source_type: string
  title: string
  canonical_url: string
  external_id: string | null
  owner_id: string | null
  sync_status: string
  last_synced_at: string | null
  sync_error: string | null
  content_hash: string | null
  classification: string
  is_archived: boolean
  created_at: string
  updated_at: string | null
}

export interface KnowledgeSourceListResponse {
  items: KnowledgeSource[]
  total: number
  page: number
  page_size: number
}

export interface RetrievedChunk {
  vector_id: string
  source_id: string
  source_title: string
  section_heading: string | null
  chunk_text: string
  relevance_score: number
  requirement_id: string | null
}

export interface RagRetrieveResponse {
  chunks: RetrievedChunk[]
  total: number
}

export interface Citation {
  case_index: number
  vector_id: string
  source_id: string
  source_title: string
  section_heading: string | null
  chunk_text_preview: string | null
  relevance_score: number | null
}

export interface RagGenerateResponse {
  batch_id: string
  generation_mode: string
  test_cases: GeneratedCase[]
  citations: Citation[]
  coverage_summary: string | null
  gaps_noted: string[]
  created_ids: string[]
}

export interface GeneratedCase {
  title: string
  description: string | null
  steps: { step_number: number; action: string; expected_result: string }[]
  test_type: string
  priority: string
  severity: string
  grounding_notes?: string
}

export interface GenerationBatch {
  id: string
  project_id: string
  created_by_id: string | null
  generation_mode: string
  cases_generated: number
  cases_accepted: number
  cases_rejected: number
  coverage_score: number | null
  status: string
  llm_model_used: string | null
  created_at: string
  completed_at: string | null
}

export interface RequirementCoverage {
  id: string
  batch_id: string
  project_id: string
  requirement_id: string
  requirement_text: string | null
  coverage_status: string
  covered_by_case_ids: string[] | null
  created_at: string
}

export interface RagStatus {
  enabled: boolean
  feature_flag: string
  total_sources: number
  total_batches: number
  total_chunks: number
}
