/**
 * Fixtures for the export-before-delete / purge-schedule / instance-default vitests (W22b ACT-03 / ACT-06).
 *
 * - `exportEstimate(overrides)`: a `DeletionPreview.export_estimate` in the REAL shape — always the three tiers,
 *   `full` over the 512 MiB cap by default.
 * - `jobRow(overrides)` + `awaitingJobRow` / `expiredJobRow` / `retainedJobRow` / `failedExportJobRow`: one
 *   `DeletionJob` (every W22b field present); `jobList(rows, total?)`: the REAL `{jobs, total}` answer.
 * - `instancePolicy(overrides)`, `enablePreview(n)`: the instance-defaults answers (off by default — D12).
 * - `policyWithSources(overrides)`: a `RetentionPolicy` carrying `field_sources` / `inherit_values` / the window.
 *
 * Sentinel names are non-hex words ("Payments sentinel"), so they never collide with a uuid or read as a phone
 * number. Timestamps are built at CALL time (never a module constant).
 */
import type { DeletionJob, DeletionJobList, RetentionPolicy } from '@/types/retention'
import {
  ARCHIVE_NOT_BACKUP,
  EXPORT_COLLECT_TTL_HOURS_DEFAULT,
  EXPORT_JOB_ERRORS,
  EXPORT_MAX_BYTES_DEFAULT,
  enablePhrase,
  type ExportEstimate,
  type InstanceEnablePreview,
  type InstanceEnablePreviewItem,
  type InstanceRetentionPolicy,
} from '@/types/retentionExport'

export const EXPORT_PROJECT_ID = '00000000-0000-4000-8000-00000000e0a1'
export const SENTINEL_PROJECT_NAME = 'Payments sentinel'
/** A 64-hex manifest digest (the acknowledge body must carry exactly this). */
export const MOCK_MANIFEST_SHA256 = `${'c0ffee'.repeat(10)}c0ff`
export const MOCK_ARCHIVE_SHA256 = 'beef'.repeat(16)

const MB = 1024 * 1024

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
function exportUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xe000 + sequence).padStart(12, '0')}`
}

function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString()
}

export function exportEstimate(overrides: Partial<ExportEstimate> = {}): ExportEstimate {
  const maxBytes = overrides.max_bytes ?? EXPORT_MAX_BYTES_DEFAULT
  return {
    run_count: 12,
    exportable: true,
    refusal: null,
    reclaim_bytes: 180 * MB,
    reclaim_object_bytes: 172 * MB,
    reclaim_mongo_bytes: 8 * MB,
    reclaim_measured: true,
    tiers: [
      { tier: 'summary', archive_bytes: Math.round(2.1 * MB), measured: true, over_cap: false },
      { tier: 'evidence', archive_bytes: 14 * MB, measured: true, over_cap: false },
      { tier: 'full', archive_bytes: Math.round(1.2 * 1024 * MB), measured: true, over_cap: true },
    ],
    max_bytes: maxBytes,
    collect_ttl_hours: EXPORT_COLLECT_TTL_HOURS_DEFAULT,
    note: ARCHIVE_NOT_BACKUP,
    ...overrides,
  }
}

/** A finished criteria job without an export (every W22b field present, null). */
export function jobRow(overrides: Partial<DeletionJob> = {}): DeletionJob {
  return {
    id: exportUuid(),
    project_id: EXPORT_PROJECT_ID,
    job_kind: 'criteria',
    status: 'completed',
    criteria: { statuses: ['FAILED'], older_than_days: 30 },
    counts: { runs: 12 },
    bytes_reclaimed: 180 * MB,
    holds_honoured: null,
    references_broken: null,
    error: null,
    requested_at: hoursFromNow(-2),
    started_at: hoursFromNow(-2),
    finished_at: hoursFromNow(-1),
    export_tier: null,
    export_target: null,
    export_bytes: null,
    export_manifest_sha256: null,
    export_verified_at: null,
    collect_expires_at: null,
    collected_at: null,
    acknowledged_at: null,
    export_summary: null,
    ...overrides,
  }
}

/** A verified `collect` archive waiting for its download and acknowledgement (nothing deleted yet). */
export function awaitingJobRow(overrides: Partial<DeletionJob> = {}): DeletionJob {
  return jobRow({
    status: 'awaiting_collection',
    counts: null,
    bytes_reclaimed: null,
    finished_at: null,
    export_tier: 'evidence',
    export_target: 'collect',
    export_bytes: Math.round(14.2 * MB),
    export_manifest_sha256: MOCK_MANIFEST_SHA256,
    export_verified_at: hoursFromNow(-1),
    collect_expires_at: hoursFromNow(71),
    export_summary: { run_count: 12, file_count: 27, objects_not_archived: 0, missing_runs: 0, excluded_runs: 0, signing_state: 'signed' },
    ...overrides,
  })
}

/** A `collect` job nobody acknowledged in time: nothing deleted, the archive removed. */
export function expiredJobRow(overrides: Partial<DeletionJob> = {}): DeletionJob {
  return awaitingJobRow({
    status: 'expired',
    finished_at: hoursFromNow(-1),
    collect_expires_at: hoursFromNow(-1),
    error: EXPORT_JOB_ERRORS.expired.replace('{hours}', String(EXPORT_COLLECT_TTL_HOURS_DEFAULT)),
    ...overrides,
  })
}

/** A finished `retain` job: the archive stays in storage, the runs are gone. */
export function retainedJobRow(overrides: Partial<DeletionJob> = {}): DeletionJob {
  return jobRow({
    export_tier: 'full',
    export_target: 'retain',
    export_bytes: 14 * MB,
    export_manifest_sha256: MOCK_MANIFEST_SHA256,
    export_verified_at: hoursFromNow(-1),
    export_summary: { run_count: 12, file_count: 40, objects_not_archived: 3, missing_runs: 0, excluded_runs: 1, signing_state: 'signed' },
    ...overrides,
  })
}

/** An export that could not be verified: failed, nothing deleted. */
export function failedExportJobRow(overrides: Partial<DeletionJob> = {}): DeletionJob {
  return jobRow({
    status: 'failed',
    counts: null,
    bytes_reclaimed: null,
    export_tier: 'summary',
    export_target: 'collect',
    error: EXPORT_JOB_ERRORS.unverified.replace('{reason}', 'digest mismatch'),
    ...overrides,
  })
}

/** The REAL `GET …/deletion/jobs` answer — `{jobs, total}`, never a bare array. */
export function jobList(rows: DeletionJob[], total?: number): DeletionJobList {
  return { jobs: rows, total: total ?? rows.length }
}

/** The seven code defaults (what a NULL means). */
export const CODE_DEFAULTS = {
  enabled: false,
  raw_events_days: 90,
  runs_days: 365,
  artifacts_days: 180,
  audit_days: 2555,
  keep_last_runs: 10,
  purge_window_start_utc: 2,
} as const

/** The instance defaults, OFF (D12) with every value NULL = the code default. */
export function instancePolicy(overrides: Partial<InstanceRetentionPolicy> = {}): InstanceRetentionPolicy {
  return {
    enabled: false,
    raw_events_days: null,
    runs_days: null,
    artifacts_days: null,
    audit_days: null,
    keep_last_runs: null,
    purge_window_start_utc: null,
    code_defaults: { ...CODE_DEFAULTS },
    effective: { ...CODE_DEFAULTS },
    inheriting_projects: {
      enabled: 4,
      raw_events_days: 4,
      runs_days: 4,
      artifacts_days: 4,
      audit_days: 4,
      keep_last_runs: 4,
      purge_window_start_utc: 4,
    },
    enabled_at: null,
    enabled_by_name: null,
    updated_at: hoursFromNow(-24),
    ...overrides,
  }
}

/** The enable preview for `n` affected projects (lists at most 500; `total` is always n). */
export function enablePreview(n: number, overrides: Partial<InstanceEnablePreview> = {}): InstanceEnablePreview {
  const listed = Math.min(n, 500)
  const items: InstanceEnablePreviewItem[] = Array.from({ length: listed }, (_, i) => ({
    project_id: exportUuid(),
    name: `${SENTINEL_PROJECT_NAME} ${i + 1}`,
    is_active: i % 4 !== 3,
    has_policy_row: i % 2 === 0,
    effective_after: {
      raw_events_days: 90,
      runs_days: 365,
      artifacts_days: 180,
      audit_days: 2555,
      keep_last_runs: 10,
      purge_window_start_utc: 2,
    },
    runs_past_window: (i + 1) * 3,
  }))
  return {
    items,
    total: n,
    truncated: n > listed,
    preview_hash: '5eed'.repeat(16),
    confirmation_phrase: enablePhrase(n),
    already_enabled: false,
    ...overrides,
  }
}

/**
 * A `RetentionPolicy` from a W22b server: raw events set on the project (90), runs and artifacts inherited from
 * the instance (180 / 120), the rest code defaults; window 02:00, not paused, disabled (no next purge).
 */
export function policyWithSources(overrides: Partial<RetentionPolicy> = {}): RetentionPolicy {
  return {
    enabled: false,
    raw_events_days: 90,
    runs_days: 180,
    artifacts_days: 120,
    audit_days: 2555,
    ai_versions_per_run: 20,
    ai_versions_per_test: 10,
    keep_last_runs: 10,
    source: 'custom',
    last_purge: null,
    purge_window_start_utc: 2,
    purge_paused_until: null,
    next_scheduled_purge_at: null,
    field_sources: {
      enabled: 'default',
      raw_events_days: 'project',
      runs_days: 'instance',
      artifacts_days: 'instance',
      audit_days: 'default',
      keep_last_runs: 'default',
      purge_window_start_utc: 'default',
    },
    inherit_values: {
      enabled: false,
      raw_events_days: 90,
      runs_days: 180,
      artifacts_days: 120,
      audit_days: 2555,
      keep_last_runs: 10,
      purge_window_start_utc: 2,
    },
    ...overrides,
  }
}
