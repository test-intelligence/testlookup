/**
 * W22b ACT-03 / ACT-06: export before delete, the purge window / pause and the instance retention defaults.
 *
 * The constants mirror `backend/app/services/retention_vocab.py` value for value (pinned by
 * `backend/tests/test_retention_export_vocab_frontend_parity.py`, which reads this file as TEXT — keep every
 * constant a plain literal on its own `export const` line, strings in one piece). Enum-like fields are plain
 * strings on the wire; a value outside these lists is refused by the server with one fixed sentence.
 *
 * Every name, sentence and error below is rendered as React text, never as HTML.
 *
 * The archive is an archive, not a backup — it cannot be re-imported (`ARCHIVE_NOT_BACKUP`).
 */

// ── ACT-03: export before delete ─────────────────────────────────────────────

export const EXPORT_TIERS = ['summary', 'evidence', 'full'] as const
export type ExportTier = (typeof EXPORT_TIERS)[number]
export const EXPORT_TIER_DEFAULT = 'summary'

/** The epic's "external" target stays cut. */
export const EXPORT_TARGETS = ['collect', 'retain'] as const
export type ExportTarget = (typeof EXPORT_TARGETS)[number]

/** An archive covers at most this many runs (refused, never truncated). */
export const EXPORT_MAX_RUNS = 2000
/** 512 MiB; the deployment may override it (`ExportEstimate.max_bytes` is what applies). */
export const EXPORT_MAX_BYTES_DEFAULT = 536870912
/** The deployment may override it (`ExportEstimate.collect_ttl_hours` is what applies). */
export const EXPORT_COLLECT_TTL_HOURS_DEFAULT = 72

export const ARCHIVE_NOT_BACKUP = 'Archive, not a backup — it cannot be re-imported.'
export const EXPORT_FULL_WARNING = 'Full copies raw payloads and text attachments after secret redaction; binary attachments such as screenshots and ZIP files are listed, not copied. It can be large.'
export const EXPORT_RETAIN_NOTE = "A retained archive stays in this deployment's object store, so only the difference is reclaimed."
export const EXPORT_COLLECT_NOTE = 'Download the archive, then confirm you have saved it; nothing is deleted before you confirm.'

/** detail = {code, message} on every export 4xx (`extractErrorMessage` reads the message). */
export const EXPORT_ERROR_SENTENCES = {
  unknown_tier: 'Unknown export tier. Valid: summary, evidence, full.',
  unknown_target: 'Unknown export target. Valid: collect, retain.',
  too_many_runs: 'An archive covers at most 2000 runs; narrow the criteria.',
  nothing_to_export: 'There are no runs to archive in this set.',
  over_cap: 'This archive would be over the size limit; choose a smaller tier or narrow the criteria.',
  job_not_found: 'Deletion job not found.',
  not_awaiting: 'This job is not waiting for its archive to be collected.',
  collect_expired: 'The collection window has closed; nothing was deleted. Preview again to start over.',
  not_collected: 'Download the archive before confirming you have saved it.',
  manifest_mismatch: "That manifest checksum is not this job's archive; download it again.",
  archive_unreadable: 'The archive could not be read from storage. Try again.',
  interactive_login_required: 'An archive is collected and confirmed by a signed-in administrator.',
} as const
export type ExportErrorCode = keyof typeof EXPORT_ERROR_SENTENCES

/** `DeletionJob.error` text of a failed / expired export job (every one says nothing was deleted). */
export const EXPORT_JOB_ERRORS = {
  build: 'Export failed — nothing was deleted: the archive could not be built ({error_type}).',
  over_cap: 'Export failed — nothing was deleted: the archive passed the {limit_mb} MB limit.',
  write: 'Export failed — nothing was deleted: the archive could not be written to storage ({error_type}).',
  unverified: 'Export failed — nothing was deleted: the stored archive could not be verified ({reason}).',
  holds_unreadable: 'Export failed — nothing was deleted: retention holds could not be read.',
  expired: 'Not collected within {hours} hours — nothing was deleted; the archive was removed.',
  interrupted: 'Export interrupted — nothing was deleted.',
} as const
/** The "expired" sentence for a one-hour window. */
export const EXPORT_JOB_EXPIRED_ONE = 'Not collected within 1 hour — nothing was deleted; the archive was removed.'

// ── ACT-06: purge window, pause, instance default ────────────────────────────

export const INHERITABLE_FIELDS = [
  'enabled',
  'raw_events_days',
  'runs_days',
  'artifacts_days',
  'audit_days',
  'keep_last_runs',
  'purge_window_start_utc',
] as const
export type InheritableField = (typeof INHERITABLE_FIELDS)[number]

/** `RetentionPolicy.field_sources` values. */
export const FIELD_SOURCES = ['project', 'instance', 'default'] as const
export type FieldSource = (typeof FIELD_SOURCES)[number]

export const PURGE_WINDOW_DEFAULT_HOUR = 2
export const PURGE_WINDOW_MIN = 0
export const PURGE_WINDOW_MAX = 23
export const PAUSE_DAYS_DEFAULT = 7
export const PAUSE_DAYS_MIN = 1
export const PAUSE_DAYS_MAX = 90

export const INSTANCE_ENABLE_PHRASE = 'enable retention for {count} projects'
export const INSTANCE_ENABLE_PHRASE_ONE = 'enable retention for 1 project'
/** The enable preview lists at most this many projects and ALWAYS returns `total`. */
export const INSTANCE_PREVIEW_MAX_ITEMS = 500

/** 422 / 409 detail {code, message} on the policy and instance routes. */
export const POLICY_ERROR_SENTENCES = {
  unknown_inherit_field: 'Unknown field in inherit. Valid: enabled, raw_events_days, runs_days, artifacts_days, audit_days, keep_last_runs, purge_window_start_utc.',
  inherit_conflict: 'A field cannot be both set and inherited in one save.',
  pause_conflict: 'Send pause_days or resume, not both.',
  enable_via_flow: 'Turning the instance default on needs its preview and a typed confirmation.',
  preview_stale: 'The projects this would switch on changed since the preview; preview again.',
  confirmation_mismatch: 'Type the confirmation exactly as shown.',
  instance_interactive_only: 'The instance default is changed by a signed-in administrator; API keys can only read it.',
  already_enabled: 'The instance default is already on.',
} as const
export type PolicyErrorCode = keyof typeof POLICY_ERROR_SENTENCES

/** The marker text when the source is "instance". */
export const INHERITED_LABEL = '{value} (instance default)'
/** … when the source is "default". */
export const DEFAULT_LABEL = '{value} (default)'
/** date = the viewer's locale date-time. */
export const PAUSED_SENTENCE = 'Paused until {date}'
export const NEXT_PURGE_SENTENCE = 'Next scheduled purge {date}'

// ── Wire shapes (backend `models/schemas.py`; every field a client may not get is optional) ──

/** One tier's archive size ESTIMATE. `archive_bytes: null` = not measured, never 0. */
export interface ExportTierEstimate {
  tier: ExportTier
  archive_bytes: number | null
  measured: boolean
  /** Over `max_bytes` (false when not measured: the build enforces the cap). */
  over_cap: boolean
}

/** `DeletionPreview.export_estimate`, computed over the DELETABLE runs. */
export interface ExportEstimate {
  run_count: number
  /** 0 < run_count <= EXPORT_MAX_RUNS. */
  exportable: boolean
  /** The `too_many_runs` / `nothing_to_export` sentence when not exportable. */
  refusal?: string | null
  /** Object bytes (exact) + Mongo bytes (estimate); null = not measured. */
  reclaim_bytes: number | null
  reclaim_object_bytes?: number | null
  reclaim_mongo_bytes?: number | null
  reclaim_measured: boolean
  /** Always the three tiers, in `EXPORT_TIERS` order. */
  tiers: ExportTierEstimate[]
  max_bytes: number
  collect_ttl_hours: number
  /** `ARCHIVE_NOT_BACKUP`. */
  note: string
}

/** `DeletionExecute` body's `export`. */
export interface ExportChoice {
  tier: ExportTier
  target: ExportTarget
}

/** GET / PUT `/api/v1/settings/retention-defaults`. A null value = the code default. */
export interface InstanceRetentionPolicy {
  enabled: boolean
  raw_events_days: number | null
  runs_days: number | null
  artifacts_days: number | null
  audit_days: number | null
  keep_last_runs: number | null
  purge_window_start_utc: number | null
  /** The seven code defaults (what a null means). */
  code_defaults: Partial<Record<InheritableField, boolean | number>>
  /** COALESCE(instance, code default) per field. */
  effective: Partial<Record<InheritableField, boolean | number>>
  /** Per field: projects whose value comes from the instance. */
  inheriting_projects: Partial<Record<InheritableField, number>>
  enabled_at?: string | null
  enabled_by_name?: string | null
  updated_at?: string | null
}

/** The PUT body. `enabled: true` is refused (`enable_via_flow`); `false` switches it off. */
export interface InstanceRetentionPolicyWrite {
  enabled?: boolean
  raw_events_days?: number
  runs_days?: number
  artifacts_days?: number
  audit_days?: number
  keep_last_runs?: number
  purge_window_start_utc?: number
  /** Reset a field to the code default (never `enabled`). */
  clear?: Exclude<InheritableField, 'enabled'>[]
}

export interface InstanceEnablePreviewItem {
  project_id: string
  name: string
  is_active: boolean
  has_policy_row: boolean
  /** The windows / floor / window hour it would run with. */
  effective_after: Record<string, number>
  /** Runs older than its runs window — before the keep-last floor and holds. */
  runs_past_window: number
}

/** GET `/api/v1/settings/retention-defaults/enable-preview` (writes nothing). */
export interface InstanceEnablePreview {
  items: InstanceEnablePreviewItem[]
  total: number
  truncated: boolean
  preview_hash: string
  confirmation_phrase: string
  already_enabled: boolean
}

/** POST `/api/v1/settings/retention-defaults/enable`. */
export interface InstanceEnableRequest {
  confirmation: string
  preview_hash: string
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

/** The typed confirmation for enabling the instance default on `total` projects (the server's phrase). */
export function enablePhrase(total: number): string {
  return total === 1 ? INSTANCE_ENABLE_PHRASE_ONE : INSTANCE_ENABLE_PHRASE.replace('{count}', String(total))
}

const APPROX_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/**
 * An approximate size: `≈ 2.1 MB`, `≈ 14 MB`, `≈ 1.2 GB` (1024-based, so the 512 MiB cap reads "512 MB" as the
 * server's sentences do; one decimal below 10, whole numbers above). `null` / `undefined` → `not measured` —
 * an unmeasured size never renders as 0.
 */
export function formatBytesApprox(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n) || n < 0) return 'not measured'
  let exponent = 0
  let value = n
  while (value >= 1024 && exponent < APPROX_UNITS.length - 1) {
    value /= 1024
    exponent += 1
  }
  const shown = exponent === 0 || value >= 10 ? String(Math.round(value)) : value.toFixed(1)
  return `≈ ${shown} ${APPROX_UNITS[exponent]}`
}

/**
 * The archive-to-reclaimed ratio of a finished export job: `export_bytes / bytes_reclaimed` (so 14 MB against
 * 180 MB is ≈ 0.078, rendered "1:13"). `null` when either figure is missing or not positive — never 0.
 */
export function reclaimRatio(job: {
  export_bytes?: number | null
  bytes_reclaimed?: number | null
}): number | null {
  const archive = job.export_bytes
  const reclaimed = job.bytes_reclaimed
  if (archive === null || archive === undefined || reclaimed === null || reclaimed === undefined) return null
  if (!(archive > 0) || !(reclaimed > 0)) return null
  return archive / reclaimed
}
