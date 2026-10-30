/**
 * Settings › AI › Review gate (W23c AGT-10): the page's words and its pure helpers.
 *
 * The page's copy lives here so a test imports ONE string; the server's sentences (`GATE_SENTENCES`) are the
 * mirror in `types/reviewGate.ts` (pinned against the backend vocabulary by the parity test). Nothing here
 * reads the network or the clock: `now` is passed in.
 *
 *  - `stateLines(gate)` — the state card's sentences: `GATE_SENTENCES[source]` filled with the server's counts;
 *    `measured: false` → the lead of the sentence + "Not measured: …", and NO digit (absence is not health).
 *  - `channelRows(gate)` — "Would have been withheld, by channel": the labels from the answer's `channels`, the
 *    counts from `by_channel` (every channel the server returned, in its order; never invented, never top-N).
 *  - `toOffsetIso(local)` — a `datetime-local` value as ISO 8601 WITH the browser's offset at that instant (the
 *    server refuses a naive value with 422 `naive_datetime`); `isPast(local, now)` — a past or now instant
 *    enforces at once, so the page asks inline first.
 *  - `formatInstant(iso)` — "Nov 2, 2026, 09:00 (UTC+01:00)", the viewer's local time with its offset.
 */
import { isAxiosError } from 'axios'
import { extractErrorMessage } from '@/services/apiErrors'
import { GATE_SENTENCES, type ReviewGateRead } from '@/types/reviewGate'
import { formatDay, formatNumber } from '@/utils/formatters'

export const PAGE_TITLE = 'Review gate'
export const PAGE_SUBTITLE =
  'When unreviewed AI reports stop leaving TestLookup. Until a date is set nothing is withheld; would-be refusals are only counted.'
export const STATE_TITLE = 'Current state'
export const DATE_TITLE = 'Enforcement date'
export const CHANNELS_TITLE = 'Gated channels'
export const BADGE_AUDIT_ONLY = 'Audit only'
export const BADGE_ENFORCED = 'Enforced'
export const BY_CHANNEL_TITLE = 'Would have been withheld, by channel'
export const NONE_IN_WINDOW = 'None in the last 30 days.'
export const ENFORCE_FROM_LABEL = 'Enforce from'
export const SET_DATE_LABEL = 'Set date'
export const CLEAR_DATE_LABEL = 'Clear date'
export const ENFORCE_NOW_LABEL = 'Enforce now'
export const DATE_REQUIRED = 'Pick the date and time the gate starts.'
export const SAVE_ERROR = 'The review gate could not be saved.'
export const ALSO_CHANGES =
  'Also changes: the release readiness value CI reads (GO / NO_GO → PENDING_REVIEW) and the release verdict quoted in reports.'
export const PROPAGATION = 'Takes effect on every server within 30 seconds.'
export const ADMIN_ONLY = 'Only administrators can change the review gate.'
export const LOAD_ERROR = 'Could not load the review gate.'
export const WINDOW_DAYS = 30

/**
 * A refused save, in the server's words: the `{code, message}` detail (403 `interactive_only`, 422
 * `naive_datetime`) reads as its sentence — never "Request failed with status code 403".
 */
export function saveRefusal(err: unknown): string {
  const detail = isAxiosError(err) ? (err.response?.data as { detail?: unknown } | undefined)?.detail : undefined
  return extractErrorMessage(detail, SAVE_ERROR)
}

/** `{name}` placeholders filled from `values`; an unknown name stays as written. */
export function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in values ? values[name] : whole))
}

/** The part of a sentence before its counts ("Audit only — nothing is withheld."): what stays when nothing was measured. */
function leadBeforeCounts(template: string, values: Record<string, string>): string {
  const cut = template.indexOf('{runs}')
  const lead = cut < 0 ? template : template.slice(0, cut)
  return fill(lead, values).trim()
}

function pad2(n: number): string {
  return String(Math.trunc(n)).padStart(2, '0')
}

/** "UTC+01:00" — the browser's offset at that instant (DST-aware: it is read for the instant, not for today). */
export function offsetLabel(date: Date): string {
  const east = -date.getTimezoneOffset()
  const abs = Math.abs(east)
  return `UTC${east >= 0 ? '+' : '-'}${pad2(abs / 60)}:${pad2(abs % 60)}`
}

/** "Nov 2, 2026, 09:00 (UTC+01:00)" in the viewer's local time; "" for a missing or unreadable instant. */
export function formatInstant(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${formatDay(date)}, ${pad2(date.getHours())}:${pad2(date.getMinutes())} (${offsetLabel(date)})`
}

/** The browser's IANA zone name, or `UTC` when it cannot say. */
export function browserZone(): string {
  try {
    // eslint-disable-next-line no-restricted-properties -- the browser's zone NAME for the hint line, not a date format
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    return typeof zone === 'string' && zone ? zone : 'UTC'
  } catch {
    return 'UTC'
  }
}

/** "Your time zone: Europe/Berlin (UTC+01:00). Stored as UTC." */
export function zoneLine(now: Date, zone: string = browserZone()): string {
  return `Your time zone: ${zone} (${offsetLabel(now)}). Stored as UTC.`
}

const LOCAL_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

/** A `datetime-local` value as ISO 8601 WITH the browser's offset at that instant; `null` when unreadable. */
export function toOffsetIso(local: string): string | null {
  const m = LOCAL_DATETIME.exec(local.trim())
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s ?? 0))
  if (Number.isNaN(date.getTime()) || date.getMonth() !== Number(mo) - 1) return null
  const offset = offsetLabel(date).slice(3)
  return `${y}-${mo}-${d}T${h}:${mi}:${s ?? '00'}${offset}`
}

/** A past (or this very) instant: saving it enforces the gate at once. `false` when unreadable. */
export function isPast(local: string, now: Date): boolean {
  const iso = toOffsetIso(local)
  return iso !== null && Date.parse(iso) <= now.getTime()
}

/** The stored instant as a `datetime-local` value in the viewer's time ("2026-11-02T09:00"); "" when unset. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

// ── the state card ──────────────────────────────────────────────────────────

/** The badge: what the gate does right now (neutral tokens — no status hue). */
export function badgeLabel(gate: Pick<ReviewGateRead, 'enforced'>): string {
  return gate.enforced ? BADGE_ENFORCED : BADGE_AUDIT_ONLY
}

/**
 * The state card's sentences, the server's words filled with the server's numbers. `env` and `date` carry no
 * count; `unset` / `scheduled` carry the 30-day would-refuse counts — or, when the audit read failed, only the
 * lead of the sentence and "Not measured: …" (no digit: an unread audit log is not zero refusals).
 */
export function stateLines(gate: ReviewGateRead): string[] {
  const source = gate.source
  const date = formatInstant(gate.enforced_from)
  if (source === 'env') return [GATE_SENTENCES.env]
  if (source === 'date') return [fill(GATE_SENTENCES.date, { date })]
  const template = source === 'scheduled' ? GATE_SENTENCES.scheduled : GATE_SENTENCES.unset
  const summary = gate.would_refuse
  if (!summary?.measured || summary.runs == null || summary.decisions == null) {
    return [leadBeforeCounts(template, { date }), GATE_SENTENCES.not_measured]
  }
  let line = fill(template, { date, runs: formatNumber(summary.runs), decisions: formatNumber(summary.decisions) })
  // The shared template is plural; one run or one attempt reads in the singular (W23c demo: "1 runs' AI reports").
  if (summary.runs === 1) line = line.replace(" 1 runs' AI reports", " 1 run's AI reports")
  if (summary.decisions === 1) line = line.replace('(1 distribution attempts)', '(1 distribution attempt)')
  return [line]
}

export interface ChannelRow {
  channel: string
  label: string
  decisions: number
}

/**
 * Every channel the server counted (≥ 1 decision), in its order (decisions desc, then code), labelled from the
 * answer's own `channels`; a code the list lacks keeps its code. `[]` when nothing was measured.
 */
export function channelRows(gate: ReviewGateRead): ChannelRow[] {
  const summary = gate.would_refuse
  if (!summary?.measured || !summary.by_channel) return []
  const labels = new Map((gate.channels ?? []).map((c) => [c.channel, c.label]))
  return summary.by_channel
    .filter((row) => row.decisions > 0)
    .map((row) => ({ channel: row.channel, label: labels.get(row.channel) ?? row.channel, decisions: row.decisions }))
}

/**
 * "Measured since Oct 17, 2026 — the audit log holds less than 30 days." when the server clipped the window to
 * the oldest audit row; `null` for a full 30-day window, an unmeasured answer or no `since`.
 */
export function measuredSinceLine(gate: ReviewGateRead, now: Date): string | null {
  const summary = gate.would_refuse
  if (!summary?.measured || !summary.since) return null
  const since = Date.parse(summary.since)
  if (Number.isNaN(since)) return null
  // A minute of slack: the server's `now - 30 d` and this page's differ by the request's latency.
  if (since <= now.getTime() - WINDOW_DAYS * 86_400_000 + 60_000) return null
  return `Measured since ${formatDay(since)} — the audit log holds less than ${WINDOW_DAYS} days.`
}
