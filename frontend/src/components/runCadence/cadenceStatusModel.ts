/**
 * The expected-run cadence as Trends and Coverage read it (W23a TRND-03,
 * contract §0.10 / §0.11 / §4.1 / §4.3): the pure model of the Trends › Volume
 * callout and of the Coverage score's run dimension and gap rows. Every
 * sentence is here, so a test imports the one string it asserts.
 *
 * Nothing here assumes a schedule nobody configured: without a configured
 * cadence there is no "Expected runs missed" tile, no "Action needed" and no
 * "Schedule appears paused" — only the measured gap and the Configure line.
 * With one, the count is the server's exact `missed` (never a `~`).
 */
import type { TrendSummaryGap } from '@/types/trends'
import { formatMonthDay, formatWeekday } from '@/utils/formatters'
import {
  CONFIGURE_LABEL,
  NO_CADENCE_SENTENCE,
  missedLine,
  type RunCadenceStatus,
  type RunCadenceStatusItem,
} from '@/types/runCadence'

export const TITLE_MISSED = 'Expected runs missed'
export const TITLE_PAUSED = 'Expected runs paused'
/** The gap's title, ongoing or not (the callout keeps its name and its `schedpaused` region). */
export const TITLE_GAP = 'Run gap in this window'
export const SILENT_DAYS = 'Silent days'
export const MISSED_TILE = 'Expected runs missed'
export const ACTION_NEEDED = 'Action needed'
/** `frontend.load-error-wording`: the status read failed. */
export const CADENCE_LOAD_ERROR = 'Could not load the expected runs.'
/** Settings › Project › Expected runs (G-C's page). */
export const EXPECTED_RUNS_PATH = '/settings/expected-runs'
/** `No expected cadence configured — Configure` (the link's words included). */
export const NO_CADENCE_CONFIGURE = `${NO_CADENCE_SENTENCE} — ${CONFIGURE_LABEL}`
/** The same line for someone who cannot open Settings. */
export const NO_CADENCE_PLAIN = `${NO_CADENCE_SENTENCE} — a QA lead can set one in Settings`
export const ANY_RUN_SCOPE = 'Any run of the project'

/** The Coverage score's run dimension: its label with a configured cadence, and without one. */
export const RUN_CADENCE_LABEL = 'Run cadence'
export const DAYS_WITH_RUNS_LABEL = 'Days with runs'

export interface CalloutTile {
  label: string
  value: string | number
}

/** A line of the callout or a Coverage gap row: plain text, or the Configure line with its link. */
export type CadenceLine =
  | { kind: 'configure'; text: string; linkLabel: string; to: string }
  | { kind: 'text'; text: string }

export interface CalloutModel {
  show: boolean
  title: string
  tiles: CalloutTile[]
  lines: CadenceLine[]
  actionNeeded: boolean
}

/** The Configure line: a link for a QA lead / admin, the plain sentence otherwise. */
export function configureLine(canConfigure: boolean): CadenceLine {
  return canConfigure
    ? { kind: 'configure', text: `${NO_CADENCE_SENTENCE} — `, linkLabel: CONFIGURE_LABEL, to: EXPECTED_RUNS_PATH }
    : { kind: 'text', text: NO_CADENCE_PLAIN }
}

/** A cadence's scope as the lines name it: its suite, or any run. */
export function scopeLabel(item: RunCadenceStatusItem): string {
  return item.cadence?.suite_name?.trim() || ANY_RUN_SCOPE
}

/**
 * `Fri Oct 30`: the pause's end through the app's ONE date format (`utils/formatters`, W29a UIX-10 — a
 * page never formats with `Intl` itself), in the reader's zone; `''` for an unreadable instant.
 */
export function dayLabel(iso: string | null | undefined): string {
  const at = iso ? new Date(iso) : null
  if (!at || Number.isNaN(at.getTime())) return ''
  return `${formatWeekday(at, 'short')} ${formatMonthDay(at)}`
}

export function isPausedItem(item: RunCadenceStatusItem): boolean {
  return item.cadence?.paused === true
}

/** `Paused until Fri 30 Oct (release freeze) — set by Alice Lead`, prefixed by the scope. */
export function pausedLine(item: RunCadenceStatusItem): string {
  const c = item.cadence
  const reason = c?.paused_reason?.trim()
  const who = c?.paused_by_name?.trim()
  const until = dayLabel(c?.paused_until)
  return `${scopeLabel(item)}: Paused until ${until}${reason ? ` (${reason})` : ''}${who ? ` — set by ${who}` : ''}`
}

/** `nightly-regression: 2 expected runs missed (Tue, Wed)`. */
export function scopedMissedLine(item: RunCadenceStatusItem): string {
  return `${scopeLabel(item)}: ${missedLine(item)}`
}

function itemsOf(status: RunCadenceStatus | null | undefined): RunCadenceStatusItem[] {
  return Array.isArray(status?.items) ? status.items : []
}

/**
 * The Trends › Volume callout (`SchedulePausedCallout`).
 *
 * - `status` null = not read yet (the gap part only) or failed (`statusError`: the gap part plus
 *   "Could not load the expected runs.", nothing at all without a gap).
 * - Shown when the server's gap exists, or a CONFIGURED cadence has misses or a pause.
 * - Title: `Expected runs missed` (configured, missed > 0) · `Expected runs paused` (configured, only
 *   paused) · else `Run gap in this window`.
 * - Tiles: `Silent days` (the gap) and, only when configured, `Expected runs missed` = the exact count.
 * - `Action needed` only when configured with a miss on a cadence that is not paused.
 */
export function calloutModel(
  gap: TrendSummaryGap | null | undefined,
  status: RunCadenceStatus | null | undefined,
  canConfigure: boolean,
  statusError = false,
): CalloutModel {
  const configured = status?.configured === true
  const items = configured ? itemsOf(status) : []
  const missed = configured ? Math.max(0, Number(status?.missed) || 0) : 0
  const paused = items.filter(isPausedItem)
  const lines: CadenceLine[] = []
  const tiles: CalloutTile[] = []
  if (gap) tiles.push({ label: SILENT_DAYS, value: gap.silent_days })
  if (configured) {
    tiles.push({ label: MISSED_TILE, value: missed })
    for (const item of items) if ((item.missed ?? 0) > 0) lines.push({ kind: 'text', text: scopedMissedLine(item) })
    for (const item of paused) lines.push({ kind: 'text', text: pausedLine(item) })
  } else if (status && !statusError) {
    lines.push(configureLine(canConfigure))
  }
  if (statusError) lines.push({ kind: 'text', text: CADENCE_LOAD_ERROR })
  const title = configured && missed > 0 ? TITLE_MISSED : configured && paused.length > 0 ? TITLE_PAUSED : TITLE_GAP
  return {
    show: Boolean(gap) || (configured && (missed > 0 || paused.length > 0)),
    title,
    tiles,
    lines,
    actionNeeded: configured && items.some((item) => (item.missed ?? 0) > 0 && !isPausedItem(item)),
  }
}

/** A Coverage gap row from the cadence (the tone picks its dot). */
export interface CadenceGapRow {
  tone: 'warn' | 'info'
  line: CadenceLine
}

/** How many cadences with misses Coverage names before `and n more`. */
export const COVERAGE_MISSED_SHOWN = 2

/** `No runs in the last 14 days`: a measured fact (it replaced "No nightly schedule firing"). */
export function noRunsSentence(days: number): string {
  return days === 1 ? 'No runs in the last 24 hours' : `No runs in the last ${days} days`
}

/**
 * The Coverage score's gap rows about runs. No inferred schedule: "No nightly
 * schedule firing" became the measured `No runs in the last N days`, and "Run
 * cadence below 50% — scheduled runs may be paused" is gone. Then: not
 * configured → the Configure line; configured → each cadence's misses (at most
 * two, then `and n more`) and each pause.
 */
export function coverageCadenceRows(
  status: RunCadenceStatus | null | undefined,
  days: number,
  daysWithRuns: number,
  canConfigure = false,
  statusError = false,
): CadenceGapRow[] {
  const rows: CadenceGapRow[] = []
  if (daysWithRuns === 0) rows.push({ tone: 'warn', line: { kind: 'text', text: noRunsSentence(days) } })
  if (statusError) {
    rows.push({ tone: 'info', line: { kind: 'text', text: CADENCE_LOAD_ERROR } })
    return rows
  }
  if (!status) return rows
  if (status.configured !== true) {
    rows.push({ tone: 'info', line: configureLine(canConfigure) })
    return rows
  }
  const items = itemsOf(status)
  const withMisses = items.filter((item) => (item.missed ?? 0) > 0)
  for (const item of withMisses.slice(0, COVERAGE_MISSED_SHOWN)) {
    rows.push({ tone: 'warn', line: { kind: 'text', text: scopedMissedLine(item) } })
  }
  const more = withMisses.length - COVERAGE_MISSED_SHOWN
  if (more > 0) {
    rows.push({ tone: 'warn', line: { kind: 'text', text: `and ${more} more cadence${more === 1 ? '' : 's'} with missed runs` } })
  }
  for (const item of items.filter(isPausedItem)) {
    const c = item.cadence
    rows.push({
      tone: 'info',
      line: { kind: 'text', text: `${scopeLabel(item)}: Expected runs paused until ${dayLabel(c?.paused_until)}` },
    })
  }
  return rows
}

/** The run dimension's input: the configured cadence's `{ expected, met }` over the page window, else null. */
export function cadenceDimension(status: RunCadenceStatus | null | undefined): { expected: number; met: number } | null {
  if (status?.configured !== true) return null
  const expected = Number(status.expected) || 0
  if (expected <= 0) return null
  return { expected, met: Math.max(0, Math.min(expected, Number(status.met) || 0)) }
}

/** The dimension's detail: `4 of 5 expected runs` (configured) or `7 of 14 days`. */
export function cadenceDetail(cadence: { expected: number; met: number }): string {
  return `${cadence.met} of ${cadence.expected} expected run${cadence.expected === 1 ? '' : 's'}`
}
export function daysWithRunsDetail(daysWithRuns: number, days: number): string {
  return `${daysWithRuns} of ${days} day${days === 1 ? '' : 's'}`
}
