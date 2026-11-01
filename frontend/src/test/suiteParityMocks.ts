/**
 * Fixtures for the suite-membership cutover card (W25a HYG-04 phase 1), in the REAL §3.1 shapes, shared by the
 * vitests and the hermetic Playwright worlds (`tests/visual/production/fixtures-search.ts`,
 * `tests/ci-e2e/settings-two-column-forms.spec.ts`) — so this file imports by RELATIVE path only (no `@/`).
 *
 * - `parityRun(day, now)`: one comparison started `day.daysAgo` UTC days before `now` (the 06:15 beat by default).
 * - `parityHistoryOf(days, { now })`: the history GET's answer for those runs — newest first, with `streak_days` and
 *   `cutover_ready` COMPUTED here by contract §1.3 (the last finished run of a UTC date decides it; today without a
 *   run does not break the streak; a missing past day does), so a fixture's states always agree.
 * - `parityHistory({ streak, newest, now })`: the common histories (see `ParityNewest`).
 * - `parityDetail(run, …)`: the detail GET's answer for a run — every reason counted (zeros included), the projects,
 *   and the sample (≤ 20 rows per reason, as the server caps it).
 * - W25d HYG-04, the cut-over states (contract §1.4 / §3.2), each a history the server answers together:
 *   `parityLegacyOn(history)` (+ the null-filled `cutover` a W25d server adds before the cutover),
 *   `parityReadyHistory({ now })` (7 clean beat days ending today, legacy writes on),
 *   `parityCutOverHistory({ now, … })` (the 7 clean days ending at the cut-over date, legacy writes off, the audit
 *   row's time and actor, the streak at the cutover) and `parityNotWrittenHistory({ now })` (legacy writes off, no
 *   audit row: the flag row deleted or flipped outside the trail).
 *
 * Names are non-hex words; timestamps are built at CALL time from `now` (never a module constant).
 */
import {
  BLOCKING_REASONS,
  EXPECTED_REASONS,
  INFO_COUNTS,
  REQUIRED_CLEAN_DAYS,
  SAMPLE_PER_REASON,
  type SuiteParityCutover,
  type SuiteParityHistory,
  type SuiteParityRunDetail,
  type SuiteParityRunSummary,
  type SuiteParitySampleRow,
} from '../types/suiteParity'

const DAY_MS = 86_400_000

export const PARITY_PROJECT_ID = '00000000-0000-4000-8000-00000000c0de'
export const PARITY_PROJECT_NAME = 'Payment Service'
export const PARITY_ADMIN_NAME = 'Avery Admin'
/** The expected divergences a clean day of the visual history carries (≈ 340 each). */
export const PARITY_EXPECTED = 340
/** The differences of the diffs day before the streak. */
export const PARITY_BLOCKING = 12

export interface ParityDay {
  /** UTC days before `now` (0 = today). */
  daysAgo: number
  status: 'running' | 'clean' | 'diffs' | 'failed'
  blocking?: number
  expected?: number
  trigger?: 'beat' | 'admin'
  /** UTC start time (default the 06:15 beat). */
  hour?: number
  minute?: number
}

/** A stable run id per (day, start time): `00000000-0000-4000-8000-0000005DDHHMM`. */
export function parityRunId(daysAgo: number, hour = 6, minute = 15): string {
  const tail = `5${String(daysAgo).padStart(2, '0')}${String(hour).padStart(2, '0')}${String(minute).padStart(2, '0')}`
  return `00000000-0000-4000-8000-${tail.padStart(12, '0')}`
}

export function parityRun(day: ParityDay, now: Date = new Date()): SuiteParityRunSummary {
  const hour = day.hour ?? 6
  const minute = day.minute ?? 15
  const date = new Date(now.getTime() - day.daysAgo * DAY_MS)
  const started = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), hour, minute))
  const finished = day.status === 'running' ? null : new Date(started.getTime() + 4 * 60_000).toISOString()
  const trigger = day.trigger ?? 'beat'
  const blocking = day.status === 'diffs' ? (day.blocking ?? PARITY_BLOCKING) : 0
  const expected = day.status === 'clean' || day.status === 'diffs' ? (day.expected ?? PARITY_EXPECTED) : 0
  return {
    id: parityRunId(day.daysAgo, hour, minute),
    trigger,
    status: day.status,
    started_at: started.toISOString(),
    finished_at: finished,
    requested_by_name: trigger === 'admin' ? PARITY_ADMIN_NAME : null,
    window_runs: 5,
    projects_checked: day.status === 'running' ? 0 : 3,
    keys_compared: day.status === 'running' ? 0 : 4210,
    blocking_diffs: blocking,
    expected_divergences: expected,
  }
}

const utcDate = (iso: string) => iso.slice(0, 10)

/** Contract §1.3, over the runs a history holds (a mirror of `suite_membership_parity.streak_days`). */
export function parityStreak(runs: readonly SuiteParityRunSummary[], now: Date = new Date()): number {
  const lastOfDate = new Map<string, SuiteParityRunSummary>()
  for (const run of runs) {
    if (run.status === 'running') continue
    const date = utcDate(run.started_at)
    const seen = lastOfDate.get(date)
    if (!seen || seen.started_at < run.started_at) lastOfDate.set(date, run)
  }
  const today = now.toISOString().slice(0, 10)
  let cursor = lastOfDate.has(today) ? now.getTime() : now.getTime() - DAY_MS
  let streak = 0
  for (;;) {
    const run = lastOfDate.get(new Date(cursor).toISOString().slice(0, 10))
    if (!run || run.status !== 'clean') return streak
    streak += 1
    cursor -= DAY_MS
  }
}

export function parityHistoryOf(
  days: readonly ParityDay[],
  { now = new Date(), windowRunsNow = 5 }: { now?: Date; windowRunsNow?: number } = {},
): SuiteParityHistory {
  const runs = days.map((day) => parityRun(day, now)).sort((a, b) => (a.started_at < b.started_at ? 1 : -1))
  const streak = parityStreak(runs, now)
  return {
    runs,
    streak_days: streak,
    required_days: REQUIRED_CLEAN_DAYS,
    cutover_ready: streak >= REQUIRED_CLEAN_DAYS,
    window_runs_now: windowRunsNow,
  }
}

/**
 * What the newest comparison of `parityHistory` is:
 * - `'clean'` (default): `streak` clean beat days ENDING TODAY, after one `diffs` day (12 differences · 340
 *   expected) — the first comparison after a deploy. `streak: 4` is the visual region's history: "Day 4 of 7", the
 *   two dates before the diffs day never ran.
 * - `'running'`: the same clean days ending YESTERDAY, then today's beat still running (started 06:15).
 * - `'diffs'` / `'failed'`: the same clean days ending YESTERDAY, then today's beat with `blocking` differences / not
 *   finished (the streak is then 0, computed).
 * - `'none'`: no comparison has run yet (an empty history).
 */
export type ParityNewest = 'clean' | 'running' | 'diffs' | 'failed' | 'none'

export function parityHistory({
  streak = 0,
  newest = 'clean',
  blocking = PARITY_BLOCKING,
  now = new Date(),
}: { streak?: number; newest?: ParityNewest; blocking?: number; now?: Date } = {}): SuiteParityHistory {
  if (newest === 'none') return parityHistoryOf([], { now })
  const first = newest === 'clean' ? 0 : 1
  const days: ParityDay[] = []
  // A small spread of expected divergences per day (≈ 340), as one-run noise moves them.
  for (let i = 0; i < streak; i += 1) days.push({ daysAgo: first + i, status: 'clean', expected: PARITY_EXPECTED + ((i * 3) % 5) - 2 })
  if (newest === 'clean') days.push({ daysAgo: streak, status: 'diffs', blocking: PARITY_BLOCKING })
  else days.push({ daysAgo: 0, status: newest, blocking })
  return parityHistoryOf(days, { now })
}

// ── W25d HYG-04: the cut-over states ─────────────────────────────────────────

/** The cut-over visual's actor (`cut_over_by_name`). */
export const PARITY_CUT_OVER_BY = 'admin'

/** The `cutover` object a W25d server answers before the cutover: legacy writes on, every other field null. */
export function parityLegacyOnCutover(): SuiteParityCutover {
  return { legacy_writes: true, cut_over_at: null, cut_over_by_name: null, streak_days_at_cutover: null }
}

/** `history` as a W25d server answers it before the cutover (the same runs, plus the null-filled `cutover`). */
export function parityLegacyOn(history: SuiteParityHistory): SuiteParityHistory {
  return { ...history, cutover: parityLegacyOnCutover() }
}

/** `count` clean beat days from `first` days ago back, ≈ 340 expected each (the same spread as `parityHistory`). */
function cleanDays(count: number, first = 0): ParityDay[] {
  return Array.from({ length: count }, (_, i) => ({ daysAgo: first + i, status: 'clean' as const, expected: PARITY_EXPECTED + ((i * 3) % 5) - 2 }))
}

/** Seven clean beat days ending today, legacy writes on: "Ready for the cutover: 7 clean days in a row." */
export function parityReadyHistory({ now = new Date() }: { now?: Date } = {}): SuiteParityHistory {
  return parityLegacyOn(parityHistoryOf(cleanDays(REQUIRED_CLEAN_DAYS), { now }))
}

/**
 * After a guarded cutover `daysAgo` UTC days before `now`, at `hour:minute` UTC: the server's window ENDS at
 * `cut_over_at` (contract §3.2), so `runs` are the clean beat days ending at the cut-over date that started before
 * it, and `streak_days` / `cutover_ready` are computed over them. `streakAtCutover` defaults to that streak (`null`
 * = the server could not say); `byName` defaults to `PARITY_CUT_OVER_BY` (`null` = a removed user).
 */
export function parityCutOverHistory({
  now = new Date(),
  daysAgo = 0,
  hour = 10,
  minute = 12,
  cleanDaysBefore = REQUIRED_CLEAN_DAYS,
  byName = PARITY_CUT_OVER_BY,
  streakAtCutover,
}: {
  now?: Date
  daysAgo?: number
  hour?: number
  minute?: number
  cleanDaysBefore?: number
  byName?: string | null
  streakAtCutover?: number | null
} = {}): SuiteParityHistory {
  const date = new Date(now.getTime() - daysAgo * DAY_MS)
  const at = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), hour, minute))
  const before = parityHistoryOf(cleanDays(cleanDaysBefore), { now: at })
  const runs = before.runs.filter((run) => run.started_at < at.toISOString())
  const streak = parityStreak(runs, at)
  return {
    ...before,
    runs,
    streak_days: streak,
    cutover_ready: streak >= REQUIRED_CLEAN_DAYS,
    cutover: {
      legacy_writes: false,
      cut_over_at: at.toISOString(),
      cut_over_by_name: byName,
      streak_days_at_cutover: streakAtCutover === undefined ? streak : streakAtCutover,
    },
  }
}

/**
 * Legacy writes off with no audit row saying when (the flag row deleted, or flipped outside the audit trail): the
 * W25a history of `parityHistory({ streak, now })` with `cutover = { legacy_writes: false }` and nulls.
 */
export function parityNotWrittenHistory({ now = new Date(), streak = 4 }: { now?: Date; streak?: number } = {}): SuiteParityHistory {
  return {
    ...parityHistory({ streak, now }),
    cutover: { legacy_writes: false, cut_over_at: null, cut_over_by_name: null, streak_days_at_cutover: null },
  }
}

/**
 * The detail GET's answer for `run`: `blocking` / `expected` counts per reason (defaults: the visual's 12 = 10
 * missing from the catalog + 2 names differ, and 340 = 300 absent from one run + 40 moved), every other reason 0,
 * one project line, and the sample — ≤ `SAMPLE_PER_REASON` rows per reason, blocking reasons first.
 */
export function parityDetail(
  run: SuiteParityRunSummary,
  {
    blocking = { missing_in_canonical: 10, name_drift: 2 },
    expected = { deletion_window: 300, moved_suite: 40 },
    errorCode = null,
  }: { blocking?: Record<string, number>; expected?: Record<string, number>; errorCode?: string | null } = {},
): SuiteParityRunDetail {
  const counts: Record<string, number> = {}
  for (const reason of [...BLOCKING_REASONS, ...EXPECTED_REASONS]) counts[reason] = blocking[reason] ?? expected[reason] ?? 0
  for (const info of INFO_COUNTS) counts[info] = 0
  counts.legacy_modified_events = 1
  const sample: SuiteParitySampleRow[] = []
  for (const reason of [...BLOCKING_REASONS, ...EXPECTED_REASONS]) {
    const isBlocking = (BLOCKING_REASONS as readonly string[]).includes(reason)
    for (let n = 1; n <= Math.min(counts[reason], SAMPLE_PER_REASON); n += 1) {
      sample.push({
        reason,
        blocking: isBlocking,
        project_id: PARITY_PROJECT_ID,
        project_name: PARITY_PROJECT_NAME,
        suite: n % 2 ? 'Checkout Regression' : 'Payments Smoke',
        fingerprint: `fp-${reason}-${n}`,
        test_name: `test_checkout_pays_with_voucher_${n}`,
        legacy: reason === 'missing_in_legacy' ? 'absent' : 'active',
        canonical: reason === 'missing_in_canonical' ? 'absent' : reason === 'moved_suite' ? 'other_suite' : 'active',
      })
    }
  }
  const blockingTotal = Object.values(blocking).reduce((a, b) => a + b, 0)
  const expectedTotal = Object.values(expected).reduce((a, b) => a + b, 0)
  return {
    ...run,
    blocking_diffs: blockingTotal,
    expected_divergences: expectedTotal,
    counts,
    per_project: [{ project_id: PARITY_PROJECT_ID, project_name: PARITY_PROJECT_NAME, blocking: blockingTotal, expected: expectedTotal }],
    sample,
    error_code: errorCode,
  }
}
