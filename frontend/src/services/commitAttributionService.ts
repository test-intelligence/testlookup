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

// W36 STRAT-08 — "Likely affected tests": an advisory ranking of THIS run's tests by path overlap with the run's
// changed files and past co-failures. Read-only; it never selects or skips tests.

export interface LikelyAffectedReason {
  /** At most 5 of the run's changed paths that matched the test. */
  overlapping_paths: string[]
  overlap_score: number
  /** Prior runs touching these files in which the test failed; null (not 0) when history is not ready. */
  co_failure_runs: number | null
  /** How many prior runs touching these files were looked at; null when history is not ready. */
  history_runs_considered: number | null
  known_flaky: boolean
}

export interface LikelyAffectedTest {
  test_fingerprint: string
  test_name: string
  class_name: string | null
  status_in_run: string
  /** 0..1. */
  score: number
  reason: LikelyAffectedReason
}

export interface LikelyAffectedHistory {
  available: boolean
  /** The readiness sentence ("only 4 of 12 runs carry a usable commit range (need 30)"), else null. */
  insufficient_data_reason: string | null
  usable_runs: number
  window_days: number
  runs_considered: number
  scan_capped: boolean
}

export interface LikelyAffectedTests {
  run_id: string
  project_id: string
  available: boolean
  reason: 'no_commit_range' | 'no_changed_files' | null
  advisory: true
  caveat: string
  changed_files_count: number
  file_detail_coverage: number | null
  source: string | null
  base_source: string | null
  base_anchor_is_strong: boolean | null
  history: LikelyAffectedHistory
  tests: LikelyAffectedTest[]
  total_candidates: number
}

/** The most rows the panel asks for (the route's default). */
export const LIKELY_AFFECTED_LIMIT = 20

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

  /** W36 STRAT-08 — the advisory "Likely affected tests" for a run (the panel renders its own failure). */
  async getLikelyAffectedTests(runId: string, limit: number = LIKELY_AFFECTED_LIMIT): Promise<LikelyAffectedTests> {
    const { data } = await api.get<LikelyAffectedTests>(
      `/api/v1/runs/${encodeURIComponent(runId)}/likely-affected-tests`,
      { params: { limit }, suppressToast: true },
    )
    return data
  },
}
