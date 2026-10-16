/**
 * W08 TRND-07: the suite row's last-run chip — the short age, the run link,
 * and the amber rule (older, in UTC days, than the project's median gap
 * between active days, never under 1).
 */
import { describe, expect, it } from 'vitest'
import type { TrendPoint } from '@/types/metrics'
import { lastRunChip, shortAge, typicalRunGapDays, utcDaysBetween } from './coverageLastRun'

const NOW = Date.parse('2026-10-16T12:00:00Z')
const HOUR = 3_600_000
const DAY = 24 * HOUR

const day = (date: string, total = 5): TrendPoint =>
  ({ date, passed: total, failed: 0, skipped: 0, broken: 0, total, pass_rate: 100 }) as TrendPoint

describe('shortAge', () => {
  it.each([
    [0, '<1h'],
    [59 * 60_000, '<1h'],
    [HOUR, '1h'],
    [5 * HOUR + 59 * 60_000, '5h'],
    [23 * HOUR, '23h'],
    [DAY, '1d'],
    [2 * DAY + 3 * HOUR, '2d'],
    [14 * DAY, '14d'],
    [-HOUR, '<1h'],
  ])('%d ms ago → %s', (ago, text) => {
    expect(shortAge(NOW - ago, NOW)).toBe(text)
  })
})

describe('typicalRunGapDays', () => {
  it('is the median gap between consecutive active days', () => {
    // active: 1, 3, 4, 10 → gaps 2, 1, 6 → median 2
    expect(typicalRunGapDays([day('2026-10-01'), day('2026-10-03'), day('2026-10-04'), day('2026-10-10')])).toBe(2)
  })

  it('an even number of gaps takes the mean of the middle two', () => {
    // active: 1, 2, 5, 9, 12 → gaps 1, 3, 4, 3 → sorted 1 3 3 4 → 3
    expect(typicalRunGapDays([day('2026-10-01'), day('2026-10-02'), day('2026-10-05'), day('2026-10-09'), day('2026-10-12')])).toBe(3)
  })

  it('is never under 1, and is 1 with fewer than two active days or no data', () => {
    expect(typicalRunGapDays([day('2026-10-01'), day('2026-10-02'), day('2026-10-03')])).toBe(1)
    expect(typicalRunGapDays([day('2026-10-01')])).toBe(1)
    expect(typicalRunGapDays([])).toBe(1)
    expect(typicalRunGapDays(undefined)).toBe(1)
    expect(typicalRunGapDays('x' as unknown as TrendPoint[])).toBe(1)
  })

  it('skips empty days (zero-filled) and malformed rows', () => {
    const rows = [day('2026-10-01'), day('2026-10-02', 0), day('2026-10-05'), { date: 7 } as unknown as TrendPoint]
    expect(typicalRunGapDays(rows)).toBe(4)
  })
})

describe('lastRunChip', () => {
  const at = (msAgo: number) => new Date(NOW - msAgo).toISOString()
  const fmt = () => 'Oct 14, 10:00'

  it('no last_run_at (a pre-W08 payload) → no chip, never a made-up age', () => {
    expect(lastRunChip({}, 1, NOW, fmt)).toBeNull()
    expect(lastRunChip({ last_run_at: null, last_run_id: 'r' }, 1, NOW, fmt)).toBeNull()
    expect(lastRunChip({ last_run_at: 'yesterday' }, 1, NOW, fmt)).toBeNull()
  })

  it('the age, the run link (encoded), and the title', () => {
    const chip = lastRunChip({ last_run_at: at(2 * DAY), last_run_id: 'run/1?x' }, 7, NOW, fmt)
    expect(chip).toEqual({
      text: '2d ago',
      href: '/runs/run%2F1%3Fx',
      title: 'Last run 2d ago — Oct 14, 10:00',
      amber: false,
    })
  })

  it('no run id → plain text (no link)', () => {
    expect(lastRunChip({ last_run_at: at(HOUR) }, 1, NOW, fmt)?.href).toBeNull()
  })

  it('amber only when older than the typical gap, in UTC days; the title says how often', () => {
    // 2026-10-16T12:00 now; a run at 2026-10-14T13:00 is 2 UTC days back.
    const twoDays = { last_run_at: '2026-10-14T13:00:00Z', last_run_id: 'r' }
    expect(lastRunChip(twoDays, 2, NOW, fmt)?.amber).toBe(false)
    const stale = lastRunChip(twoDays, 1, NOW, fmt)
    expect(stale?.amber).toBe(true)
    expect(stale?.title).toBe('Last run 1d ago — Oct 14, 10:00; this project usually runs every 1 day')
    expect(lastRunChip(twoDays, 1.5, NOW, fmt)?.title).toMatch(/every 1\.5 days$/)
  })

  it('utcDaysBetween counts calendar days, not 24 h periods', () => {
    expect(utcDaysBetween(Date.parse('2026-10-15T23:59:00Z'), Date.parse('2026-10-16T00:01:00Z'))).toBe(1)
    expect(utcDaysBetween(Date.parse('2026-10-16T00:01:00Z'), Date.parse('2026-10-16T23:59:00Z'))).toBe(0)
  })
})
