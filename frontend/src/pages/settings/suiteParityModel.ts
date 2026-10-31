/**
 * The words and the pure logic of the "Suite membership cutover" card on /settings/storage (W25a HYG-04 phase 1,
 * contract §4.1 / §4.3). Every sentence the card renders lives here, so a test imports one string.
 *
 *   - `statusLine(history)` — ONE line: no comparison yet · comparing · ready for the cutover · day N of 7 · the last
 *     comparison found N differences · did not finish · has not run since; plus the restart line after a break.
 *   - `dayRows(history, now)` — the last 7 UTC dates, newest first: the LAST run of a date decides it; a date with no
 *     run is "Not run" (absence is not health), today before its beat "Not run yet".
 *   - `sampleView(detail)` — the inline differences table (first 50 blocking rows, then "… and N more"), and the
 *     counts per reason, the expected ones as information.
 *
 * Dates and times are UTC (the comparison's days are UTC days), built from fixed English names — never the
 * browser's locale. Counts read in the singular for 1.
 */
import { extractErrorMessage } from '@/services/apiErrors'
import {
  BLOCKING_REASONS,
  EXPECTED_REASONS,
  REASON_LABEL,
  REQUIRED_CLEAN_DAYS,
  type SuiteParityHistory,
  type SuiteParityRunDetail,
  type SuiteParityRunSummary,
} from '@/types/suiteParity'
import { formatNumber } from '@/utils/formatters'

export const PARITY_TEXT = {
  heading: 'Suite membership cutover',
  explainer:
    'Compares the old suite-membership tables with the test catalog every day at 06:15 UTC. Seven clean days in a ' +
    'row allow the old tables to stop being written.',
  compareNow: 'Compare now',
  comparing: 'Comparing…',
  queued: 'Comparison queued.',
  alreadyRunning: 'A comparison is already running.',
  couldNotQueue: 'Could not queue the comparison.',
  loadError: 'Could not load the cutover comparisons.',
  detailLoadError: 'Could not load the differences of this comparison.',
  noRuns: 'No comparison has run yet.',
  didNotFinish: 'The last comparison did not finish.',
  restart: 'The count restarts at the next clean day.',
  showDifferences: 'Show differences',
  hideDifferences: 'Hide differences',
  dayNoDifferences: 'No differences',
  dayDidNotFinish: 'Did not finish',
  dayNotRun: 'Not run',
  dayNotRunYet: 'Not run yet',
  deletedProject: 'Deleted project',
  columns: { reason: 'Reason', project: 'Project', suite: 'Suite', test: 'Test' },
  daysHeading: 'Last 7 days (UTC)',
} as const

/** Rows the inline differences table shows before "… and N more in this comparison." */
export const SAMPLE_ROWS_SHOWN = 50
/** The day list's length (the last 7 UTC dates). */
export const DAY_ROWS = 7

const DAY_MS = 86_400_000
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const

const pad2 = (n: number) => String(n).padStart(2, '0')

/** `YYYY-MM-DD` of the UTC date of `value`. */
export function utcDateKey(value: Date | string): string {
  return new Date(value).toISOString().slice(0, 10)
}

/** "Fri 31 Oct" — the UTC date, fixed English names (no locale, no "Sept"). */
export function formatUtcDay(value: Date | string): string {
  const d = new Date(value)
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`
}

/** "06:15" — the UTC time of day. */
export function formatUtcTime(value: Date | string): string {
  const d = new Date(value)
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`
}

/** "1 difference" / "1,234 differences". */
export function differencesNoun(count: number): string {
  return `${formatNumber(count)} ${count === 1 ? 'difference' : 'differences'}`
}

/** " · 340 expected" — nothing at 0. */
function expectedSuffix(count: number): string {
  return count > 0 ? ` · ${formatNumber(count)} expected` : ''
}

/** The restart line after a break, with the failure's exception TYPE when the row carries one. */
export function restartLine(errorCode?: string | null): string {
  return errorCode ? `The count restarts at the next clean day (${errorCode}).` : PARITY_TEXT.restart
}

/** The run's `error_code` when the row carries one (the detail does; a summary may in a later server). */
export function runErrorCode(run: SuiteParityRunSummary): string | null {
  const code = 'error_code' in run ? run.error_code : null
  return typeof code === 'string' && code ? code : null
}

// ── The status line ──────────────────────────────────────────────────────────

export type StatusKind = 'none' | 'running' | 'ready' | 'streak' | 'diffs' | 'failed' | 'stale'

export interface StatusLine {
  kind: StatusKind
  text: string
  /** The second line after a break (diffs / failed / a missed day), else null. */
  detail: string | null
}

/**
 * ONE status line for the history (contract §4.1): the newest run running wins; then the streak (ready at 7);
 * at 0 the newest FINISHED run says why — differences, did not finish, or (a clean run followed by a missed day)
 * "has not run since". Never "0 differences" for an empty history.
 */
export function statusLine(history: SuiteParityHistory): StatusLine {
  const runs = history.runs ?? []
  if (runs.length === 0) return { kind: 'none', text: PARITY_TEXT.noRuns, detail: null }
  if (runs[0].status === 'running') {
    return { kind: 'running', text: `${PARITY_TEXT.comparing} (started ${formatUtcTime(runs[0].started_at)})`, detail: null }
  }
  const streak = history.streak_days ?? 0
  const required = history.required_days ?? REQUIRED_CLEAN_DAYS
  if (history.cutover_ready) {
    return { kind: 'ready', text: `Ready for the cutover: ${formatNumber(streak)} clean days in a row.`, detail: null }
  }
  if (streak > 0) return { kind: 'streak', text: `Day ${formatNumber(streak)} of ${required} with no differences.`, detail: null }
  const last = runs[0] // newest first, and not running (above)
  if (last.status === 'diffs') {
    return { kind: 'diffs', text: `The last comparison found ${differencesNoun(last.blocking_diffs)}.`, detail: restartLine() }
  }
  if (last.status === 'clean') {
    return { kind: 'stale', text: `The daily comparison has not run since ${formatUtcDay(last.started_at)}.`, detail: restartLine() }
  }
  return { kind: 'failed', text: PARITY_TEXT.didNotFinish, detail: restartLine(runErrorCode(last)) }
}

// ── The day list ─────────────────────────────────────────────────────────────

export type DayKind = 'clean' | 'diffs' | 'failed' | 'running' | 'not_run' | 'not_run_yet'

export interface DayRow {
  /** `YYYY-MM-DD` (UTC). */
  date: string
  /** "Fri 31 Oct". */
  label: string
  kind: DayKind
  result: string
  /** The deciding run (null on a date with none). */
  run: SuiteParityRunSummary | null
}

/** The last `DAY_ROWS` UTC dates ending at `now`'s, newest first; the LAST run started on a date decides it. */
export function dayRows(history: SuiteParityHistory, now: Date): DayRow[] {
  const lastOfDate = new Map<string, SuiteParityRunSummary>()
  for (const run of history.runs ?? []) {
    const key = utcDateKey(run.started_at)
    const seen = lastOfDate.get(key)
    if (!seen || new Date(seen.started_at).getTime() < new Date(run.started_at).getTime()) lastOfDate.set(key, run)
  }
  const today = utcDateKey(now)
  return Array.from({ length: DAY_ROWS }, (_, i): DayRow => {
    const day = new Date(now.getTime() - i * DAY_MS)
    const date = utcDateKey(day)
    const run = lastOfDate.get(date) ?? null
    const row = { date, label: formatUtcDay(day), run }
    if (!run) {
      return date === today
        ? { ...row, kind: 'not_run_yet', result: PARITY_TEXT.dayNotRunYet }
        : { ...row, kind: 'not_run', result: PARITY_TEXT.dayNotRun }
    }
    if (run.status === 'running') return { ...row, kind: 'running', result: PARITY_TEXT.comparing }
    if (run.status === 'clean') {
      return { ...row, kind: 'clean', result: `${PARITY_TEXT.dayNoDifferences}${expectedSuffix(run.expected_divergences)}` }
    }
    if (run.status === 'diffs') {
      return { ...row, kind: 'diffs', result: `${differencesNoun(run.blocking_diffs)}${expectedSuffix(run.expected_divergences)}` }
    }
    return { ...row, kind: 'failed', result: PARITY_TEXT.dayDidNotFinish }
  })
}

// ── The inline differences ───────────────────────────────────────────────────

export interface SampleLine {
  key: string
  /** The `REASON_LABEL` sentence (the raw reason when a newer server adds one). */
  reason: string
  project: string
  suite: string
  test: string
}

export interface SampleView {
  rows: SampleLine[]
  /** "… and 1,194 more in this comparison." — the differences of the run not listed; null when all are. */
  moreLine: string | null
  /** "By reason: Missing from the catalog 10 · Name differs 2." — null with no difference. */
  blockingLine: string | null
  /** "Expected, not counted: Absent from one run only 300 · Moved to another suite 40." — null with none. */
  expectedLine: string | null
}

const reasonLabel = (reason: string) => REASON_LABEL[reason] ?? reason

function reasonCounts(prefix: string, reasons: readonly string[], counts: Record<string, number>): string | null {
  const parts = reasons
    .filter((reason) => Number.isFinite(counts[reason]) && counts[reason] > 0)
    .map((reason) => `${reasonLabel(reason)} ${formatNumber(counts[reason])}`)
  return parts.length ? `${prefix}: ${parts.join(' · ')}.` : null
}

/**
 * The differences of one comparison, inline under its day: the sample's BLOCKING rows in the server's order (≤ 20
 * per reason), the first `SAMPLE_ROWS_SHOWN`, then how many of the run's differences are not listed; the counts per
 * reason, the expected reasons as information (never counted against the cutover).
 */
export function sampleView(detail: SuiteParityRunDetail): SampleView {
  const blocking = (detail.sample ?? []).filter((row) => row.blocking)
  const rows = blocking.slice(0, SAMPLE_ROWS_SHOWN).map((row, i) => ({
    key: `${row.reason}:${row.project_id}:${row.suite}:${row.fingerprint}:${i}`,
    reason: reasonLabel(row.reason),
    project: row.project_name || PARITY_TEXT.deletedProject,
    suite: row.suite,
    test: row.test_name || row.fingerprint,
  }))
  const more = Math.max(0, (detail.blocking_diffs ?? 0) - rows.length)
  const counts = detail.counts ?? {}
  return {
    rows,
    moreLine: more > 0 ? `… and ${formatNumber(more)} more in this comparison.` : null,
    blockingLine: reasonCounts('By reason', BLOCKING_REASONS, counts),
    expectedLine: reasonCounts('Expected, not counted', EXPECTED_REASONS, counts),
  }
}

// ── "Compare now" ────────────────────────────────────────────────────────────

/**
 * The words for a refused "Compare now": the server's sentence (409 "A comparison is already running.", 503 "Could
 * not queue the comparison."), else the same sentence by status — never "Request failed with status code 409".
 */
export function queueRefusalSentence(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const fallback = response?.status === 409 ? PARITY_TEXT.alreadyRunning : PARITY_TEXT.couldNotQueue
  return extractErrorMessage(response?.data?.detail, fallback)
}
