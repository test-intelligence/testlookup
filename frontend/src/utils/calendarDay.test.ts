import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import {
  daysBetweenDayIso, formatDayIso, relativeDayLabel, shiftDayIso, utcDayIso,
} from './calendarDay'

// The bug this module exists to prevent only reproduces west of Greenwich, so
// the suite pins a timezone rather than inheriting the runner's. Node re-reads
// TZ per Date operation, so setting it here works even when the process
// started in UTC (verified — CI does).
// `@types/node` is not in the frontend's tsconfig types; this is the one thing
// the suite needs from it.
declare const process: { env: Record<string, string | undefined> }

const REAL_TZ = process.env.TZ

describe('calendarDay (America/Chicago, UTC-5)', () => {
  beforeAll(() => { process.env.TZ = 'America/Chicago' })
  afterAll(() => { process.env.TZ = REAL_TZ })

  it('demonstrates the parse that shifted every label a day early', () => {
    // This is what shortDate() used to do. Kept as the reason the helper exists:
    // if this ever stops being true the helper can go.
    expect(new Date('2026-08-16').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }))
      .toBe('Aug 15')
    // The helper reads the string's own parts, so it names the day the backend
    // bucketed.
    expect(formatDayIso('2026-08-16')).toBe('Aug 16')
  })

  it('labels a day the same way regardless of local offset', () => {
    for (const tz of ['America/Chicago', 'UTC', 'Asia/Kolkata', 'Pacific/Kiritimati']) {
      process.env.TZ = tz
      expect(formatDayIso('2026-01-01')).toBe('Jan 1')
      expect(formatDayIso('2026-12-31')).toBe('Dec 31')
    }
    process.env.TZ = 'America/Chicago'
  })

  it('builds a consecutive window, and agrees with the loop it replaces', () => {
    // The old builders read a local calendar date and stamped it with
    // toISOString(). That mix is equivalent to whole-day UTC arithmetic — it
    // was consolidated for one frame end-to-end, not because it miscounted.
    // Pinned here so a future "simplification" back to mixed frames has to
    // argue with a test. 2026-11-01 and 2026-03-08 are the US DST transitions.
    const mixedFrameWindow = (now: Date, days: number) => {
      const today = new Date(now)
      const out: string[] = []
      for (let i = days - 1; i >= 0; i--) {
        const d = new Date(today)
        d.setDate(today.getDate() - i)
        out.push(d.toISOString().slice(0, 10))
      }
      return out
    }
    for (const at of ['2026-08-17T00:30:00Z', '2026-11-03T12:00:00Z', '2026-03-10T12:00:00Z']) {
      const now = new Date(at)
      const window14 = Array.from({ length: 14 }, (_, i) => shiftDayIso(utcDayIso(now), -(13 - i)))
      expect(window14).toEqual(mixedFrameWindow(now, 14))
      expect(window14[13]).toBe(utcDayIso(now))
      expect(new Set(window14).size).toBe(14)
      for (let i = 1; i < window14.length; i++) {
        expect(daysBetweenDayIso(window14[i], window14[i - 1])).toBe(1)
      }
    }
  })

  it('crosses month and year ends', () => {
    expect(shiftDayIso('2026-03-01', -1)).toBe('2026-02-28')
    expect(shiftDayIso('2028-03-01', -1)).toBe('2028-02-29')  // leap year
    expect(shiftDayIso('2026-01-01', -1)).toBe('2025-12-31')
    expect(daysBetweenDayIso('2026-01-01', '2025-12-31')).toBe(1)
  })

  it('reports age at the granularity a calendar day actually has', () => {
    const now = new Date('2026-08-16T23:45:00Z')
    // The old helper did Date.now() - new Date('2026-08-16'), i.e. UTC
    // midnight, and reported "23h ago" for a run ingested minutes earlier.
    expect(relativeDayLabel('2026-08-16', now)).toBe('today')
    expect(relativeDayLabel('2026-08-15', now)).toBe('yesterday')
    expect(relativeDayLabel('2026-08-09', now)).toBe('7d ago')
    expect(relativeDayLabel(null, now)).toBe('—')
  })

  it('passes malformed input through instead of inventing a date', () => {
    expect(formatDayIso('')).toBe('')
    expect(formatDayIso('not-a-day')).toBe('not-a-day')
    expect(formatDayIso('2026-13-01')).toBe('2026-13-01')
    expect(daysBetweenDayIso('nope', '2026-01-01')).toBeNull()
    expect(shiftDayIso('nope', -1)).toBe('nope')
  })
})
