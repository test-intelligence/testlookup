/**
 * Fixtures for the subject-rights vitests (W31 STRAT-12), in the REAL §1.2 shapes.
 *
 * - `subjectJobFixture({kind, status, phase, …})`: one `SubjectJob` whose fields match what the server returns
 *   for that kind + status (+ phase) together: a succeeded export has bytes, file count, counts, a signature, a
 *   download path and an expiry; an expired one has neither path nor a future expiry; a failed export carries an
 *   `error`; an erasure carries its `phase` (`lockout` while queued, `done` once succeeded) and per-location
 *   counts; a running erasure at `erasure_incomplete` names its phase.
 * - `erasurePreviewFixture({blockers, changes, kept, open_items, …})`: the default is the visual's state —
 *   6 changes, 4 kept, 2 open items, no blockers.
 * - `subjectJobList(items, total?)`: the `{items, total}` answer.
 * - `subjectErrorBody(code, fmt?, extra?)` / `exportRunningBody(jobId)` / `subjectErrorBlob(code)` /
 *   `subjectRefusal(status, body)`: the `{detail: {code, message}}` refusals as JSON, as a Blob (a blob
 *   download's refusal) and as an AxiosError.
 * - W38: `myErasureRequestFixture({status})`, `erasureRequestRowFixture({status, …})`, `erasureRequestList(items)`:
 *   the person's newest erasure request and the administrators' queue rows, each in the shape its status has.
 *
 * Names are non-hex words; timestamps are built at CALL time (never a module constant).
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

import {
  DECLINE_SENTENCES,
  ERASED_DISPLAY,
  ERROR_SENTENCES,
  EXPORT_TTL_HOURS,
  fillSentence,
  PSEUDONYM_PREFIX,
  type DeclineCode,
  type ErasureBlocker,
  type ErasureLocationCount,
  type ErasurePreview,
  type ErasureRequestList,
  type ErasureRequestRead,
  type ErasureRequestStatus,
  type MyErasureRequest,
  type SubjectJob,
  type SubjectJobList,
  type SubjectRightsErrorCode,
} from '@/types/subjectRights'

export const SUBJECT_USER_ID = '00000000-0000-4000-8000-0000005ab1ec'
export const SUBJECT_USERNAME = 'quinn.tester'
export const SUBJECT_DISPLAY = 'Quinn Tester'
export const ADMIN_DISPLAY = 'Morgan Admin'
/** What the subject's username becomes: "erased-" + the id's first 8 hex digits. */
export const SUBJECT_PSEUDONYM = `${PSEUDONYM_PREFIX}00000000`
const MB = 1024 * 1024

let sequence = 0

function jobUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0x5000 + sequence).padStart(12, '0')}`
}

function minutesFromNow(minutes: number): string {
  return new Date(Date.now() + minutes * 60_000).toISOString()
}

/** Rows per archive member of a typical export. */
export function exportMemberCounts(): Record<string, number> {
  return { memberships: 3, sessions: 4, api_keys: 1, comments: 12, reviews: 2, activity: 57, audit_access: 6 }
}

/** Rows changed per inventory location of a finished erasure. */
export function erasureCounts(): Record<string, number> {
  return { 'users.*': 1, 'api_keys.user_id': 1, 'project_members.user_id': 3, 'chat_sessions.user_id': 2 }
}

type JobOverrides = Partial<SubjectJob> & { kind?: 'export' | 'erasure'; status?: SubjectJob['status'] }

export function subjectJobFixture(overrides: JobOverrides = {}): SubjectJob {
  const kind = overrides.kind ?? 'export'
  const status = overrides.status ?? 'succeeded'
  const id = overrides.id ?? jobUuid()
  const isExport = kind === 'export'
  const base: SubjectJob = {
    id,
    kind,
    status,
    source: isExport ? 'self' : 'admin',
    subject_user_id: SUBJECT_USER_ID,
    subject_display: SUBJECT_DISPLAY,
    requested_by_display: isExport ? SUBJECT_DISPLAY : ADMIN_DISPLAY,
    requested_at: minutesFromNow(-5),
    started_at: status === 'queued' ? null : minutesFromNow(-4),
    finished_at: null,
    phase: null,
    bytes: null,
    file_count: null,
    manifest_sha256: null,
    signature: null,
    expires_at: null,
    counts: {},
    error: null,
    download_path: null,
  }
  if (isExport) {
    if (status === 'succeeded' || status === 'expired') {
      base.finished_at = minutesFromNow(-1)
      base.bytes = Math.round(1.4 * MB)
      base.file_count = 11
      base.manifest_sha256 = 'f00d'.repeat(16)
      base.signature = 'signed'
      base.counts = exportMemberCounts()
      base.expires_at = status === 'expired' ? minutesFromNow(-60) : minutesFromNow(EXPORT_TTL_HOURS * 60 - 1)
      if (status === 'succeeded') base.download_path = `/api/v1/subject-data-jobs/${id}/download`
    }
    if (status === 'failed') {
      base.finished_at = minutesFromNow(-1)
      base.error = { code: 'export_build', message: fillSentence(ERROR_SENTENCES.export_build, { error_type: 'OSError' }) }
    }
  } else {
    base.phase = status === 'queued' ? 'lockout' : status === 'succeeded' ? 'done' : 'postgres'
    if (status !== 'queued') base.counts = erasureCounts()
    if (status === 'succeeded') {
      base.finished_at = minutesFromNow(-1)
      base.subject_display = ERASED_DISPLAY
    }
  }
  return { ...base, ...overrides }
}

/** A running erasure whose last attempt stopped after `phase` (it resumes; never "failed"). */
export function incompleteErasureFixture(phase: 'lockout' | 'postgres' | 'objects' | 'redis' = 'postgres'): SubjectJob {
  return subjectJobFixture({
    kind: 'erasure',
    status: 'running',
    phase,
    error: { code: 'erasure_incomplete', message: fillSentence(ERROR_SENTENCES.erasure_incomplete, { phase }) },
  })
}

export function subjectJobList(items: SubjectJob[], total = items.length): SubjectJobList {
  return { items, total }
}

/** The 6 rows the preview changes (delete / pseudonymise), in the inventory's order. */
export function previewChanges(): ErasureLocationCount[] {
  return [
    { key: 'api_keys.user_id', label: 'API keys', action: 'delete', count: 1, measured: true },
    { key: 'refresh_token_records.user_id', label: 'Sessions and devices', action: 'delete', count: 4, measured: true },
    { key: 'project_members.user_id', label: 'Project memberships', action: 'delete', count: 3, measured: true },
    { key: 'chat_sessions.user_id', label: 'Chat conversations', action: 'delete', count: 2, measured: true },
    { key: 'test_case_comments.content', label: 'Comments that mention this person', action: 'pseudonymise', count: 1, measured: true },
    { key: 'users.*', label: 'Name, username and e-mail', action: 'pseudonymise', count: 1, measured: true },
  ]
}

/** The 4 kept rows (Art. 17(3)) with their reasons. */
export function previewKept(): ErasureLocationCount[] {
  return [
    { key: 'access_audit_logs.actor_user_id', label: 'Access changes they made', action: 'keep_audit', count: 6, measured: true, reason: 'The record of who changed access (Art. 17(3)).' },
    { key: 'identity_events.user_id', label: 'Sign-in and provisioning events', action: 'keep_audit', count: 9, measured: true, reason: 'Authentication evidence.' },
    { key: 'project_activity_events.actor_id', label: 'Activity records', action: 'keep_audit', count: 57, measured: true, reason: 'The activity ledger is append-only; the product shows "Erased user".' },
    { key: 'retention_holds.placed_by_id', label: 'Legal holds they placed', action: 'keep_audit', count: null, measured: false, reason: 'A legal hold is itself a legal record (Art. 17(3)(e)).' },
  ]
}

/** The 2 open items (assignments a lead may reassign first — never blockers). */
export function previewOpenItems(): ErasureLocationCount[] {
  return [
    { key: 'test_cases.assigned_to_user_id', label: 'Assigned test cases', action: 'reference', count: 5, measured: true },
    { key: 'releases.owner_user_id', label: 'Releases they own', action: 'reference', count: 1, measured: true },
  ]
}

export function blockerFixture(code: SubjectRightsErrorCode = 'last_admin'): ErasureBlocker {
  return { code, message: ERROR_SENTENCES[code] }
}

export function erasurePreviewFixture(overrides: Partial<ErasurePreview> = {}): ErasurePreview {
  return {
    user_id: SUBJECT_USER_ID,
    username: SUBJECT_USERNAME,
    display: SUBJECT_DISPLAY,
    role: 'TESTER',
    is_active: true,
    pseudonym: SUBJECT_PSEUDONYM,
    blockers: [],
    changes: previewChanges(),
    kept: previewKept(),
    open_items: previewOpenItems(),
    erasure_job: null,
    ...overrides,
  }
}

export function subjectErrorBody(
  code: SubjectRightsErrorCode,
  fmt: Record<string, string | number> = {},
  extra: Record<string, unknown> = {},
): { detail: { code: string; message: string } & Record<string, unknown> } {
  return { detail: { code, message: fillSentence(ERROR_SENTENCES[code], fmt), ...extra } }
}

export function exportRunningBody(jobId: string) {
  return subjectErrorBody('export_running', {}, { job_id: jobId })
}

/** A refusal as a blob download receives it. */
export function subjectErrorBlob(code: SubjectRightsErrorCode, fmt: Record<string, string | number> = {}): Blob {
  return new Blob([JSON.stringify(subjectErrorBody(code, fmt))], { type: 'application/json' })
}

// ── W38: the person's erasure request and the administrators' queue (contract §2 shapes) ──

export const REQUEST_ID = '00000000-0000-4000-8000-0000000e5a01'
export const OTHER_PERSON_ID = '00000000-0000-4000-8000-0000000e5b02'

type MyRequestOverrides = Partial<MyErasureRequest> & { status?: ErasureRequestStatus }

/**
 * My newest erasure request in the server's shape for its status: a pending one has no decision; a declined one
 * carries a code and the code's sentence; a withdrawn / accepted one has `decided_at` and no code.
 */
export function myErasureRequestFixture(overrides: MyRequestOverrides = {}): MyErasureRequest {
  const status = overrides.status ?? 'pending'
  const declined = status === 'declined'
  const code: DeclineCode | null = declined ? ((overrides.decline_code as DeclineCode | undefined) ?? 'legal_obligation') : null
  return {
    id: REQUEST_ID,
    status,
    requested_at: minutesFromNow(-3 * 24 * 60),
    decided_at: status === 'pending' ? null : minutesFromNow(-24 * 60),
    decline_code: code,
    decline_message: code ? DECLINE_SENTENCES[code] : null,
    ...overrides,
  }
}

type RowOverrides = Partial<ErasureRequestRead> & { status?: ErasureRequestStatus }

/** One queue row in the server's shape for its status (accepted: the erasure job; declined: the code). */
export function erasureRequestRowFixture(overrides: RowOverrides = {}): ErasureRequestRead {
  const status = overrides.status ?? 'pending'
  const decided = status !== 'pending'
  return {
    id: REQUEST_ID,
    subject_user_id: SUBJECT_USER_ID,
    subject_display: SUBJECT_DISPLAY,
    subject_username: SUBJECT_USERNAME,
    subject_is_erased: false,
    subject_is_active: true,
    requester_is_you: false,
    status,
    requested_at: minutesFromNow(-3 * 24 * 60),
    decided_at: decided ? minutesFromNow(-24 * 60) : null,
    decided_by_display: decided && status !== 'withdrawn' ? ADMIN_DISPLAY : null,
    decline_code: status === 'declined' ? 'other' : null,
    erasure_job_id: status === 'accepted' ? jobUuid() : null,
    ...overrides,
  }
}

export function erasureRequestList(items: ErasureRequestRead[], total = items.length): ErasureRequestList {
  return { items, total }
}

/** An AxiosError carrying `body` with HTTP `status`, as the service's callers receive it. */
export function subjectRefusal(status: number, body: unknown): AxiosError {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', headers: {}, config, data: body } as AxiosResponse
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, undefined, response)
}
