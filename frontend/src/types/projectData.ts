/**
 * W24 ADM-05 phase 1: export a project's settings + catalog, import it as a NEW project.
 * W30 ADM-05 run history: optionally WITH run history (schema 2), imported through a parted upload.
 *
 * The constants mirror `backend/app/services/project_export_vocab.py` value for
 * value (pinned by `backend/tests/test_project_export_vocab_parity.py`, which
 * reads this file as TEXT — keep every constant a plain literal, one record
 * entry per line). Enum-like fields are plain strings on the wire.
 *
 * Every name, sentence and count below is rendered as React text, never as
 * HTML, and never used to build an href.
 */

export const JOB_KINDS = ['export', 'import'] as const
export type ProjectDataJobKind = (typeof JOB_KINDS)[number]

/**
 * `uploading` = a parted import upload (W30) still receiving parts; `expired` = the export's object was
 * removed by the janitor after EXPORT_TTL_HOURS.
 */
export const JOB_STATUSES = ['uploading', 'queued', 'running', 'succeeded', 'failed', 'expired'] as const
export type ProjectDataJobStatus = (typeof JOB_STATUSES)[number]

export const TERMINAL_STATUSES = ['succeeded', 'failed', 'expired'] as const
/** A job in one of these is still working: the job hook polls it (a parted upload included). */
export const ACTIVE_STATUSES = ['uploading', 'queued', 'running'] as const

/** progress.phase of a history job (export: catalog → runs → documents → objects → verify; import: + index). */
export const JOB_PHASES = ['catalog', 'runs', 'documents', 'objects', 'verify', 'index'] as const
export type ProjectDataJobPhase = (typeof JOB_PHASES)[number]

/** The export card's window choices in days; `null` = all history. */
export const HISTORY_WINDOWS_DAYS = [30, 90, 365] as const
export const HISTORY_DEFAULT_DAYS = 90
/** Newest runs first inside the window; older ones are not exported. */
export const HISTORY_MAX_RUNS = 2000

/** 50 MiB — under every shipped nginx's 55m body cap; the route stops reading past it (413). */
export const IMPORT_UPLOAD_MAX_BYTES = 52428800
/** 32 MiB — one part of a parted upload (a larger file than IMPORT_UPLOAD_MAX_BYTES uploads in parts). */
export const IMPORT_PART_MAX_BYTES = 33554432
/** At most this many parts (64 × 32 MiB = 2 GiB). */
export const IMPORT_MAX_PARTS = 64
/** 2 GiB — the largest archive a parted upload takes (== the largest history export). */
export const IMPORT_ARCHIVE_MAX_BYTES = 2147483648
/** A succeeded export can be downloaded for this long, then the janitor removes it. */
export const EXPORT_TTL_HOURS = 72
/** The page's recent-jobs read (`?limit`). */
export const JOBS_LIST_LIMIT = 10
/** A queued / running job is re-read this often (a NUMBER `refreshInterval`, 0 once it is terminal). */
export const JOB_POLL_MS = 2000

/** detail = {code, message} on every W24 4xx / 5xx; a failed job's `error.message` is the same text. */
export const ERROR_SENTENCES = {
  job_not_found: 'Project data job not found.',
  export_running: 'An export of this project is already running.',
  export_not_ready: 'This export has not finished yet.',
  export_failed: 'This export failed; start a new one.',
  export_expired: 'This export was removed after 72 hours; start a new one.',
  not_an_export: 'Only an export has a download.',
  archive_unreadable: 'The archive could not be read from storage. Try again.',
  upload_too_large: 'The file is larger than 50 MB.',
  upload_empty: 'Choose a .zip file exported from TestLookup.',
  not_a_zip: 'The file is not a valid ZIP archive.',
  not_a_project_export: 'This ZIP is not a TestLookup project export (manifest.json is missing or of another kind).',
  schema_newer: 'This export was written by a newer TestLookup (schema {found}); this deployment reads schema {supported}. Upgrade, then import again.',
  schema_unknown: "This export's schema version is not one this deployment can read.",
  tampered: 'The archive does not match its manifest ({reason}); nothing was imported.',
  signature_invalid: "The archive's signature is not valid; nothing was imported.",
  unsafe_archive: 'The archive was refused before reading: {message}',
  invalid_row: '{entity} row {line} in {file} is not valid ({field}); nothing was imported.',
  unknown_entity: 'The archive contains {entity}, which this deployment does not import; nothing was imported.',
  unknown_column: 'This export has {entity}.{column}, which this deployment does not know — it was written by a newer TestLookup. Upgrade, then import again.',
  name_invalid: 'A project name of 2 to 255 characters is required.',
  name_taken: 'A project called {name} already exists; choose another name.',
  client_token_invalid: 'client_token must be a UUID.',
  import_failed: 'The import failed and was rolled back; no project was created ({error_type}).',
  interrupted: 'The job was interrupted; nothing was kept. Start it again.',
  export_build: 'The export could not be built ({error_type}).',
  export_write: 'The export could not be written to storage ({error_type}).',
  export_unverified: 'The stored export could not be verified ({reason}).',
  export_too_large: "This project's export is larger than 50 MB, the import limit.",
  row_too_large: '{entity} has {count} rows larger than 4 MB; they cannot be exported.',
  history_too_large: 'This export with run history is larger than 2 GB. Choose a shorter window.',
  history_window_invalid: 'Choose 30, 90 or 365 days, or all history.',
  max_runs_invalid: 'max_runs must be between 1 and 2000.',
  upload_parts_invalid: 'An upload has 1 to 64 parts of at most 32 MB and at most 2 GB in all.',
  part_too_large: 'A part is larger than 32 MB.',
  part_number_invalid: 'Part {part} is not part of this upload (1 to {parts}).',
  part_digest_mismatch: 'Part {part} does not match its SHA-256; send it again.',
  part_conflict: 'Part {part} was already received with different content.',
  upload_incomplete: 'The upload is missing parts {missing}.',
  upload_digest_mismatch: 'The assembled file does not match the SHA-256 given when the upload started.',
  upload_not_open: 'This upload is no longer accepting parts.',
  invalid_document: '{collection} document {line} in {file} is not valid ({reason}); nothing was imported.',
  object_unreadable: 'An object in the archive could not be written to storage ({error_type}); nothing was imported.',
  documents_unwritable: 'The documents could not be written ({error_type}); nothing was imported.',
  upload_too_large_history: 'The file is larger than 2 GB.',
} as const
export type ProjectDataErrorCode = keyof typeof ERROR_SENTENCES

/** The cards render these verbatim; `{name}` style placeholders are filled with `fillSentence`. */
export const UI_SENTENCES = {
  export_intro: "Download this project's settings and test catalog as a ZIP, with run history if you choose.",
  import_intro: 'Create a NEW project from an export. Nothing is merged into an existing project.',
  import_admin_only: 'Importing creates a project, so it is limited to administrators.',
  inert_note: 'Webhooks, team channels, Git integrations and notification rules arrive switched off; run cadences arrive paused for 90 days.',
  no_export: 'No export of this project yet.',
  export_running: 'Exporting… {done} of {total} tables',
  export_ready: 'Download ({size})',
  export_expires: 'Available until {date}.',
  import_running: 'Importing… {done} of {total} tables',
  import_done: 'Created {name}.',
  skipped_one: '1 row was skipped: {reason}',
  skipped_many: '{count} rows were skipped: {reason}',
  cleared_one: '1 assignment was cleared',
  cleared_many: '{count} assignments were cleared',
  load_exports_failed: "Could not load this project's exports.",
  load_imports_failed: 'Could not load your imports.',
  export_heading: 'Export project data',
  import_heading: 'Import as a new project',
  below_qa_lead_title: 'QA lead access required',
  below_qa_lead_body: 'Project data is limited to QA leads and administrators.',
  include_history: 'Include run history',
  history_window: 'Runs from the last',
  history_intro: 'Runs, test results, AI analyses, stored documents and attachments. Raw report uploads and pipeline scratch files are not included.',
  estimate: 'About {size} · {runs} runs · {documents} documents · {objects} attachments',
  estimate_unmeasured: 'Size unknown: {store} could not be read.',
  estimate_over: 'Larger than 2 GB — choose a shorter window.',
  phase_catalog: 'Settings and catalog',
  phase_runs: 'Runs and results',
  phase_documents: 'Stored documents',
  phase_objects: 'Attachments',
  phase_verify: 'Verifying',
  phase_index: 'Search index',
  progress_phase: '{phase}… {done} of {total}',
  uploading: 'Uploading… {done} of {total} parts',
  history_imported: '{runs} runs from {oldest} to {newest}',
  retention_note: "Runs older than this project's retention window ({days} days) will be purged on the next nightly purge.",
  retention_off_note: 'Retention is off for this project: imported runs are kept.',
  recent_runs_note: '{count} imported runs are from the last 7 days; an all-projects digest can list them.',
  reindex_queued: 'Search is being rebuilt for the new project.',
  reindex_not_queued: 'Search could not be rebuilt automatically — use Reindex on the Search page.',
  resume_upload: 'Choose the same file to resume the upload.',
  large_download_hint: 'Large archives download more reliably with the CLI: testlookup projects export {project} --history',
} as const

/** import_report.skipped[*] / cleared[*].reason → the words after "… were skipped: " / "… were cleared: ". */
export const SKIP_REASONS = {
  user_not_found: 'the person does not exist here',
  assignment: 'the new project has no other members yet',
  row_unresolved: 'it pointed at a row that is not in the export',
  retired_table: 'the table no longer exists in this version',
  run_not_exported: 'its run is not in the export',
  pipeline_not_terminal: 'its AI pipeline was still running at export',
  unsafe_key: 'its storage key is not a safe path',
  too_large: 'it is larger than 256 MB',
  invalid_document: 'it is not a valid stored document',
  pipeline_scratch: 'it pointed at a pipeline scratch file, which is not exported',
} as const
export type ProjectDataSkipReason = keyof typeof SKIP_REASONS

// ── wire shapes (mirror backend/app/models/schemas.py ProjectData*) ──────────

/** Also the 4xx `detail` shape. */
export interface ProjectDataError {
  code: string
  message: string
}

export interface ProjectDataProgress {
  /** Tables finished. */
  done: number
  /** Tables in the plan. */
  total: number
  /** W30: the history phase (a JOB_PHASES value) and how far it is. */
  phase?: ProjectDataJobPhase | string | null
  phase_done?: number | null
  phase_total?: number | null
}

/** W30: the optional body of `POST /api/v1/projects/{id}/export` (no body = the catalog-only export). */
export interface ProjectExportRequest {
  include_history: boolean
  /** A HISTORY_WINDOWS_DAYS value, or null = all history. */
  history_days?: number | null
  max_runs?: number
}

/** W30: `GET /api/v1/projects/{id}/export-estimate` — counts only; null = that store was not measured. */
export interface ProjectExportEstimate {
  include_history: boolean
  history_days?: number | null
  runs?: number | null
  runs_in_progress?: number | null
  runs_over_cap?: number | null
  test_cases?: number | null
  /** collection → documents (null = Mongo not measured). */
  documents?: Record<string, number> | null
  objects?: number | null
  object_bytes?: number | null
  estimated_bytes?: number | null
  over_limit?: boolean | null
  /** {postgres, documents, objects} → measured. */
  measured: Record<string, boolean>
}

/** W30: what an export was asked for (echoed on the job). */
export interface ProjectDataOptions {
  include_history: boolean
  history_days?: number | null
  max_runs?: number | null
}

/** W30: a parted upload's state while the import job is `uploading`. */
export interface ProjectDataUpload {
  parts: number
  bytes: number
  /** Part numbers received, ascending. */
  received: number[]
  received_bytes: number
}

/** W30: the body of `POST /api/v1/project-imports/uploads`. */
export interface ProjectImportUploadStart {
  client_token: string
  name?: string | null
  bytes: number
  parts: number
  /** SHA-256 of the WHOLE file, lower-case hex. */
  sha256: string
}

/** W30: the answer of `PUT /api/v1/project-imports/uploads/{id}/parts/{n}`. */
export interface ProjectImportPartReceipt {
  part: number
  bytes: number
  received: number[]
  /** Every part present — the client then calls complete. */
  complete: boolean
}

/** W30: `import_report.history` (null for a catalog-only import). */
export interface ProjectDataHistoryReport {
  runs: number
  test_cases: number
  oldest_run_at?: string | null
  newest_run_at?: string | null
  /** collection → documents inserted. */
  documents?: Record<string, number>
  objects?: number
  object_bytes?: number
  skipped?: ProjectDataSkip[]
  retention_paused_until?: string | null
  runs_past_retention?: number
  /** The copy's effective runs window in days; null = retention off. */
  retention_days?: number | null
  recent_runs?: number
  /** 'queued' | 'not_queued'. */
  search_reindex: string
}

export interface ProjectDataSkip {
  entity: string
  column?: string | null
  /** A SKIP_REASONS key. */
  reason: string
  count: number
}

export interface ProjectDataImportReport {
  project_id: string
  project_name: string
  project_slug: string
  /** entity → rows inserted (every exported entity present, 0 allowed). */
  inserted: Record<string, number>
  skipped?: ProjectDataSkip[]
  cleared?: ProjectDataSkip[]
  /** entity → rows that arrived disabled / paused. */
  switched_off?: Record<string, number>
  /** 'valid' | 'unknown_key' | 'unsigned'. */
  signature: string
  source_project_name: string
  source_app_version?: string | null
  exported_at: string
  /** W30: the history block (null for a catalog-only import). */
  history?: ProjectDataHistoryReport | null
}

export interface ProjectDataJob {
  id: string
  kind: ProjectDataJobKind | string
  status: ProjectDataJobStatus | string
  /** export: the source; import: the created project (null until it commits). */
  project_id?: string | null
  project_name?: string | null
  /** Full name or username — never an e-mail address. */
  requested_by_name?: string | null
  requested_at: string
  started_at?: string | null
  finished_at?: string | null
  progress?: ProjectDataProgress | null
  bytes?: number | null
  file_count?: number | null
  manifest_sha256?: string | null
  /** export: 'signed' | 'unsigned'; import: the report's values. */
  signature?: string | null
  /** export only: finished_at + EXPORT_TTL_HOURS. */
  expires_at?: string | null
  /** export: rows per entity written. */
  counts?: Record<string, number>
  import_report?: ProjectDataImportReport | null
  error?: ProjectDataError | null
  /** Only for a succeeded, unexpired export. */
  download_path?: string | null
  /** W30: an export with history — what was asked for (null for a catalog-only export / an import). */
  options?: ProjectDataOptions | null
  /** W30: only while status === 'uploading'. */
  upload?: ProjectDataUpload | null
  /** W30: export: the schema it writes (2 with history); import: the manifest's. */
  schema_version?: number | null
}

export interface ProjectDataJobList {
  items: ProjectDataJob[]
  /** All matching rows; `items` holds at most JOBS_LIST_LIMIT. */
  total: number
}

/** True while the job is queued / running (the job hook polls it). */
export function isActiveJob(job: Pick<ProjectDataJob, 'status'> | null | undefined): boolean {
  return !!job && (ACTIVE_STATUSES as readonly string[]).includes(job.status)
}

/** `'Created {name}.'` + `{name: 'X'}` → `'Created X.'`; an unknown placeholder stays as written. */
export function fillSentence(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole,
  )
}
