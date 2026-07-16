import { api } from './api'

// Epic 8 US-8.1/US-8.2 — commit-range association + suspect ranking.

export interface RangeCommit {
  sha: string
  author: string | null
  message: string | null
  files?: string[]
  committed_at?: string | null
  commit_url?: string | null
}

export interface CommitRange {
  run_id: string
  available: boolean
  source: 'connector' | 'supplied' | 'unavailable' | null
  base_commit: string | null
  head_commit: string | null
  base_run_id?: string | null
  commits: RangeCommit[]
  resolved_at?: string | null
}

export interface SuspectRationale {
  overlapping_files: string[]
  overlap_score: number
  recency_rank: number
  author_touched_module_before: boolean
  changed_file_count: number
}

export interface Suspect {
  sha: string
  author: string | null
  message: string | null
  committed_at?: string | null
  score: number
  rationale: SuspectRationale
  commit_url?: string | null
}

export interface SuspectRanking {
  run_id: string
  cluster_id: string | null
  fingerprint: string | null
  available: boolean
  reason?: string
  source?: string
  has_location_signal?: boolean
  target_test_count?: number
  base_commit?: string | null
  head_commit?: string | null
  caveat: string
  suspects: Suspect[]
}

export interface SuspectQuery {
  clusterId?: string | null
  fingerprint?: string | null
}

export const commitAttributionService = {
  async getCommitRange(runId: string): Promise<CommitRange> {
    const { data } = await api.get<CommitRange>(`/api/v1/runs/${runId}/commit-range`)
    return data
  },

  async getSuspects(runId: string, query: SuspectQuery = {}): Promise<SuspectRanking> {
    const params: Record<string, string> = {}
    if (query.clusterId) params.cluster_id = query.clusterId
    if (query.fingerprint) params.fingerprint = query.fingerprint
    const { data } = await api.get<SuspectRanking>(`/api/v1/runs/${runId}/suspects`, { params })
    return data
  },
}
