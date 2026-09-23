/**
 * VIZ-404 — release-over-release alignment is a client transform over the
 * absolute days the API returns: both lines start at day 0, a day one release
 * does not have is a gap with a reason, and every relative day keeps the date
 * it stands for.
 */
import { describe, expect, it } from 'vitest'
import type { SeriesPoint } from '@/lib/viz/contracts'
import {
  NO_START_REASON,
  addUtcDays,
  alignByReleaseStart,
  alignedRowLabel,
  utcDaysBetween,
} from './seriesAlignment'

const days = (from: string, values: (number | null)[], n = 50): SeriesPoint[] =>
  values.map((y, i) =>
    y === null
      ? { x: addUtcDays(from, i), y: null, n: 0, measured: false, reason: 'no evaluated executions' }
      : { x: addUtcDays(from, i), y, n },
  )

describe('day arithmetic', () => {
  it('adds and measures UTC days across a month end and a DST change', () => {
    expect(addUtcDays('2026-02-27', 3)).toBe('2026-03-02')
    expect(addUtcDays('2026-03-07', 2)).toBe('2026-03-09') // US DST starts 2026-03-08
    expect(utcDaysBetween('2026-03-02', '2026-04-10')).toBe(39)
    expect(utcDaysBetween('2026-03-02', 'not a day')).toBeNaN()
  })
})

describe('alignByReleaseStart', () => {
  // The API returns ONE calendar window; R1 ran in March, R2 in April, and the
  // days before each release's start are zero-filled gaps.
  const r1 = { key: 'r1', label: 'R1', points: [...days('2026-02-28', [null, null]), ...days('2026-03-02', [90, 92, 95])] }
  const r2 = { key: 'r2', label: 'R2', points: days('2026-04-10', [80, 85, 88, 91]) }

  it('starts BOTH lines at day 0 — each at its own first day with anything evaluated', () => {
    const aligned = alignByReleaseStart([r1, r2])
    expect(aligned.xs).toEqual(['0', '1', '2', '3'])
    const [a, b] = aligned.series
    expect(a.start).toBe('2026-03-02')
    expect(b.start).toBe('2026-04-10')
    expect(a.points[0]).toMatchObject({ x: '0', y: 90 })
    expect(b.points[0]).toMatchObject({ x: '0', y: 80 })
    // Each relative day keeps the absolute day it stands for.
    expect(a.dates.slice(0, 3)).toEqual(['2026-03-02', '2026-03-03', '2026-03-04'])
    expect(b.dates[3]).toBe('2026-04-13')
  })

  it('a day one release does not have is a GAP with a reason, never a zero', () => {
    const [a] = alignByReleaseStart([r1, r2]).series
    // R1 has three days, R2 four: R1's day 3 is past what the API returned.
    expect(a.points[3].y).toBeNull()
    expect(a.points[3].measured).toBe(false)
    expect(a.points[3].reason).toMatch(/R1 has no data for day 3 \(2026-03-05\)/)
  })

  it('keeps a gap the API sent as a gap, with the API reason', () => {
    const r3 = { key: 'r3', label: 'R3', points: days('2026-05-01', [70, null, 75]) }
    const [c] = alignByReleaseStart([r3]).series
    expect(c.points[1]).toMatchObject({ y: null, measured: false, reason: 'no evaluated executions' })
  })

  it('honours an explicit start and drops the days before it', () => {
    const [a] = alignByReleaseStart([r1], { starts: { r1: '2026-03-03' } }).series
    expect(a.start).toBe('2026-03-03')
    expect(a.points[0]).toMatchObject({ x: '0', y: 92 })
    expect(a.points.map((p) => p.x)).toEqual(['0', '1'])
  })

  it('a release with nothing evaluated is all gaps, with the reason', () => {
    const empty = { key: 'r0', label: 'R0', points: days('2026-06-01', [null, null]) }
    const aligned = alignByReleaseStart([empty, r2])
    const [z] = aligned.series
    expect(z.points.every((p) => p.y === null && p.reason === NO_START_REASON)).toBe(true)
  })

  it('never exceeds maxDays, and counts points whose x is not a day', () => {
    const odd = { key: 'x', label: 'X', points: [{ x: 'week-1', y: 1, n: 1 }, ...days('2026-01-01', [1, 2, 3, 4, 5])] }
    const aligned = alignByReleaseStart([odd], { maxDays: 3 })
    expect(aligned.xs).toEqual(['0', '1', '2'])
    expect(aligned.unplaced).toBe(1)
  })

  it('says where each day 0 came from: a named start, the first day with runs, or neither', () => {
    const empty = { key: 'r0', label: 'R0', points: days('2026-06-01', [null, null]) }
    const aligned = alignByReleaseStart([r1, r2, empty], { starts: { r2: '2026-04-10' } })
    expect(aligned.series.map((s) => s.startSource)).toEqual(['first-active', 'named', 'none'])
  })

  it('a day missing INSIDE the returned window is one the release reported nothing for, not one outside it', () => {
    const holey = { key: 'r4', label: 'R4', points: days('2026-02-02', [70, 71, 72, 73]).filter((p) => p.x !== '2026-02-03') }
    const [h] = alignByReleaseStart([holey, r2]).series
    expect(h.points[1].reason).toBe('R4 reported nothing for day 1 (2026-02-03)')
    // Past its last returned day, it IS outside the window.
    expect(alignByReleaseStart([r1, r2]).series[0].points[3].reason).toMatch(/outside the returned window/)
  })

  it('names the relative day AND every absolute date in the row header', () => {
    const aligned = alignByReleaseStart([r1, r2])
    expect(alignedRowLabel(2, aligned.series)).toBe('Day 2 (R1 2026-03-04; R2 2026-04-12)')
  })
})
