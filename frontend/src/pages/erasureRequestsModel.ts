/**
 * W38 (W31 F-1, contract §4): the pure model of Settings › Users › Erasure requests — the administrators' queue of
 * people who asked to be erased.
 *
 * A pending row offers "Review" (the person's existing Personal data section opens under the row; its erasure POST
 * IS the approval, and the server closes the request in the same commit, D-5) and "Decline" (one of the four fixed
 * codes, never free text, D-2). The signed-in administrator's OWN request offers nothing but the `own_request` note.
 * Every name and sentence is React text; the person's display comes from the server ("Erased user" once erased).
 */
import type { UserRowLike } from '@/components/subjectRights/erasureModel'
import {
  DECLINE_CODES,
  DECLINE_SENTENCES,
  ERASED_DISPLAY,
  ERASURE_REQUEST_FILTERS,
  ERROR_SENTENCES,
  UI_SENTENCES,
  type DeclineCode,
  type ErasureRequestFilter,
  type ErasureRequestRead,
} from '@/types/subjectRights'
import { formatCompactDateTime } from '@/utils/formatters'

// ── Copy ─────────────────────────────────────────────────────────────────────

export const TAB_LABEL = 'Erasure requests'
export const FILTER_GROUP_LABEL = 'Show erasure requests'
export const FILTER_LABELS: Record<ErasureRequestFilter, string> = { pending: 'Pending', decided: 'Decided', all: 'All' }
export const FILTERS: readonly ErasureRequestFilter[] = ERASURE_REQUEST_FILTERS
/** The tab opens on the requests that wait for a decision. */
export const DEFAULT_FILTER: ErasureRequestFilter = 'pending'
export const REVIEW = 'Review'
export const DECLINE = 'Decline'
export const DECLINE_REQUEST = 'Decline request'
export const DECLINE_REASON_LABEL = 'Reason'
export const DECLINE_REASON_PLACEHOLDER = 'Choose a reason'
export const DECLINE_FAILED = 'The request could not be declined.'
export const LOAD_FAILED = 'Could not load erasure requests.'
export const OWN_REQUEST_NOTE = ERROR_SENTENCES.own_request
export const ACCEPTED_LINE = UI_SENTENCES.erasure_request_accepted
export const EMPTY_LINES: Record<ErasureRequestFilter, string> = {
  pending: 'No erasure requests are waiting for review.',
  decided: 'No decided erasure requests yet.',
  all: 'Nobody has asked for erasure yet.',
}

/** The administrator's short name for each fixed decline code (the select's options; the status column). */
export const DECLINE_CODE_LABELS: Record<DeclineCode, string> = {
  legal_obligation: 'Legal obligation',
  active_contract: 'Work in progress',
  identity_unconfirmed: 'Identity not confirmed',
  other: 'Other',
}

export const DECLINE_OPTIONS: readonly { code: DeclineCode; label: string }[] = DECLINE_CODES.map((code) => ({
  code,
  label: DECLINE_CODE_LABELS[code],
}))

/** Under the reason select: exactly what the person will read on their Profile (DECLINE_SENTENCES). */
export const declinePreview = (code: DeclineCode) => `They will read: “${DECLINE_SENTENCES[code]}”`

/** The Review section is the person's Personal data section; its heading names them. */
export const reviewLabel = (row: Pick<ErasureRequestRead, 'subject_display' | 'subject_username'>) =>
  `Review the erasure request of ${personName(row)}`
export const declineLabel = (row: Pick<ErasureRequestRead, 'subject_display' | 'subject_username'>) =>
  `Decline the erasure request of ${personName(row)}`

// ── A row ────────────────────────────────────────────────────────────────────

function isDeclineCode(code: string | null | undefined): code is DeclineCode {
  return typeof code === 'string' && (DECLINE_CODES as readonly string[]).includes(code)
}

/** The person's name: the server's display ("Erased user" once erased), else the username. */
export function personName(row: Pick<ErasureRequestRead, 'subject_display' | 'subject_username'>): string {
  return row.subject_display?.trim() || row.subject_username?.trim() || ERASED_DISPLAY
}

/** The Status cell: Pending / "Accepted — erasure started" / "Declined — <reason>" / Withdrawn. */
export function statusLabel(row: Pick<ErasureRequestRead, 'status' | 'decline_code'>): string {
  switch (row.status) {
    case 'pending':
      return 'Pending'
    case 'accepted':
      return ACCEPTED_LINE
    case 'declined':
      return `Declined — ${isDeclineCode(row.decline_code) ? DECLINE_CODE_LABELS[row.decline_code] : DECLINE_CODE_LABELS.other}`
    case 'withdrawn':
      return 'Withdrawn'
    default:
      return row.status
  }
}

/** The Decided cell: "Sep 19, 10:00 by Morgan Admin" (a withdrawal has no decider), "—" while pending. */
export function decidedLabel(row: Pick<ErasureRequestRead, 'decided_at' | 'decided_by_display'>): string {
  if (!row.decided_at) return '—'
  const by = row.decided_by_display?.trim()
  return by ? `${formatCompactDateTime(row.decided_at)} by ${by}` : formatCompactDateTime(row.decided_at)
}

export type RowActions = 'review' | 'own' | 'none'

/** A pending row offers Review + Decline, except the reader's OWN request (the note only); a decided row nothing. */
export function rowActions(row: Pick<ErasureRequestRead, 'status' | 'requester_is_you'>): RowActions {
  if (row.status !== 'pending') return 'none'
  return row.requester_is_you ? 'own' : 'review'
}

/** The person as the Personal data section reads a Users row (the same section, reused — no second erase UI). */
export function asUserRow(row: ErasureRequestRead): UserRowLike {
  return {
    id: row.subject_user_id,
    username: row.subject_username ?? '',
    full_name: row.subject_display,
    is_erased: row.subject_is_erased,
  }
}

/**
 * The rows to draw: the server's, plus the row whose section is OPEN when a re-read dropped it (an erasure from the
 * Pending filter moves the request to Decided at once, D-5 — the section and its phase line stay until closed). A
 * kept row whose Review started the erasure reads "Accepted — erasure started".
 */
export function withOpenRow(
  rows: readonly ErasureRequestRead[],
  open: ErasureRequestRead | null,
  erasedFromReview: ReadonlySet<string>,
): ErasureRequestRead[] {
  if (!open || rows.some((row) => row.id === open.id)) return [...rows]
  const kept = erasedFromReview.has(open.id) ? { ...open, status: 'accepted' } : open
  return [kept, ...rows]
}
