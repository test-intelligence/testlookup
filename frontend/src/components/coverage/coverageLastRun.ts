/**
 * W08 TRND-07: the "last run" chip of a suite row on /coverage — pure, so the
 * age words and the amber rule are table-testable.
 *
 *   - The age is short: `<1h`, `5h`, `2d`, `14d`.
 *   - Amber when the suite's last run is older, in UTC days, than the
 *     project's typical gap: max(1, the MEDIAN gap in days between
 *     consecutive active days of the page's own trend data). The title then
 *     says how often the project usually runs.
 *   - No `last_run_at` (a payload from before W08, or a malformed one) → no
 *     chip at all, never a made-up age.
 */
import type { CoverageSuite } from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'

const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000
const DAY = /^\d{4}-\d{2}-\d{2}/

/** `<1h` · `5h` · `2d`: the age of `atMs` at `nowMs` (a future instant reads `<1h`). */
export function shortAge(atMs: number, nowMs: number): string {
  const ms = Math.max(0, nowMs - atMs)
  if (ms < HOUR_MS) return '<1h'
  if (ms < DAY_MS) return `${Math.floor(ms / HOUR_MS)}h`
  return `${Math.floor(ms / DAY_MS)}d`
}

/** Whole UTC calendar days from the UTC day of `atMs` to the UTC day of `nowMs`. */
export function utcDaysBetween(atMs: number, nowMs: number): number {
  const day = (ms: number) => Math.floor(ms / DAY_MS)
  return Math.max(0, day(nowMs) - day(atMs))
}

/**
 * The project's typical gap between runs, in days: max(1, the median gap
 * between consecutive active days of `trend`). Fewer than two active days
 * gives 1 (no evidence of a slower cadence).
 */
export function typicalRunGapDays(trend: readonly TrendPoint[] | null | undefined): number {
  if (!Array.isArray(trend)) return 1
  const active: number[] = []
  for (const point of trend) {
    if (!point || typeof point.date !== 'string' || !DAY.test(point.date)) continue
    const total = (point.passed ?? 0) + (point.failed ?? 0) + (point.skipped ?? 0) + (point.broken ?? 0)
    if (!(total > 0)) continue
    const at = Date.parse(`${point.date.slice(0, 10)}T00:00:00Z`)
    if (Number.isFinite(at)) active.push(Math.round(at / DAY_MS))
  }
  const days = [...new Set(active)].sort((a, b) => a - b)
  if (days.length < 2) return 1
  const gaps = days.slice(1).map((day, i) => day - days[i]).sort((a, b) => a - b)
  const mid = Math.floor(gaps.length / 2)
  const median = gaps.length % 2 === 1 ? gaps[mid] : (gaps[mid - 1] + gaps[mid]) / 2
  return Math.max(1, median)
}

export interface LastRunChip {
  text: string
  /** `/runs/{id}`, or `null` when the run's id is not known (the chip is then plain text). */
  href: string | null
  title: string
  amber: boolean
}

const dayWord = (g: number) => (g === 1 ? 'day' : 'days')

/** The chip of one suite row, or `null` when its last run is not known. */
export function lastRunChip(
  suite: Pick<CoverageSuite, 'last_run_at' | 'last_run_id'>,
  typicalGapDays: number,
  nowMs: number,
  formatLocal: (atMs: number) => string = (atMs) => new Date(atMs).toLocaleString(),
): LastRunChip | null {
  const at = typeof suite.last_run_at === 'string' ? Date.parse(suite.last_run_at) : Number.NaN
  if (!Number.isFinite(at)) return null
  const age = shortAge(at, nowMs)
  const gap = Number.isFinite(typicalGapDays) && typicalGapDays > 0 ? typicalGapDays : 1
  const amber = utcDaysBetween(at, nowMs) > gap
  const shownGap = Number.isInteger(gap) ? String(gap) : gap.toFixed(1)
  const id = typeof suite.last_run_id === 'string' && suite.last_run_id.trim() ? suite.last_run_id.trim() : null
  return {
    text: `${age} ago`,
    href: id ? `/runs/${encodeURIComponent(id)}` : null,
    title: `Last run ${age} ago — ${formatLocal(at)}${amber ? `; this project usually runs every ${shownGap} ${dayWord(gap)}` : ''}`,
    amber,
  }
}
