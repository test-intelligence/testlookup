/**
 * W24 ADM-05 phase 1: export a project's settings + catalog, import it as a NEW project.
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

/** `expired` = the export's object was removed by the janitor after EXPORT_TTL_HOURS. */
export const JOB_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'expired'] as const
export type ProjectDataJobStatus = (typeof JOB_STATUSES)[number]

export const TERMINAL_STATUSES = ['succeeded', 'failed', 'expired'] as const
/** A job in one of these is still working: the job hook polls it. */
export const ACTIVE_STATUSES = ['queued', 'running'] as const

/** 50 MiB — under every shipped nginx's 55m body cap; the route stops reading past it (413). */
export const IMPORT_UPLOAD_MAX_BYTES = 52428800
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
} as const
export type ProjectDataErrorCode = keyof typeof ERROR_SENTENCES

/** The cards render these verbatim; `{name}` style placeholders are filled with `fillSentence`. */
export const UI_SENTENCES = {
  export_intro: "Download this project's settings and test catalog as a ZIP. Run history, logs and attachments are not included.",
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
  export_heading: 'Export settings and catalog',
  import_heading: 'Import as a new project',
  below_qa_lead_title: 'QA lead access required',
  below_qa_lead_body: 'Project data is limited to QA leads and administrators.',
} as const

/** import_report.skipped[*] / cleared[*].reason → the words after "… were skipped: " / "… were cleared: ". */
export const SKIP_REASONS = {
  user_not_found: 'the person does not exist here',
  assignment: 'the new project has no other members yet',
  row_unresolved: 'it pointed at a row that is not in the export',
  retired_table: 'the table no longer exists in this version',
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
