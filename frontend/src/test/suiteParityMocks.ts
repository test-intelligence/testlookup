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
 *
 * Names are non-hex words; timestamps are built at CALL time from `now` (never a module constant).
 */
import {
  BLOCKING_REASONS,
  EXPECTED_REASONS,
  INFO_COUNTS,
  REQUIRED_CLEAN_DAYS,
  SAMPLE_PER_REASON,
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
