/**
 * Settings › Expected runs (W23a TRND-03): the page's copy and the PURE form model.
 *
 * A cadence is TestLookup's own EXPECTATION ("a run of <suite | any run> arrives by <time>"): nothing on
 * the page starts, stops or pauses CI. The form keeps what the person typed (`CadenceFormState`); the
 * bodies sent are built here so a test reads exactly what goes on the wire:
 *
 *  - `toCreateBody(form)` — the `POST …/run-cadences` body (a weekly cadence sends no cron, a cron one no
 *    weekdays / time: the server refuses the mix);
 *  - `toPatchBody(form, saved)` — only the fields that CHANGED, `clear_suite` for "any run" (JSON null on
 *    `suite_name` means "keep");
 *  - `validate(form)` — the server's own sentences (`CADENCE_ERRORS`) for what the browser can tell;
 *  - `pauseBody(localDateTime, reason)` — the `until` WITH the browser's offset (a naive value is a 422).
 *
 * Every string a person typed (a suite name, a reason) is rendered as React text by the page.
 */
import { isAxiosError } from 'axios'
import { extractErrorMessage } from '@/services/apiErrors'
import { formatCompactDateTime, formatWeekday } from '@/utils/formatters'
import {
  CADENCE_ERRORS,
  GRACE_DEFAULT,
  GRACE_MAX,
  GRACE_MIN,
  PAUSE_MAX_DAYS,
  PAUSE_REASON_MAX,
  WEEKDAYS,
  type CadenceMode,
  type RunCadence,
  type RunCadenceCreate,
  type RunCadencePatch,
  type RunCadencePause,
  type RunCadenceStatusItem,
  type Weekday,
} from '@/types/runCadence'

// ── copy ────────────────────────────────────────────────────────────────────

export const PAGE_TITLE = 'Expected runs'
export const PAGE_SUBTITLE =
  'When TestLookup expects a run, so a missing one is noticed. Observe only: nothing here starts, stops or pauses your CI.'
export const PROJECT_PROMPT = 'Expected runs belong to one project: pick it to see and set them.'
export const PANEL_TITLE = 'Expected cadences'
export const ADD_LABEL = 'Add expected cadence'
export const LOAD_ERROR = 'Could not load the expected runs.'
export const STATUS_LOAD_ERROR = 'Could not load the last 14 days.'
export const EMPTY_TEXT = 'No expected cadence configured. Add one so a missing run is noticed.'
export const ANY_RUN_LABEL = 'Any run of the project'
export const DELETE_QUESTION = 'Remove this expectation? Its window history goes with it.'
export const PAUSE_HELP = 'No alerts while paused; Trends shows the pause and who set it.'
export const CRON_HELP = 'minute hour * * day-of-week (0 = Sunday), for example 0 6 * * 1-5'
export const SUITE_REQUIRED = 'Type the suite name, or choose "Any run of the project".'
export const GRACE_INVALID = `Grace is a whole number of minutes from ${GRACE_MIN} to ${GRACE_MAX}.`
export const PAUSE_UNTIL_REQUIRED = 'Pick when the pause ends.'
export const STATUS_DAYS_SHOWN = 14
export const WEEKDAY_LABELS: Record<Weekday, string> = {
  mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun',
}
export const WORKING_DAYS: readonly Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri']

// ── the form ────────────────────────────────────────────────────────────────

export interface CadenceFormState {
  scope: 'any' | 'suite'
  suiteName: string
  mode: CadenceMode
  weekdays: Weekday[]
  /** "HH:MM" (an `<input type="time">` value). */
  expectedBy: string
  cron: string
  timezone: string
  /** The number input's text. */
  grace: string
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/

/** Weekdays in Mon … Sun order, de-duplicated, unknown names dropped. */
export function orderedWeekdays(days: readonly string[] | null | undefined): Weekday[] {
  const wanted = new Set((days ?? []).map((d) => String(d).toLowerCase()))
  return WEEKDAYS.filter((d) => wanted.has(d))
}

/** The refusal's sentence: the API's `{code, message}` detail (`extractErrorMessage`), else `fallback`. */
export function refusalMessage(err: unknown, fallback: string): string {
  const detail = isAxiosError(err) ? (err.response?.data as { detail?: unknown } | undefined)?.detail : undefined
  return extractErrorMessage(detail, fallback)
}

/** The browser's IANA zone, or `UTC` when it cannot say. */
export function browserZone(): string {
  try {
    // eslint-disable-next-line no-restricted-properties -- the browser's zone NAME (the select's default), not a date format
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    return typeof zone === 'string' && zone ? zone : 'UTC'
  } catch {
    return 'UTC'
  }
}

/** The zone `<select>` options: `Intl.supportedValuesOf('timeZone')`, else `['UTC', <browser zone>]`; always
 *  with `UTC`, the browser's zone and `current` (a saved zone the list lacks stays selectable). */
export function timeZoneOptions(current?: string | null): string[] {
  let zones: string[] = []
  try {
    const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
    zones = typeof intl.supportedValuesOf === 'function' ? intl.supportedValuesOf('timeZone') : []
  } catch {
    zones = []
  }
  const extra = ['UTC', browserZone(), ...(current ? [current] : [])]
  return Array.from(new Set([...extra.filter((z) => !zones.includes(z)), ...zones]))
}

export function emptyForm(zone: string = browserZone()): CadenceFormState {
  return {
    scope: 'any', suiteName: '', mode: 'weekly', weekdays: [], expectedBy: '', cron: '',
    timezone: zone, grace: String(GRACE_DEFAULT),
  }
}

/** The form for an existing cadence (Edit). */
export function fromCadence(c: RunCadence): CadenceFormState {
  const mode: CadenceMode = c.mode === 'cron' ? 'cron' : 'weekly'
  return {
    scope: c.suite_name ? 'suite' : 'any',
    suiteName: c.suite_name ?? '',
    mode,
    weekdays: orderedWeekdays(c.weekdays),
    expectedBy: c.expected_by ?? '',
    cron: c.cron ?? '',
    timezone: c.timezone ?? 'UTC',
    grace: String(c.grace_minutes ?? GRACE_DEFAULT),
  }
}

function normalisedCron(text: string): string {
  return text.trim().split(/\s+/).filter(Boolean).join(' ')
}

/** The first problem the browser can tell, as the server would say it; `null` when the form may be sent. */
export function validate(form: CadenceFormState): string | null {
  if (form.scope === 'suite' && !form.suiteName.trim()) return SUITE_REQUIRED
  if (form.mode === 'weekly') {
    if (form.weekdays.length === 0 || !HHMM.test(form.expectedBy)) return CADENCE_ERRORS.weekly_fields
  } else if (!normalisedCron(form.cron)) {
    return CADENCE_ERRORS.cron_fields
  }
  if (!form.timezone.trim()) return CADENCE_ERRORS.unknown_timezone
  const grace = form.grace.trim()
  if (!/^\d+$/.test(grace) || Number(grace) < GRACE_MIN || Number(grace) > GRACE_MAX) return GRACE_INVALID
  return null
}

/** The `POST …/run-cadences` body. */
export function toCreateBody(form: CadenceFormState): RunCadenceCreate {
  const base = {
    suite_name: form.scope === 'suite' ? form.suiteName.trim() : null,
    mode: form.mode,
  }
  const when = form.mode === 'weekly'
    ? { weekdays: orderedWeekdays(form.weekdays), expected_by: form.expectedBy }
    : { cron: normalisedCron(form.cron) }
  return { ...base, ...when, timezone: form.timezone, grace_minutes: Number(form.grace.trim()) }
}

/** The `PATCH …/run-cadences/{id}` body: only what changed (`{}` = nothing to send). */
export function toPatchBody(form: CadenceFormState, saved: RunCadence): RunCadencePatch {
  const body: RunCadencePatch = {}
  const was = fromCadence(saved)
  const suite = form.scope === 'suite' ? form.suiteName.trim() : ''
  if (!suite && was.scope === 'suite') body.clear_suite = true
  else if (suite && suite !== was.suiteName) body.suite_name = suite
  const days = orderedWeekdays(form.weekdays)
  if (form.mode !== was.mode) {
    body.mode = form.mode
    if (form.mode === 'weekly') Object.assign(body, { weekdays: days, expected_by: form.expectedBy })
    else body.cron = normalisedCron(form.cron)
  } else if (form.mode === 'weekly') {
    if (days.join(',') !== was.weekdays.join(',')) body.weekdays = days
    if (form.expectedBy !== was.expectedBy) body.expected_by = form.expectedBy
  } else if (normalisedCron(form.cron) !== normalisedCron(was.cron)) {
    body.cron = normalisedCron(form.cron)
  }
  if (form.timezone !== was.timezone) body.timezone = form.timezone
  const grace = Number(form.grace.trim())
  if (grace !== Number(was.grace)) body.grace_minutes = grace
  return body
}

// ── pause ───────────────────────────────────────────────────────────────────

const LOCAL_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

function pad2(n: number): string {
  return String(Math.trunc(n)).padStart(2, '0')
}

/** A `datetime-local` value as ISO 8601 WITH the browser's offset at that instant; `null` if unreadable. */
export function localIsoWithOffset(local: string): string | null {
  const m = LOCAL_DATETIME.exec(local.trim())
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s ?? 0))
  if (Number.isNaN(date.getTime())) return null
  const east = -date.getTimezoneOffset()
  const sign = east >= 0 ? '+' : '-'
  const abs = Math.abs(east)
  return `${y}-${mo}-${d}T${h}:${mi}:${s ?? '00'}${sign}${pad2(abs / 60)}:${pad2(abs % 60)}`
}

/** The `POST …/pause` body: `until` with the browser's offset, the reason trimmed. */
export function pauseBody(localDateTime: string, reason: string): RunCadencePause {
  return { until: localIsoWithOffset(localDateTime) ?? localDateTime, reason: reason.trim() }
}

/** The pause form's first problem, in the server's words; `null` when it may be sent. */
export function validatePause(localDateTime: string, reason: string, now: Date): string | null {
  const iso = localIsoWithOffset(localDateTime)
  if (!iso) return PAUSE_UNTIL_REQUIRED
  const until = Date.parse(iso)
  if (until <= now.getTime()) return CADENCE_ERRORS.pause_until_past
  if (until > now.getTime() + PAUSE_MAX_DAYS * 86_400_000) return CADENCE_ERRORS.pause_too_long
  const text = reason.trim()
  if (!text || text.length > PAUSE_REASON_MAX) return CADENCE_ERRORS.pause_reason_required
  return null
}

// ── the table's lines ───────────────────────────────────────────────────────

/**
 * "Fri Oct 30, 23:59" — the pause's end in the BROWSER's zone, through the app's one date format
 * (`utils/formatters`): the person picked it in their own local time (`datetime-local`), so it reads back
 * the same way. "" for an unreadable instant.
 */
export function untilLabel(iso: string | null | undefined): string {
  if (!iso || Number.isNaN(Date.parse(iso))) return ''
  return `${formatWeekday(iso, 'short')} ${formatCompactDateTime(iso)}`
}

export interface CadenceState {
  paused: boolean
  /** "Active" | "Paused". */
  label: string
  /** Under the pill: "until Fri Oct 30, 23:59", the reason, "set by Alice Lead". */
  lines: string[]
}

/** The State column of one cadence. A pause that ran out reads Active (the server's `paused`). */
export function stateLines(c: RunCadence): CadenceState {
  if (!c.paused) return { paused: false, label: 'Active', lines: [] }
  const until = untilLabel(c.paused_until)
  const lines = [until ? `until ${until}` : '', c.paused_reason ?? '', c.paused_by_name ? `set by ${c.paused_by_name}` : '']
  return { paused: true, label: 'Paused', lines: lines.filter(Boolean) }
}

/** "Last 14 days: 12 met · 2 missed (Tue, Wed)" from the page's own status read. */
export function statusLine(item: RunCadenceStatusItem | null | undefined, days: number = STATUS_DAYS_SHOWN): string {
  if (!item || !item.measured) return `No expected run in the last ${days} days yet`
  const missed = item.missed ?? 0
  const labels = [...(item.missed_windows ?? [])]
    .sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline))
    .map((w) => (w.local_label ?? '').trim().split(/\s+/)[0])
    .filter(Boolean)
  const shown = labels.slice(0, 5)
  const more = missed - shown.length
  const which = shown.length ? ` (${shown.join(', ')}${more > 0 ? `, and ${more} more` : ''})` : ''
  const paused = item.paused ? ` · ${item.paused} paused` : ''
  return `Last ${days} days: ${item.met ?? 0} met · ${missed} missed${missed ? which : ''}${paused}`
}
