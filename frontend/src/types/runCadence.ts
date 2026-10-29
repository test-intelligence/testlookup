/**
 * W23a TRND-03: expected run cadences — the wire vocabulary, the shapes, their defensive parser and the
 * pure copy helpers.
 *
 * A cadence is TestLookup's own EXPECTATION ("a run of <suite | any> arrives by <time>"); nothing here
 * starts, stops or pauses CI. The constants mirror `backend/app/services/run_cadence_vocab.py` value for
 * value (pinned by `backend/tests/test_insight_cadence_vocab_frontend_parity.py`, which reads this file as
 * TEXT — keep every constant a plain literal on its own `export const` line, strings in one piece, one
 * `key: 'value',` per record line). Enum-like fields are plain strings on the wire.
 *
 * Every suite name, reason, person and sentence below is rendered as React text, never as HTML.
 */

export const CADENCE_MODES = ['weekly', 'cron'] as const
export type CadenceMode = (typeof CADENCE_MODES)[number]

/** Bit i of the stored mask = WEEKDAYS[i] (Mon = bit 0). */
export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
export type Weekday = (typeof WEEKDAYS)[number]

/** What the beat DECIDED for a window (`run_cadence_windows.outcome`). */
export const WINDOW_OUTCOMES = ['met', 'missed', 'paused'] as const
export type WindowOutcome = (typeof WINDOW_OUTCOMES)[number]

/** `/status` adds "pending" (the deadline + grace is not reached yet; never stored). */
export const STATUS_OUTCOMES = ['met', 'missed', 'paused', 'pending'] as const
export type StatusOutcome = (typeof STATUS_OUTCOMES)[number]

/** Minutes. */
export const GRACE_DEFAULT = 60
export const GRACE_MIN = 0
export const GRACE_MAX = 720
export const PAUSE_MAX_DAYS = 90
export const PAUSE_REASON_MAX = 200

/** `GET …/run-cadences/status?days=` (the Trends / Coverage windows snap onto these). */
export const STATUS_DAYS = [1, 7, 14, 30, 60, 90] as const
export type StatusDays = (typeof STATUS_DAYS)[number]

export const NO_CADENCE_SENTENCE = 'No expected cadence configured'
export const CONFIGURE_LABEL = 'Configure'

/** detail = {code, message} on every cadence 4xx (`extractErrorMessage` reads the message). */
export const CADENCE_ERRORS = {
  cadence_not_found: 'Expected cadence not found.',
  duplicate_scope: 'This project already has an expected cadence for that suite.',
  too_many: 'A project can have at most 20 expected cadences.',
  unknown_mode: 'Unknown cadence mode. Valid: weekly, cron.',
  weekly_fields: 'A weekly cadence needs at least one weekday and an expected-by time.',
  cron_fields: 'A cron cadence needs a cron expression and no weekdays or time.',
  bad_cron: 'That cron expression could not be read. Use five fields: minute hour * * day-of-week.',
  cron_day_fields: 'Only minute, hour and day-of-week are supported; day-of-month and month must be *.',
  cron_too_frequent: 'That cron expression expects more than 24 runs a day.',
  unknown_timezone: 'Unknown time zone. Use an IANA name such as Europe/Berlin.',
  pause_until_past: 'The pause must end in the future.',
  pause_too_long: 'A pause can last at most 90 days.',
  pause_reason_required: 'Say why the expectation is paused.',
  not_paused: 'This expected cadence is not paused.',
  suite_conflict: 'Send suite_name or clear_suite, not both.',
} as const
export type CadenceErrorCode = keyof typeof CADENCE_ERRORS

// ── Shapes (every field beyond a row's identity is OPTIONAL, so a fixture written today type-checks when
// the shape grows; the parser fills the defaults) ──────────────────────────────────────────────────────

/** `RunCadenceRead`. */
export interface RunCadence {
  id: string
  project_id?: string
  /** null = any run of the project. */
  suite_name?: string | null
  mode?: CadenceMode | string
  weekdays?: (Weekday | string)[] | null
  /** "HH:MM", local to `timezone`. */
  expected_by?: string | null
  cron?: string | null
  timezone?: string
  grace_minutes?: number
  /** "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min" (server-built). */
  summary?: string
  effective_from?: string
  paused?: boolean
  paused_at?: string | null
  paused_until?: string | null
  paused_reason?: string | null
  paused_by_name?: string | null
  next_deadline?: string | null
  created_by_name?: string | null
  created_at?: string
  updated_at?: string
}

/** `GET /api/v1/projects/{project_id}/run-cadences` (`RunCadenceList`). */
export interface RunCadenceList {
  items: RunCadence[]
  total?: number
}

/** `POST …/run-cadences` (`RunCadenceCreate`). */
export interface RunCadenceCreate {
  suite_name?: string | null
  mode?: CadenceMode
  weekdays?: Weekday[] | null
  expected_by?: string | null
  cron?: string | null
  timezone: string
  grace_minutes?: number
}

/** `PATCH …/run-cadences/{cadence_id}` (`RunCadencePatch`): only the fields sent change. */
export interface RunCadencePatch {
  suite_name?: string | null
  mode?: CadenceMode
  weekdays?: Weekday[] | null
  expected_by?: string | null
  cron?: string | null
  timezone?: string
  grace_minutes?: number
  /** true -> any run (suite_name NULL); never together with a suite_name. */
  clear_suite?: boolean
}

/** `POST …/run-cadences/{cadence_id}/pause` (`RunCadencePauseRequest`). */
export interface RunCadencePause {
  /** ISO 8601 WITH an offset (a naive value is refused). */
  until: string
  reason: string
}

/** One expected window (`CadenceWindowStatus`). */
export interface CadenceWindow {
  deadline: string
  window_start?: string
  window_end?: string
  outcome: StatusOutcome | string
  /** true = the beat decided it; false = computed now. */
  recorded?: boolean
  run_count?: number | null
  /** "Wed 28 Oct" in the cadence's zone. */
  local_label?: string
}

/** `RunCadenceStatusItem`. */
export interface RunCadenceStatusItem {
  cadence: RunCadence
  /** false only when nothing could be judged (`reason` says so) — never "all met". */
  measured?: boolean
  reason?: string | null
  /** expected = met + missed (paused / pending excluded). */
  expected?: number
  met?: number
  missed?: number
  paused?: number
  pending?: number
  /** Newest first, at most 50; `missed` is the full count. */
  missed_windows?: CadenceWindow[]
  last_run_at?: string | null
}

/** `GET /api/v1/projects/{project_id}/run-cadences/status` (`RunCadenceStatusResponse`). */
export interface RunCadenceStatus {
  /** false = the project has no cadence (`NO_CADENCE_SENTENCE`). */
  configured: boolean
  days?: number
  window_start?: string
  window_end?: string
  items: RunCadenceStatusItem[]
  total?: number
  expected?: number
  met?: number
  missed?: number
  paused?: number
}

// ── guards ──────────────────────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function num(v: unknown): number {
  return isNum(v) ? v : 0
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

/** One `RunCadenceRead`, defaults filled; `null` when it has no id (malformed). */
export function parseCadence(raw: unknown): RunCadence | null {
  if (!isRecord(raw) || typeof raw.id !== 'string') return null
  return {
    id: raw.id,
    project_id: str(raw.project_id),
    suite_name: strOrNull(raw.suite_name),
    mode: str(raw.mode, 'weekly'),
    weekdays: Array.isArray(raw.weekdays) ? raw.weekdays.filter((d): d is string => typeof d === 'string') : null,
    expected_by: strOrNull(raw.expected_by),
    cron: strOrNull(raw.cron),
    timezone: str(raw.timezone, 'UTC'),
    grace_minutes: isNum(raw.grace_minutes) ? raw.grace_minutes : GRACE_DEFAULT,
    summary: str(raw.summary),
    effective_from: str(raw.effective_from),
    paused: raw.paused === true,
    paused_at: strOrNull(raw.paused_at),
    paused_until: strOrNull(raw.paused_until),
    paused_reason: strOrNull(raw.paused_reason),
    paused_by_name: strOrNull(raw.paused_by_name),
    next_deadline: strOrNull(raw.next_deadline),
    created_by_name: strOrNull(raw.created_by_name),
    created_at: str(raw.created_at),
    updated_at: str(raw.updated_at),
  }
}

/** `undefined` = malformed. */
function parseWindow(raw: unknown): CadenceWindow | undefined {
  if (!isRecord(raw) || typeof raw.deadline !== 'string' || typeof raw.outcome !== 'string') return undefined
  return {
    deadline: raw.deadline,
    window_start: str(raw.window_start),
    window_end: str(raw.window_end),
    outcome: raw.outcome,
    recorded: raw.recorded === true,
    run_count: isNum(raw.run_count) ? raw.run_count : null,
    local_label: str(raw.local_label),
  }
}

/** `undefined` = malformed. */
function parseStatusItem(raw: unknown): RunCadenceStatusItem | undefined {
  if (!isRecord(raw)) return undefined
  const cadence = parseCadence(raw.cadence)
  if (!cadence) return undefined
  if (raw.missed_windows !== undefined && raw.missed_windows !== null && !Array.isArray(raw.missed_windows)) return undefined
  const windows: CadenceWindow[] = []
  for (const w of Array.isArray(raw.missed_windows) ? raw.missed_windows : []) {
    const parsed = parseWindow(w)
    if (!parsed) return undefined
    windows.push(parsed)
  }
  return {
    cadence,
    measured: raw.measured === true,
    reason: strOrNull(raw.reason),
    expected: num(raw.expected),
    met: num(raw.met),
    missed: num(raw.missed),
    paused: num(raw.paused),
    pending: num(raw.pending),
    missed_windows: windows,
    last_run_at: strOrNull(raw.last_run_at),
  }
}

/**
 * The defensive guard over `GET /api/v1/projects/{project_id}/run-cadences/status` (the
 * `parseTrendSummary` precedent). Returns `null` for anything that is not a status answer (the reader
 * then says "Could not load the expected runs." and the rest of the page is unchanged); missing optional
 * fields are defaulted (counts 0, sums over the items). `configured: false` is the honest "No expected
 * cadence configured" — never "all met".
 */
export function parseCadenceStatus(raw: unknown): RunCadenceStatus | null {
  if (!isRecord(raw) || !Array.isArray(raw.items)) return null
  if (raw.configured !== undefined && typeof raw.configured !== 'boolean') return null
  const items: RunCadenceStatusItem[] = []
  for (const item of raw.items) {
    const parsed = parseStatusItem(item)
    if (!parsed) return null
    items.push(parsed)
  }
  const sum = (key: 'expected' | 'met' | 'missed' | 'paused') =>
    isNum(raw[key]) ? (raw[key] as number) : items.reduce((acc, it) => acc + (it[key] ?? 0), 0)
  return {
    configured: typeof raw.configured === 'boolean' ? raw.configured : items.length > 0,
    days: isNum(raw.days) ? raw.days : 14,
    window_start: str(raw.window_start),
    window_end: str(raw.window_end),
    items,
    total: isNum(raw.total) ? raw.total : items.length,
    expected: sum('expected'),
    met: sum('met'),
    missed: sum('missed'),
    paused: sum('paused'),
  }
}

// ── copy helpers (pure) ─────────────────────────────────────────────────────

function dayLabel(day: string): string {
  return day.charAt(0).toUpperCase() + day.slice(1)
}

/**
 * The weekdays of a cadence as people say them: "Every day" (all seven), "Mon–Fri" (one run of three or
 * more consecutive days), else "Mon, Wed, Fri". Unknown names are ignored; order and duplicates do not
 * matter. No day → "".
 */
export function weekdaysLabel(names: readonly string[] | null | undefined): string {
  const indexes = Array.from(
    new Set((names ?? []).map((n) => (WEEKDAYS as readonly string[]).indexOf(String(n).toLowerCase()))),
  )
    .filter((i) => i >= 0)
    .sort((a, b) => a - b)
  if (indexes.length === 0) return ''
  if (indexes.length === WEEKDAYS.length) return 'Every day'
  const contiguous = indexes.every((i, k) => k === 0 || i === indexes[k - 1] + 1)
  if (contiguous && indexes.length >= 3) {
    return `${dayLabel(WEEKDAYS[indexes[0]])}–${dayLabel(WEEKDAYS[indexes[indexes.length - 1]])}`
  }
  return indexes.map((i) => dayLabel(WEEKDAYS[i])).join(', ')
}

const SHORT_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const
const MISSED_LABELS_SHOWN = 5

/** The short day of a missed window: the first word of its zone-local label, else the UTC weekday. */
function missedDay(w: CadenceWindow): string {
  const first = (w.local_label ?? '').trim().split(/\s+/)[0]
  if (first) return first
  const d = new Date(w.deadline)
  return Number.isNaN(d.getTime()) ? '' : SHORT_DAYS[d.getUTCDay()]
}

/**
 * "2 expected runs missed (Tue, Wed)" / "1 expected run missed (Wed)"; more than five days →
 * "8 expected runs missed (Mon, Tue, Wed, Thu, Fri, and 3 more)". The count is `missed` (never capped);
 * the days are the missed windows oldest first. No miss → "".
 */
export function missedLine(item: Pick<RunCadenceStatusItem, 'missed' | 'missed_windows'>): string {
  const count = item.missed ?? 0
  if (count <= 0) return ''
  const days = [...(item.missed_windows ?? [])]
    .sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline))
    .map(missedDay)
    .filter(Boolean)
  const head = `${count} expected ${count === 1 ? 'run' : 'runs'} missed`
  if (days.length === 0) return head
  const shown = days.slice(0, MISSED_LABELS_SHOWN)
  const rest = count - shown.length
  return rest > 0 ? `${head} (${shown.join(', ')}, and ${rest} more)` : `${head} (${shown.join(', ')})`
}
