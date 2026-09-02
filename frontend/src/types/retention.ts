/**
 * Types for the Retention & Purge admin settings (PMF backlog US-11.4 — UI).
 *
 * These mirror the pinned retention contract verbatim — the backend
 * implements the same shapes on a parallel branch. Do not extend without
 * reconciling with `backend` (see docs/PMF_BACKLOG.md, US-11.4).
 *
 * Contract endpoints (all project-scoped, ADMIN for writes):
 *   GET  /api/v1/projects/{project_id}/retention-policy          → RetentionPolicy
 *   PUT  /api/v1/projects/{project_id}/retention-policy          → RetentionPolicy (partial body)
 *   POST /api/v1/projects/{project_id}/retention-policy/preview  → RetentionPreview
 *   POST /api/v1/projects/{project_id}/retention-policy/purge    → 202 RetentionPurgeResponse
 *        (body {confirmation_name}; 409 when the policy is disabled;
 *         422 on confirmation-name mismatch)
 */

/** Summary of the most recent purge run for this project, or null if never. */
export interface RetentionLastPurge {
  at: string
  mode: string
  counts: Record<string, unknown>
}

/** Where the effective policy values come from. */
export type RetentionPolicySource = 'default' | 'custom'

/**
 * RetentionPolicy — the read shape returned by GET/PUT.
 *
 * The backend returns contract defaults even when the project has never been
 * configured (enabled false, 90/365/180/2555, source "default"), so GET never
 * 404s for a valid project.
 */
export interface RetentionPolicy {
  enabled: boolean
  raw_events_days: number
  runs_days: number
  artifacts_days: number
  audit_days: number
  source: RetentionPolicySource
  last_purge: RetentionLastPurge | null
}

/**
 * RetentionPolicyWrite — the PUT payload. Partial: only the fields being
 * changed are sent; omitted fields keep their stored values.
 */
export interface RetentionPolicyWrite {
  enabled?: boolean
  raw_events_days?: number
  runs_days?: number
  artifacts_days?: number
  audit_days?: number
}

/** POST …/preview response — cutoff timestamps per data class. */
export interface RetentionPreviewCutoffs {
  raw_events: string
  runs: string
  artifacts: string
  audit: string
}

/** POST …/preview response — purge-candidate counts computed right now. */
export interface RetentionPreviewCandidates {
  runs: number
  test_cases: number
  mongo_docs: Record<string, number>
  minio_objects: number
  event_archive_rows: number
  audit_rows: number
  provenance_rows: number
  compliance_packs_expired: number
  evidence_artifact_rows: number
  memory_entries_expired: number
  /**
   * Nullable ON PURPOSE. These two come from stores that can be down
   * independently of Postgres (Redis and the two Chroma collections). They
   * used to report 0 on an outage, which is indistinguishable from "nothing
   * to delete" on the screen an ADMIN authorises an irreversible purge from.
   * `null` means the store was not reached — render "not measured", never 0.
   */
  analysis_cache_entries: number | null
  search_index_documents: number | null
}

export interface RetentionPreview {
  cutoffs: RetentionPreviewCutoffs
  candidates: RetentionPreviewCandidates
  /** Candidate classes whose store could not be reached. */
  unmeasured?: string[]
}

/** POST …/purge 202 response. */
export interface RetentionPurgeResponse {
  queued: boolean
}

/** The four editable day-count fields (everything writable except `enabled`). */
export type RetentionDayField =
  | 'raw_events_days'
  | 'runs_days'
  | 'artifacts_days'
  | 'audit_days'

/**
 * Per-field inclusive bounds from the pinned contract. The backend validates
 * these too; the client-side copy exists for inline feedback, not security.
 * Cross-rule (also backend-enforced): audit_days ≥ runs_days.
 */
export const RETENTION_BOUNDS: Record<RetentionDayField, { min: number; max: number }> =
  Object.freeze({
    raw_events_days: { min: 7, max: 3650 },
    runs_days: { min: 30, max: 3650 },
    artifacts_days: { min: 7, max: 3650 },
    audit_days: { min: 365, max: 3650 },
  })

/** Contract defaults returned by the backend when unconfigured. */
export const DEFAULT_RETENTION_POLICY: RetentionPolicy = Object.freeze({
  enabled: false,
  raw_events_days: 90,
  runs_days: 365,
  artifacts_days: 180,
  audit_days: 2555,
  source: 'default',
  last_purge: null,
})

// ── Criteria deletion (S5) ───────────────────────────────────────────────────

/**
 * Statuses a run can hold. Mirrors the backend `LaunchStatus` enum exactly — a
 * value outside this list matches nothing forever, and the delete would report
 * success having found zero runs.
 */
export const RUN_STATUSES = ['IN_PROGRESS', 'PASSED', 'FAILED', 'STOPPED'] as const
export type RunStatus = (typeof RUN_STATUSES)[number]

/** Max run ids the backend accepts in one criteria set (it 422s above this). */
export const MAX_RUN_IDS = 500

/**
 * What to delete. Every field is optional and AT LEAST ONE must be set — a
 * project id alone is a full project purge, not a filtered deletion, and the
 * backend refuses it.
 *
 * **AND across fields, OR within a list.** `statuses: ['FAILED']` with
 * `branches: ['main']` means *failed AND on main*.
 */
export interface RetentionCriteria {
  date_from?: string | null
  date_to?: string | null
  older_than_days?: number | null
  run_ids?: string[] | null
  statuses?: RunStatus[] | null
  suite_names?: string[] | null
  /**
   * `only` matches the run's own suite label — the conservative reading, which
   * will not delete a multi-suite run because one of its suites was named.
   * `any` matches a run with any test case in the suite; destructive, opt-in.
   */
  suite_match?: 'only' | 'any'
  branches?: string[] | null
  environments?: string[] | null
}

/** Fields that actually narrow the set. `project_id` is deliberately absent. */
export const NARROWING_FIELDS: Array<keyof RetentionCriteria> = [
  'date_from',
  'date_to',
  'older_than_days',
  'run_ids',
  'statuses',
  'suite_names',
  'branches',
  'environments',
]

/** One run the preview refuses to delete, and every reason it gave. */
export interface DeletionBlocker {
  run_id: string
  reasons: string[]
}

/**
 * A FROZEN candidate set. `job_id` is what execute takes — never the criteria
 * again, because re-resolving would run a different set from the one shown
 * here: `TestRun.status` and `primary_suite_name` are rewritten by ingestion
 * and live-session close while the job waits.
 */
export interface DeletionPreview {
  job_id: string
  project_id: string
  run_count: number
  run_ids: string[]
  candidate_hash: string
  /** The set exceeded the reviewable bound and was cut. */
  truncated: boolean
  /** Object prefixes outside the project scope — NOT deleted, reported here. */
  refused_prefixes: string[]
  blocked: DeletionBlocker[]
}

export interface DeletionExecuteAccepted {
  job_id: string
  run_count: number
  status: 'accepted'
}

/** Job states the backend can actually produce. There is no `cancelled`. */
export type DeletionJobStatus =
  | 'queued'
  | 'previewed'
  | 'running'
  | 'completed'
  | 'failed'
  | 'partial'

export interface DeletionJob {
  id: string
  project_id: string
  job_kind: string
  status: DeletionJobStatus
  criteria?: Record<string, unknown> | null
  counts?: Record<string, unknown> | null
  /** NULL means NOT MEASURED. Never render it as 0. */
  bytes_reclaimed?: number | null
  error?: string | null
  requested_at: string
  started_at?: string | null
  finished_at?: string | null
}
