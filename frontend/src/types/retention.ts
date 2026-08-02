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
}

export interface RetentionPreview {
  cutoffs: RetentionPreviewCutoffs
  candidates: RetentionPreviewCandidates
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
