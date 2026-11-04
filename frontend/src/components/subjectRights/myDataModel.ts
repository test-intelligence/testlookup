/**
 * W31 STRAT-12: the pure model of a personal-data EXPORT card — Settings › Profile's "Your data" (the person's own
 * export) and the Export block of Settings › Users › Personal data (an ADMIN exporting a person).
 *
 * Every sentence the cards render lives here (or in `@/types/subjectRights`, verbatim), so a test imports the one
 * string it asserts. Sizes use the W24 card's formatter (`2.1 MB`, the server's unit names); times the app's table
 * stamp (`Sep 21, 11:59`). Everything is React text.
 */
import { formatSize } from '@/pages/settings/projectDataModel'
import {
  ERROR_SENTENCES,
  fillSentence,
  isActiveJob,
  UI_SENTENCES,
  type SubjectJob,
  type SubjectJobList,
} from '@/types/subjectRights'
import { formatCompactDateTime } from '@/utils/formatters'

/** The Profile card's copy (§1.1 verbatim, plus the card's own lines). */
export const MY_DATA_TITLE = UI_SENTENCES.my_data_title
export const MY_DATA_BODY = UI_SENTENCES.my_data_body
export const MY_DATA_START = UI_SENTENCES.my_data_start
/** The muted line under the Profile card: erasure is an administrator's step (§2.3), never self-service. */
export const ERASURE_BY_ADMIN =
  'To have your account erased, ask an administrator. Erasure cannot be undone and keeps audit records, as the law allows.'
/** The card's list read failed (the page binds `useMyExports().error` and hands it to the card). */
export const LOAD_EXPORTS_FAILED = 'Could not load your exports.'
/** The person's job list (Settings › Users) could not be read. */
export const LOAD_JOBS_FAILED = "Could not load this person's exports."
export const DOWNLOAD_LABEL = 'Download'
export const START_FAILED = 'The export could not be started.'
export const DOWNLOAD_FAILED = 'The download failed.'

/** The ADMIN's Export block (Settings › Users › Personal data). */
export const EXPORT_PERSON = "Export this person's data"
/** `export_queued` speaks to the person ("your archive"); the admin's block names no owner. */
export const EXPORT_QUEUED_PERSON = 'Preparing the archive…'

/** A signed archive reads `export_ready` verbatim; an unsigned one says so (never "signed" when it is not). */
const READY_UNSIGNED = UI_SENTENCES.export_ready.replace(', signed.', ', unsigned.')
const READY_NO_EXPIRY = UI_SENTENCES.export_ready.replace(' Available until {expires}.', '')
const READY_UNSIGNED_NO_EXPIRY = READY_UNSIGNED.replace(' Available until {expires}.', '')

export type ExportViewKind = 'none' | 'running' | 'ready' | 'failed' | 'expired'

export interface ExportView {
  kind: ExportViewKind
  /** The status line (React text). */
  line: string
  /** ready: the Download button is offered. */
  download: boolean
  /** The start button ("Download my data" / "Export this person's data") is disabled — a job is running. */
  startDisabled: boolean
}

export type ExportAudience = 'self' | 'person'

function timeOf(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : t
}

/** "Ready — 1.4 MB, signed. Available until Sep 21, 11:59." (unsigned / no expiry: the sentence says so). */
export function readyLine(job: Pick<SubjectJob, 'bytes' | 'signature' | 'expires_at'>): string {
  const size = typeof job.bytes === 'number' && job.bytes >= 0 ? formatSize(job.bytes) : 'size not reported'
  const expiresAt = timeOf(job.expires_at)
  const signed = job.signature === 'signed'
  if (expiresAt === null) return fillSentence(signed ? READY_NO_EXPIRY : READY_UNSIGNED_NO_EXPIRY, { size })
  return fillSentence(signed ? UI_SENTENCES.export_ready : READY_UNSIGNED, {
    size,
    expires: formatCompactDateTime(expiresAt),
  })
}

/**
 * What an export card shows for the newest export (`null`: none yet). A succeeded export past `expires_at`, or one
 * without a download path, reads as expired — never as a dead Download button.
 */
export function exportView(
  job: SubjectJob | null | undefined,
  { audience = 'self', now = Date.now() }: { audience?: ExportAudience; now?: number } = {},
): ExportView {
  const idle = { download: false, startDisabled: false }
  if (!job) return { kind: 'none', line: UI_SENTENCES.export_none, ...idle }
  if (isActiveJob(job)) {
    const line = audience === 'self' ? UI_SENTENCES.export_queued : EXPORT_QUEUED_PERSON
    return { kind: 'running', line, download: false, startDisabled: true }
  }
  if (job.status === 'failed') {
    return { kind: 'failed', line: job.error?.message?.trim() || ERROR_SENTENCES.export_failed, ...idle }
  }
  const expiresAt = timeOf(job.expires_at)
  const ready = job.status === 'succeeded' && Boolean(job.download_path) && (expiresAt === null || now < expiresAt)
  if (!ready) return { kind: 'expired', line: ERROR_SENTENCES.export_expired, ...idle }
  return { kind: 'ready', line: readyLine(job), download: true, startDisabled: false }
}

/** The newest export of a newest-first list: `undefined` while the read is in flight, `null` when there is none. */
export function newestExport(list: SubjectJobList | null | undefined): SubjectJob | null | undefined {
  if (list === undefined) return undefined
  const items = Array.isArray(list?.items) ? list.items : []
  return items.find((job) => job.kind === 'export') ?? null
}

/** The newest erasure of a person's newest-first job list, or null. */
export function newestErasure(list: SubjectJobList | null | undefined): SubjectJob | null {
  const items = Array.isArray(list?.items) ? list.items : []
  return items.find((job) => job.kind === 'erasure') ?? null
}

/**
 * The job a card polls: the one it started (or a 409 `export_running` named), else the newest when it is still
 * queued / running (a reload mid-export resumes it), else none.
 */
export function watchedJobId(startedId: string | null, latest: SubjectJob | null | undefined): string | null {
  if (startedId) return startedId
  return latest && isActiveJob(latest) ? latest.id : null
}
