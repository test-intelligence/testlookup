/**
 * Types for the Retention & Purge admin settings.
 *
 * These mirror the backend's retention contract verbatim. Do not extend one
 * side without the other.
 *
 * Contract endpoints (all project-scoped, ADMIN for writes):
 *   GET  /api/v1/projects/{project_id}/retention-policy          → RetentionPolicy
 *   PUT  /api/v1/projects/{project_id}/retention-policy          → RetentionPolicy (partial body)
 *   POST /api/v1/projects/{project_id}/retention-policy/preview  → RetentionPreview
 *   POST /api/v1/projects/{project_id}/retention-policy/purge    → 202 RetentionPurgeResponse
 *        (body {confirmation_name}; 409 when the policy is disabled;
 *         422 on confirmation-name mismatch)
 */
import {
  KEEP_LAST_RUNS_MAX,
  KEEP_LAST_RUNS_MIN,
  type HoldRef,
} from './retentionHolds'

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
  /**
   * W03 AIH-06: AI-history caps. Stored AI versions kept per run (per kind) and
   * per test result; the oldest are pruned first, version 1 and any version cited
   * by a published decision report never. Applied whenever a version is stored,
   * whether or not scheduled purging is enabled. Absent on an older server
   * (render "not reported"; never send them back).
   */
  ai_versions_per_run?: number
  ai_versions_per_test?: number
  /**
   * W22a ACT-04: the scheduled purge always keeps the newest N runs (plus the
   * latest passing run of the project and of each suite). 0-1000, default 10.
   * Absent on an older server.
   */
  keep_last_runs?: number
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
  /** W03 AIH-06: 1-100. There is no null reset: send 20 / 10 to restore the defaults. */
  ai_versions_per_run?: number
  ai_versions_per_test?: number
  /** W22a ACT-04: 0-1000 (`KEEP_LAST_RUNS_BOUNDS`). Sent only when changed. */
  keep_last_runs?: number
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
  /**
   * W03 AIH-06: stored AI versions the purge would remove (those of purged runs,
   * the per-test rows their delete cascades, and every version over a cap).
   * Absent on an older server: render "not measured", never 0.
   */
  ai_history_versions?: number
  /**
   * W12 SRCH-05: the project's search usage rows (no query text) older than
   * the fixed 30-day window. Absent on an older server: render "not measured", never 0.
   */
  search_usage_rows?: number
  /**
   * W16 AGT-13: the project's UNLABELLED shadow pairs older than their fixed 30 days (labelled
   * pairs are G2 evidence and stay). Absent on an older server: render "not measured", never 0.
   */
  shadow_pairs_unlabelled?: number
}

/** W22a ACT-05: one live hold in the preview, with what it keeps past the cutoffs. */
export interface RetentionPreviewHold extends HoldRef {
  /** Runs past the RUNS cutoff this hold pins (a run pinned twice counts under both). */
  runs_kept: number
  /** Expired packs this hold pins. */
  packs_kept: number
}

/** W22a ACT-05: what the live holds keep out of this purge. */
export interface RetentionPreviewHolds {
  /** A live project hold: every held class counts 0. */
  project_held: boolean
  /** DISTINCT runs past the runs cutoff kept by any hold. */
  runs_kept: number
  packs_kept: number
  /** Every LIVE hold of the project, in placed order (one that keeps nothing today shows 0). */
  active: RetentionPreviewHold[]
}

/** W22a ACT-04: what the keep-last floor keeps (runs clock only). */
export interface RetentionPreviewFloor {
  keep_last_runs: number
  /** Runs past the runs cutoff the floor keeps and no hold already keeps. */
  runs_kept: number
  /** The project's newest PASSED run was a candidate and is kept. */
  latest_passed_kept: boolean
  /** Distinct suites whose newest PASSED run was a candidate and is kept. */
  suites_kept: number
}

/** W22a ACT-04: a link a deletion breaks — a warning, never a block. */
export interface ReferenceBroken {
  table: string
  column: string
  count: number
  /** Rendered server-side (`REFERENCE_LABELS` / the fallback). React text. */
  label: string
}

export interface RetentionPreview {
  cutoffs: RetentionPreviewCutoffs
  candidates: RetentionPreviewCandidates
  /** Candidate classes whose store could not be reached. */
  unmeasured?: string[]
  /** W22a: absent on an older server (read as no holds). */
  holds?: RetentionPreviewHolds
  floor?: RetentionPreviewFloor | null
  /** Only links with count > 0, highest first. */
  references_broken?: ReferenceBroken[]
  /** False = the count failed: say "not measured", never "none". Absent = true. */
  references_measured?: boolean
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

/** The two AI-history caps (W03 AIH-06): versions kept, not days. */
export type RetentionCapField = 'ai_versions_per_run' | 'ai_versions_per_test'

/** Inclusive bounds of the AI-history caps (the backend validates them too, 422). */
export const CAP_BOUNDS: Record<RetentionCapField, { min: number; max: number }> = Object.freeze({
  ai_versions_per_run: { min: 1, max: 100 },
  ai_versions_per_test: { min: 1, max: 100 },
})

/** W22a ACT-04: inclusive bounds of `keep_last_runs` (the backend validates them too, 422). */
export const KEEP_LAST_RUNS_BOUNDS: { min: number; max: number } = Object.freeze({
  min: KEEP_LAST_RUNS_MIN,
  max: KEEP_LAST_RUNS_MAX,
})

/** Contract defaults returned by the backend when unconfigured. */
export const DEFAULT_RETENTION_POLICY: RetentionPolicy = Object.freeze({
  enabled: false,
  raw_events_days: 90,
  runs_days: 365,
  artifacts_days: 180,
  audit_days: 2555,
  ai_versions_per_run: 20,
  ai_versions_per_test: 10,
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
  /** A held run lists `Held: <reason>` FIRST in its reasons (W22a). */
  blocked: DeletionBlocker[]
  /** W22a ACT-04: links deleting the deletable runs breaks (warnings). Absent on an older server. */
  references_broken?: ReferenceBroken[]
  references_measured?: boolean
  /** Always false: an explicit deletion is not limited by the keep-last floor. */
  floor_applies?: boolean
  floor_note?: string
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

/** `DeletionJob.holds_honoured` — ONE shape for every job kind (W22a). */
export interface DeletionJobHoldsHonoured {
  project_held: boolean
  holds: HoldRef[]
  runs_kept: number
  packs_kept: number
  /** At most 200, sorted. */
  run_ids_kept: string[]
}

export interface DeletionJob {
  id: string
  project_id: string
  job_kind: string
  status: DeletionJobStatus
  criteria?: Record<string, unknown> | null
  counts?: Record<string, unknown> | null
  /** NULL means NOT MEASURED. Never render it as 0. */
  bytes_reclaimed?: number | null
  /**
   * W22a ACT-05: set only when a live hold covered something the job would
   * otherwise have deleted; NULL on every older job and when no hold applied.
   */
  holds_honoured?: DeletionJobHoldsHonoured | null
  /**
   * W22a ACT-04: a flat record `{measured, total, "<table>.<column>": n}`,
   * `{measured: false}` when the count failed, NULL when nothing broke.
   */
  references_broken?: Record<string, number | boolean> | null
  error?: string | null
  requested_at: string
  started_at?: string | null
  finished_at?: string | null
}
