/**
 * W31 STRAT-12 (`docs/deferred-scope-2026-10-13/work/W31-contract.md` §1.2, §4, §6.3 G-D): the hermetic world of the
 * subject-rights pages — Settings › Profile's "Your data" and Settings › Users' Personal data / Erase — shared by
 * `ci-e2e/subject-rights.spec.ts` and `visual/prod-subject-rights.visual.spec.ts`.
 *
 * Every job / preview is in the server's §1.2 shape for its kind + status (+ phase) TOGETHER: a succeeded export has
 * bytes, a signature, a download path and an expiry 72 h after it finished; an erasure carries its last committed
 * phase (`lockout` while queued, `done` once succeeded); a preview lists 6 changes, 4 kept (one the server could not
 * count) and 2 open items, with the inventory's own labels and reasons. Names are non-hex words; times derive from
 * the frozen `NOW`. The erasure POST checks the typed username exactly as the server does (422 `confirm_mismatch`),
 * and its job reaches `done` through every phase — then the users list answers the row erased.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { ERROR_SENTENCES } from '../../../src/types/subjectRights'
import { LAYOUT, NOW } from './fixtures'
import { EMPTY_ZIP } from './fixtures-in-page-b'
import { bodyOf, type RecordedWrite } from './fixtures-in-page'
import { ADMIN } from './fixtures-releases'

const MINUTE = 60_000
const at = (minutes: number) => new Date(NOW.getTime() + minutes * MINUTE).toISOString()
const EXPORT_TTL_MINUTES = 72 * 60

// ── The people (/users) ────────────────────────────────────────────────────

const person = (id: string, username: string, fullName: string, role: string, extra: Record<string, unknown> = {}) => ({
  id,
  email: `${username}@example.test`,
  username,
  full_name: fullName,
  role,
  is_active: true,
  created_at: '2026-02-01T00:00:00Z',
  is_erased: false,
  erased_at: null as string | null,
  is_service_account: false,
  is_synthetic: false,
  ...extra,
})
export type SubjectUserRow = ReturnType<typeof person>

/** The person the walk exports and erases. */
export const SUBJECT = person('00000000-0000-4000-8000-0000005ab1ec', 'quinn.tester', 'Quinn Tester', 'TESTER')
/** What SUBJECT's username becomes: "erased-" + the id's first 8 hex digits. */
export const SUBJECT_PSEUDONYM = 'erased-00000000'
/** A person already erased (the erased-row region). */
export const ERASED = person('7ab1ec00-0000-4000-8000-0000000000e5', 'erased-7ab1ec00', 'Erased user', 'QA_ENGINEER', {
  email: 'erased-7ab1ec00@erased.invalid',
  is_active: false,
  is_erased: true,
  erased_at: at(-3 * 24 * 60),
})
export const SERVICE = person('00000000-0000-4000-8000-00000000c1b0', 'ci-runner', 'CI runner', 'QA_ENGINEER', { is_service_account: true })
export const SYNTHETIC = person('00000000-0000-4000-8000-00000000d0d0', 'qa-lead-checkout', 'Checkout QA lead', 'QA_LEAD', { is_synthetic: true })
/** A long name and e-mail: the User cell truncates at 185 px (open point 14). */
export const LONG_NAMED = person(
  '00000000-0000-4000-8000-0000000010ab',
  'maximiliana.featherstonehaugh',
  'Maximiliana Featherstonehaugh-Wolfeschlegel',
  'QA_ENGINEER',
  { email: 'maximiliana.featherstonehaugh-wolfeschlegel@quality.example.test' },
)
/** The signed-in admin's own row (no Personal data action: no self-erasure). */
export const ADMIN_ROW = person(ADMIN.id, ADMIN.username, ADMIN.full_name, 'ADMIN', { email: ADMIN.email })

// ── Jobs and the preview (§1.2) ────────────────────────────────────────────

export interface SubjectJobShape {
  id: string
  kind: 'export' | 'erasure'
  status: string
  source: string
  subject_user_id: string
  subject_display: string
  requested_by_display: string
  requested_at: string
  started_at: string | null
  finished_at: string | null
  phase: string | null
  bytes: number | null
  file_count: number | null
  manifest_sha256: string | null
  signature: string | null
  expires_at: string | null
  counts: Record<string, number>
  error: { code: string; message: string } | null
  download_path: string | null
}

export const MY_EXPORT_JOB_ID = 'dada0000-0000-4000-8000-0000000005e1'
export const PERSON_EXPORT_JOB_ID = 'dada0000-0000-4000-8000-0000000005e2'
export const ERASURE_JOB_ID = 'dada0000-0000-4000-8000-0000000005e3'
export const ERASED_JOB_ID = 'dada0000-0000-4000-8000-0000000005e4'
/** 1.4 MB: "Ready — 1.4 MB, signed. Available until Sep 21, 11:59." */
export const EXPORT_BYTES = 1_468_006

/** An export job for `subject` in the server's shape for `status` (a succeeded one finished a minute before NOW). */
export function exportJob(status: string, overrides: Partial<SubjectJobShape> = {}): SubjectJobShape {
  const id = overrides.id ?? MY_EXPORT_JOB_ID
  const done = status === 'succeeded' || status === 'expired'
  const job: SubjectJobShape = {
    id,
    kind: 'export',
    status,
    source: 'self',
    subject_user_id: SUBJECT.id,
    subject_display: SUBJECT.full_name,
    requested_by_display: SUBJECT.full_name,
    requested_at: at(-3),
    started_at: status === 'queued' ? null : at(-2),
    finished_at: done || status === 'failed' ? at(-1) : null,
    phase: null,
    bytes: done ? EXPORT_BYTES : null,
    file_count: done ? 11 : null,
    manifest_sha256: done ? 'c0ffee11'.repeat(8) : null,
    signature: done ? 'signed' : null,
    expires_at: done ? at(status === 'expired' ? -60 : EXPORT_TTL_MINUTES - 1) : null,
    counts: done ? { profile: 1, memberships: 3, sessions: 4, api_keys: 1, comments: 12, activity: 57, audit_access: 6 } : {},
    error: status === 'failed' ? { code: 'export_build', message: ERROR_SENTENCES.export_build.replace('{error_type}', 'OSError') } : null,
    download_path: status === 'succeeded' ? `/api/v1/subject-data-jobs/${id}/download` : null,
  }
  return { ...job, ...overrides }
}

/** Rows changed per inventory location by a finished erasure. */
const ERASURE_COUNTS = { 'users.*': 1, 'api_keys.user_id': 1, 'refresh_token_records.user_id': 4, 'project_members.user_id': 3, 'chat_sessions.user_id': 2, 'test_case_comments.content': 1 }

/** An erasure job at `phase` (the LAST committed step): queued at `lockout`, succeeded at `done`, running between. */
export function erasureJob(phase: 'lockout' | 'postgres' | 'objects' | 'redis' | 'done', overrides: Partial<SubjectJobShape> = {}): SubjectJobShape {
  const done = phase === 'done'
  return {
    ...exportJob('queued', { id: ERASURE_JOB_ID }),
    kind: 'erasure',
    status: phase === 'lockout' ? 'queued' : done ? 'succeeded' : 'running',
    source: 'admin',
    subject_display: done ? 'Erased user' : SUBJECT.full_name,
    requested_by_display: ADMIN.full_name,
    started_at: phase === 'lockout' ? null : at(-2),
    finished_at: done ? at(-1) : null,
    phase,
    counts: phase === 'lockout' ? {} : ERASURE_COUNTS,
    ...overrides,
  }
}

const AUDIT = 'Art. 17(3)(b)(e): an append-only audit record (the backend.audit-write-discipline gate); kept until its own retention clock'
const LEGAL = 'Art. 17(3)(e): a legal / signed record — rewriting it would break the evidence; kept until its own expiry'
const REF = "The project's record keeps pointing at the pseudonymised account, which every surface shows as \"Erased user\""
const loc = (key: string, label: string, action: string, count: number | null, reason: string | null = null) => ({
  key, label, action, count, measured: count !== null, reason,
})

/** The preview of erasing SUBJECT: 6 changes, 4 kept (the legal holds could not be counted in time), 2 open items. */
export function erasurePreview(overrides: Record<string, unknown> = {}) {
  return {
    user_id: SUBJECT.id,
    username: SUBJECT.username,
    display: SUBJECT.full_name,
    role: SUBJECT.role,
    is_active: true,
    pseudonym: SUBJECT_PSEUDONYM,
    blockers: [] as { code: string; message: string }[],
    changes: [
      loc('api_keys.user_id', 'API keys', 'delete', 1),
      loc('refresh_token_records.user_id', 'Sessions and devices', 'delete', 4),
      loc('project_members.user_id', 'Project memberships', 'delete', 3),
      loc('chat_sessions.user_id', 'Chat conversations', 'delete', 2),
      loc('test_case_comments.content', 'Comments that mention them', 'pseudonymise', 1),
      loc('users.*', 'Name, username and e-mail', 'pseudonymise', 1),
    ],
    kept: [
      loc('access_audit_logs.actor_user_id', 'Access changes they made', 'keep_audit', 6, AUDIT),
      loc('identity_events.user_id', 'Sign-in and provisioning events', 'keep_audit', 9, AUDIT),
      loc('project_activity_events.actor_id', 'Activity records', 'keep_audit', 57, AUDIT),
      loc('retention_holds.placed_by_id', 'Legal holds they placed', 'keep_audit', null, `${LEGAL}; a legal hold is itself a legal act`),
    ],
    open_items: [
      loc('test_cases.assigned_to_user_id', 'Test cases assigned to them', 'reference', 5, REF),
      loc('defects.assignee_user_id', 'Defects assigned to them', 'reference', 1, REF),
    ],
    erasure_job: null as SubjectJobShape | null,
    ...overrides,
  }
}

/** A `{detail: {code, message}}` refusal. */
export const subjectRefusal = (status: number, code: keyof typeof ERROR_SENTENCES) =>
  respond(status, { detail: { code, message: ERROR_SENTENCES[code] } })

/** The successive GET answers of a job the world created (the last repeats). */
export const myExportProgression = () => [exportJob('queued'), exportJob('running'), exportJob('running'), exportJob('succeeded')]
export const erasureProgression = () => [
  erasureJob('lockout'), erasureJob('postgres'), erasureJob('postgres'), erasureJob('objects'), erasureJob('redis'), erasureJob('done'),
]

export interface SubjectRightsWorldOptions {
  /** `GET /api/v1/me/data-exports` items, newest first (default none). */
  myExports?: SubjectJobShape[]
  /** `GET /api/v1/users` (default: the admin, SUBJECT, the long name, the service and synthetic accounts, ERASED). */
  users?: SubjectUserRow[]
  /** `GET /api/v1/users/{id}/data-jobs` items per user id (ERASED has its finished erasure by default). */
  userJobs?: Record<string, SubjectJobShape[]>
  /** `GET /api/v1/users/{id}/erasure-preview` per user id (default: SUBJECT's `erasurePreview()`). */
  previews?: Record<string, ReturnType<typeof erasurePreview>>
  /** Successive `GET /api/v1/subject-data-jobs/{id}` answers per job id (defaults: the two progressions). */
  jobAnswers?: Record<string, SubjectJobShape[]>
}

/**
 * The subject-rights routes (every path as the SPA writes it), `/settings/profile`'s MFA status, `/users`, and the
 * shell (`LAYOUT`). Writes are recorded (`writes`), downloads too (`downloads`: the job path asked for).
 */
export function subjectRightsWorld(options: SubjectRightsWorldOptions = {}) {
  const writes: RecordedWrite[] = []
  const downloads: string[] = []
  const users = (options.users ?? [ADMIN_ROW, SUBJECT, LONG_NAMED, SERVICE, SYNTHETIC, ERASED]).map((u) => ({ ...u }))
  const myExports = [...(options.myExports ?? [])]
  const userJobs: Record<string, SubjectJobShape[]> = {
    [ERASED.id]: [erasureJob('done', { id: ERASED_JOB_ID, subject_user_id: ERASED.id })],
    ...options.userJobs,
  }
  const previews = { [SUBJECT.id]: erasurePreview(), ...options.previews }
  const answers: Record<string, SubjectJobShape[]> = {
    [MY_EXPORT_JOB_ID]: myExportProgression(),
    [PERSON_EXPORT_JOB_ID]: myExportProgression().map((j) => ({ ...j, id: PERSON_EXPORT_JOB_ID, source: 'admin', requested_by_display: ADMIN.full_name })),
    [ERASURE_JOB_ID]: erasureProgression(),
    ...options.jobAnswers,
  }
  const current: Record<string, SubjectJobShape> = {}
  const userIdOf = (request: ApiRequest) => request.path.split('/')[4]
  const list = (items: SubjectJobShape[]) => ({ items: items.slice(0, 10).map((j) => current[j.id] ?? j), total: items.length })

  /** The erasure reached `done`: the users list now answers the row pseudonymised (as the Postgres phase left it). */
  const erase = (job: SubjectJobShape) => {
    const row = users.find((u) => u.id === job.subject_user_id)
    if (!row || row.is_erased) return
    Object.assign(row, {
      username: SUBJECT_PSEUDONYM, email: `${SUBJECT_PSEUDONYM}@erased.invalid`, full_name: 'Erased user',
      is_active: false, is_erased: true, erased_at: at(-1),
    })
  }

  const handlers: ApiHandlers = [
    ['/api/v1/me/data-export', (request) => {
      writes.push({ line: `POST ${request.path}`, body: bodyOf(request) })
      const job = answers[MY_EXPORT_JOB_ID][0]
      myExports.unshift(job)
      current[job.id] = job
      return respond(202, job)
    }, 'POST'],
    ['/api/v1/me/data-exports', () => list(myExports)],
    [/^\/api\/v1\/users\/[^/]+\/data-export$/, (request) => {
      writes.push({ line: `POST ${request.path}`, body: bodyOf(request) })
      const job = { ...answers[PERSON_EXPORT_JOB_ID][0], subject_user_id: userIdOf(request) }
      userJobs[job.subject_user_id] = [job, ...(userJobs[job.subject_user_id] ?? [])]
      current[job.id] = job
      return respond(202, job)
    }, 'POST'],
    [/^\/api\/v1\/users\/[^/]+\/data-jobs$/, (request) => list(userJobs[userIdOf(request)] ?? [])],
    [/^\/api\/v1\/users\/[^/]+\/erasure-preview$/, (request) => previews[userIdOf(request)] ?? subjectRefusal(404, 'user_not_found')],
    [/^\/api\/v1\/users\/[^/]+\/erasure$/, (request) => {
      const body = bodyOf(request) as { confirm_username?: string } | null
      writes.push({ line: `POST ${request.path}`, body })
      const preview = previews[userIdOf(request)]
      if (!preview) return subjectRefusal(404, 'user_not_found')
      if (preview.blockers.length) return subjectRefusal(409, preview.blockers[0].code as keyof typeof ERROR_SENTENCES)
      if (body?.confirm_username !== preview.username) return subjectRefusal(422, 'confirm_mismatch')
      const job = { ...answers[ERASURE_JOB_ID][0], subject_user_id: userIdOf(request) }
      userJobs[job.subject_user_id] = [job, ...(userJobs[job.subject_user_id] ?? [])]
      current[job.id] = job
      return respond(202, job)
    }, 'POST'],
    [/^\/api\/v1\/subject-data-jobs\/[^/]+\/download$/, (request) => {
      downloads.push(request.path)
      return respondRaw(EMPTY_ZIP, 'application/zip')
    }],
    [/^\/api\/v1\/subject-data-jobs\/[^/]+$/, (request) => {
      const id = request.path.split('/')[4]
      const queue = answers[id]
      if (!queue?.length) return current[id] ?? subjectRefusal(404, 'job_not_found')
      const next = queue.length > 1 ? (queue.shift() as SubjectJobShape) : queue[0]
      current[id] = { ...next, subject_user_id: current[id]?.subject_user_id ?? next.subject_user_id }
      if (current[id].kind === 'erasure' && current[id].phase === 'done') erase(current[id])
      return current[id]
    }],
    ['/api/v1/users', () => users],
    ['/api/v1/auth/mfa/status', () => ({
      enabled: false, enrolled_at: null, recovery_codes_remaining: 0, required_by_policy: false, sso_managed: false, secret_unreadable: false,
    })],
    ...LAYOUT,
  ]
  return { handlers, writes, downloads, users }
}

