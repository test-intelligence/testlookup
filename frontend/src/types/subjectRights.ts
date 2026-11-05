/**
 * W31 STRAT-12: a person's own data export (Art. 15 / 20) and an ADMIN's pseudonymising erasure (Art. 17).
 *
 * The constants mirror `backend/app/services/subject_rights_vocab.py` value for
 * value (pinned by `backend/tests/test_subject_rights_vocab_parity.py`, which
 * reads this file as TEXT — keep every constant a plain literal, one record
 * entry per line). Enum-like fields are plain strings on the wire.
 *
 * Every name, sentence and count below is rendered as React text, never as
 * HTML, and never used to build an href.
 */

export const JOB_KINDS = ['export', 'erasure'] as const
export type SubjectJobKind = (typeof JOB_KINDS)[number]

/** `expired` = the export's object was removed (the janitor after EXPORT_TTL_HOURS, or an erasure). */
export const JOB_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'expired'] as const
export type SubjectJobStatus = (typeof JOB_STATUSES)[number]

export const TERMINAL_STATUSES = ['succeeded', 'failed', 'expired'] as const
/** A job in one of these is still working: the job hook polls it. */
export const ACTIVE_STATUSES = ['queued', 'running'] as const

/** An erasure job's `phase` = the LAST phase committed; `done` ends the erasure. */
export const ERASURE_PHASES = ['lockout', 'postgres', 'objects', 'redis', 'done'] as const
export type ErasurePhase = (typeof ERASURE_PHASES)[number]

/** Who asked for an export (`job.source`). */
export const REQUEST_SOURCES = ['self', 'admin'] as const

export const ERASE_ACTIONS = ['delete', 'pseudonymise', 'reference', 'keep_audit', 'keep_record', 'out_of_scope', 'no_personal_data'] as const
export type EraseAction = (typeof ERASE_ACTIONS)[number]

export const ERASED_DISPLAY = 'Erased user'
export const PSEUDONYM_PREFIX = 'erased-'
export const ERASED_EMAIL_DOMAIN = 'erased.invalid'
export const EXPORT_TTL_HOURS = 72
export const JOBS_LIST_LIMIT = 10
/** The job hook's polling interval (ms) while a job is queued / running. */
export const JOB_POLL_MS = 2000

export const ERROR_SENTENCES = {
  job_not_found: 'Subject data job not found.',
  user_not_found: 'User not found.',
  session_required: 'Sign in to do this; an API key cannot export or erase personal data.',
  export_running: "An export of this person's data is already running.",
  export_not_ready: 'This export has not finished yet.',
  export_failed: 'This export failed; start a new one.',
  export_expired: 'This export was removed after 72 hours; start a new one.',
  not_an_export: 'Only an export has a download.',
  archive_unreadable: 'The archive could not be read from storage. Try again.',
  user_erased: 'This account was erased; it cannot be changed or restored.',
  not_a_person: 'This is a service or synthetic account; it holds no personal data to erase.',
  self_erasure: 'You cannot erase your own account; ask another administrator.',
  last_admin: 'Cannot erase the last active administrator.',
  erasure_running: 'An erasure of this account is already in progress.',
  export_blocks_erasure: "An export of this person's data is still running; erase after it finishes.",
  confirm_mismatch: 'Type the username exactly as shown to confirm.',
  export_build: 'The export could not be built ({error_type}).',
  export_write: 'The export could not be written to storage ({error_type}).',
  export_unverified: 'The stored export could not be verified ({reason}).',
  export_too_large: "This person's export is larger than 256 MB.",
  interrupted: 'The job was interrupted; nothing was kept. Start it again.',
  erasure_incomplete: 'The erasure stopped after the {phase} step; it resumes on its own within the hour, or run it again now.',
  username_reserved: 'Usernames starting with "erased-" are reserved.',
  project_export_removed: 'This export was removed because a person it lists was erased; export the project again.',
} as const
export type SubjectRightsErrorCode = keyof typeof ERROR_SENTENCES

export const UI_SENTENCES = {
  my_data_title: 'Your data',
  my_data_body: 'Download a copy of everything TestLookup holds about you: profile, memberships, preferences, sessions and key details (never a secret), sign-in links, your comments and reviews, chat history, assignments, and the activity and audit records where you are the actor.',
  my_data_start: 'Download my data',
  export_queued: 'Preparing your archive…',
  export_ready: 'Ready — {size}, signed. Available until {expires}.',
  export_none: 'No export yet.',
  erase_title: 'Erase this person',
  erase_body: "Erasing replaces this person's name, username and e-mail with {pseudonym} everywhere, deletes their sessions, keys, preferences and chat history, and signs them out. Audit records keep their name, as the law allows (Art. 17(3)). This cannot be undone.",
  erase_confirm_label: 'Type {username} to confirm',
  erase_button: 'Erase permanently',
  erase_done: 'Erased. This account now reads as “Erased user” ({pseudonym}).',
  erase_kept_title: 'Kept (Art. 17(3))',
  erased_badge: 'Erased',
} as const
export type SubjectRightsUiSentence = keyof typeof UI_SENTENCES

export const AUDIT_ACTIONS = ['subject_export_requested', 'subject_export_downloaded', 'subject_erasure_requested', 'subject_erased'] as const

// ── wire shapes (mirror backend/app/models/schemas.py Subject* / Erasure*) ──

/** Also the 4xx `detail` shape: `{code, message}`. */
export interface SubjectRightsError {
  code: string
  message: string
}

export interface SubjectJob {
  id: string
  kind: SubjectJobKind | string
  status: SubjectJobStatus | string
  /** 'self' | 'admin'. */
  source: string
  subject_user_id?: string | null
  /** Full name or username; "Erased user" once erased — never an e-mail. */
  subject_display?: string | null
  requested_by_display?: string | null
  requested_at: string
  started_at?: string | null
  finished_at?: string | null
  /** Erasure only: the last committed ERASURE_PHASES value. */
  phase?: ErasurePhase | string | null
  bytes?: number | null
  file_count?: number | null
  manifest_sha256?: string | null
  /** 'signed' | 'unsigned'. */
  signature?: string | null
  expires_at?: string | null
  /** Export: rows per member file; erasure: rows changed per location key. */
  counts?: Record<string, number>
  /** Erasure only: each counted location key's label from the inventory ("API keys"). */
  count_labels?: Record<string, string> | null
  error?: SubjectRightsError | null
  /** The job's download route — only for a succeeded, unexpired export. */
  download_path?: string | null
}

export interface SubjectJobList {
  items: SubjectJob[]
  /** All matching rows; `items` holds at most JOBS_LIST_LIMIT. */
  total: number
}

export interface ErasureLocationCount {
  /** Inventory key: "table.column" / "mongo:<coll>" / "objects:<prefix>" / "redis:<pattern>". */
  key: string
  /** Human sentence, e.g. "Chat conversations". */
  label: string
  action: EraseAction | string
  /** null with measured=false = "not counted" (the count timed out). */
  count?: number | null
  measured?: boolean
  /** keep_audit / keep_record / out_of_scope: why. */
  reason?: string | null
}

export interface ErasureBlocker {
  code: SubjectRightsErrorCode | string
  message: string
}

export interface ErasurePreview {
  user_id: string
  username: string
  display: string
  role: string
  is_active: boolean
  /** What the username WILL become. */
  pseudonym: string
  /** Non-empty → the erase button is disabled and the POST answers the first one. */
  blockers?: ErasureBlocker[]
  changes: ErasureLocationCount[]
  kept: ErasureLocationCount[]
  /** Assignments / ownerships a lead may want to reassign first — never blockers. */
  open_items?: ErasureLocationCount[]
  /** An unfinished erasure of this user (resume). */
  erasure_job?: SubjectJob | null
}

/** The body of the erasure POST. */
export interface ErasureRequest {
  confirm_username: string
}

/** `{name}` placeholders → values (React text); an unknown placeholder stays as written. */
export function fillSentence(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole,
  )
}

/** A job is still working (the hook polls it): queued / running, and an erasure not yet at `done`. */
export function isActiveJob(job: Pick<SubjectJob, 'status' | 'kind' | 'phase'> | null | undefined): boolean {
  if (!job) return false
  if (job.kind === 'erasure' && job.phase === 'done') return false
  return (ACTIVE_STATUSES as readonly string[]).includes(job.status)
}
