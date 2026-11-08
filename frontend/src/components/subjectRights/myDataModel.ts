/**
 * W31 STRAT-12: the pure model of a personal-data EXPORT card — Settings › Profile's "Your data" (the person's own
 * export) and the Export block of Settings › Users › Personal data (an ADMIN exporting a person). W38: also the
 * Profile card's erasure REQUEST block (`erasureRequestView`) — the person asks, an administrator decides.
 *
 * Every sentence the cards render lives here (or in `@/types/subjectRights`, verbatim), so a test imports the one
 * string it asserts. Sizes use the W24 card's formatter (`2.1 MB`, the server's unit names); times the app's table
 * stamp (`Sep 21, 11:59`). Everything is React text.
 */
import { formatSize } from '@/pages/settings/projectDataModel'
import {
  DECLINE_SENTENCES,
  ERROR_SENTENCES,
  fillSentence,
  isActiveJob,
  UI_SENTENCES,
  type DeclineCode,
  type MyErasureRequest,
  type SubjectJob,
  type SubjectJobList,
} from '@/types/subjectRights'
import { formatCompactDateTime, formatDay } from '@/utils/formatters'

/** The Profile card's copy (§1.1 verbatim, plus the card's own lines). */
export const MY_DATA_TITLE = UI_SENTENCES.my_data_title
export const MY_DATA_BODY = UI_SENTENCES.my_data_body
export const MY_DATA_START = UI_SENTENCES.my_data_start
/** W32 (OR-10 (5)): with an export ready, the start button starts a NEW copy — it says so beside "Download". */
export const MY_DATA_RESTART = UI_SENTENCES.my_data_restart
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
  /** The start button is disabled — a job is running. */
  startDisabled: boolean
  /**
   * The start button's label. The person's own card: "Prepare a new copy" when an export is ready (the Download
   * button beside it fetches that one), else "Download my data". The admin's block: "Export this person's data".
   */
  startLabel: string
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
  const startLabel = audience === 'self' ? MY_DATA_START : EXPORT_PERSON
  const idle = { download: false, startDisabled: false, startLabel }
  if (!job) return { kind: 'none', line: UI_SENTENCES.export_none, ...idle }
  if (isActiveJob(job)) {
    const line = audience === 'self' ? UI_SENTENCES.export_queued : EXPORT_QUEUED_PERSON
    return { kind: 'running', line, download: false, startDisabled: true, startLabel }
  }
  if (job.status === 'failed') {
    return { kind: 'failed', line: job.error?.message?.trim() || ERROR_SENTENCES.export_failed, ...idle }
  }
  const expiresAt = timeOf(job.expires_at)
  const ready = job.status === 'succeeded' && Boolean(job.download_path) && (expiresAt === null || now < expiresAt)
  if (!ready) return { kind: 'expired', line: ERROR_SENTENCES.export_expired, ...idle }
  return {
    kind: 'ready',
    line: readyLine(job),
    download: true,
    startDisabled: false,
    startLabel: audience === 'self' ? MY_DATA_RESTART : EXPORT_PERSON,
  }
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

// ── The erasure request (W38, W31 F-1; contract §4) ──────────────────────────
//
// The Profile card's erasure block: the person ASKS; an administrator reviews the request and runs the existing
// erasure (or declines it with a fixed code). No free text (D-2), no modal, no native confirm dialog.

export const ERASURE_REQUEST_START = UI_SENTENCES.erasure_request_start
export const ERASURE_REQUEST_CONFIRM = UI_SENTENCES.erasure_request_confirm
export const ERASURE_REQUEST_SEND = UI_SENTENCES.erasure_request_send
export const ERASURE_REQUEST_WITHDRAW = UI_SENTENCES.erasure_request_withdraw
/** The withdraw strip's question (the strip's "Withdraw request" is the only sender). */
export const WITHDRAW_CONFIRM = 'Withdraw your erasure request? An administrator will no longer review it.'
export const LOAD_ERASURE_REQUEST_FAILED = 'Could not load your erasure request.'
export const FILE_REQUEST_FAILED = 'The request could not be sent.'
export const WITHDRAW_REQUEST_FAILED = 'The request could not be withdrawn.'

/** `data-erasure-request-state`: the four of §4, plus the read's own loading / error, and a (rare) accepted one. */
export type ErasureRequestViewKind = 'loading' | 'error' | 'none' | 'pending' | 'declined' | 'withdrawn' | 'accepted'

export interface ErasureRequestView {
  kind: ErasureRequestViewKind
  /** The status line (React text), or null (none / withdrawn: the button alone). */
  line: string | null
  /** "Ask to have your account erased" is offered (none / withdrawn / declined, and after a failed read). */
  canAsk: boolean
  /** "Withdraw request" is offered (pending). */
  canWithdraw: boolean
}

/** "You asked on Sep 18, 2026. An administrator will review it." */
export function pendingRequestLine(request: Pick<MyErasureRequest, 'requested_at'>): string {
  return fillSentence(UI_SENTENCES.erasure_request_pending, { date: formatDay(request.requested_at) })
}

/** The person's sentence for a decline: the server's `decline_message`, else the code's own sentence. */
export function declineMessage(request: Pick<MyErasureRequest, 'decline_code' | 'decline_message'>): string {
  const sent = request.decline_message?.trim()
  if (sent) return sent
  const code = request.decline_code ?? ''
  return Object.prototype.hasOwnProperty.call(DECLINE_SENTENCES, code)
    ? DECLINE_SENTENCES[code as DeclineCode]
    : DECLINE_SENTENCES.other
}

/** "Declined on Sep 19, 2026: We must keep your account for a legal obligation. Ask your administrator for details." */
export function declinedRequestLine(
  request: Pick<MyErasureRequest, 'decided_at' | 'decline_code' | 'decline_message'>,
): string {
  return fillSentence(UI_SENTENCES.erasure_request_declined, {
    date: formatDay(request.decided_at),
    message: declineMessage(request),
  })
}

/**
 * What the erasure block shows for my newest request (`undefined` while the read is in flight, `null` when I never
 * asked). A failed read still offers the button: a duplicate is refused by the server (409 `request_pending`).
 */
export function erasureRequestView(request: MyErasureRequest | null | undefined, loadError?: unknown): ErasureRequestView {
  const ask = { canAsk: true, canWithdraw: false }
  if (loadError && !request) return { kind: 'error', line: LOAD_ERASURE_REQUEST_FAILED, ...ask }
  if (request === undefined) return { kind: 'loading', line: null, canAsk: false, canWithdraw: false }
  if (request === null) return { kind: 'none', line: null, ...ask }
  switch (request.status) {
    case 'pending':
      return { kind: 'pending', line: pendingRequestLine(request), canAsk: false, canWithdraw: true }
    case 'declined':
      return { kind: 'declined', line: declinedRequestLine(request), ...ask }
    case 'withdrawn':
      return { kind: 'withdrawn', line: null, ...ask }
    case 'accepted':
      return { kind: 'accepted', line: UI_SENTENCES.erasure_request_accepted, canAsk: false, canWithdraw: false }
    default:
      return { kind: 'none', line: null, ...ask }
  }
}
