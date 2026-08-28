import { describe, expect, it } from 'vitest'

import { dayTimeAgo, formatDuration, formatRunWhen, shortAgo } from './formatters'

describe('dayTimeAgo', () => {
  // Build day-only strings relative to the runner's LOCAL "today" so the
  // assertions are timezone-independent (the fix parses at local midnight).
  const dayStr = (offsetDays: number): string => {
    const now = new Date()
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offsetDays)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }

  it("labels today's date 'today' (not '—') regardless of timezone", () => {
    // Regression: the old ``…T00:00:00Z`` forced UTC midnight, so for users
    // east/west of UTC the age went negative near day boundaries and rendered
    // "—" ("Last run —") even with a fresh run. Local parsing → always "today".
    expect(dayTimeAgo(dayStr(0))).toBe('today')
  })

  it("labels yesterday and older days by whole calendar days", () => {
    expect(dayTimeAgo(dayStr(1))).toBe('yesterday')
    expect(dayTimeAgo(dayStr(3))).toBe('3 d ago')
  })

  it("treats a future day-bucket as 'today' rather than discarding it", () => {
    // A date-only value can round slightly ahead of now; must not become '—'.
    expect(dayTimeAgo(dayStr(-1))).toBe('today')
  })

  it("returns '—' for empty or unparseable input", () => {
    expect(dayTimeAgo('')).toBe('—')
    expect(dayTimeAgo(null)).toBe('—')
    expect(dayTimeAgo('not-a-date')).toBe('—')
  })
})

describe('formatRunWhen', () => {
  it('formats an ISO timestamp into a compact "MMM dd, HH:mm" label', () => {
    // Local-time formatting: build the date from local parts so the assertion
    // is timezone-independent (date-fns formats in the runner's local zone).
    const d = new Date(2026, 5, 8, 14, 30) // Jun 8 2026, 14:30 local
    expect(formatRunWhen(d)).toBe('Jun 08, 14:30')
  })

  it('accepts an ISO string', () => {
    const iso = new Date(2026, 0, 2, 9, 5).toISOString()
    expect(formatRunWhen(iso)).toBe('Jan 02, 09:05')
  })

  it('returns "" for null/undefined/empty so callers can guard the suffix', () => {
    expect(formatRunWhen(null)).toBe('')
    expect(formatRunWhen(undefined)).toBe('')
    expect(formatRunWhen('')).toBe('')
  })

  it('returns "" for an unparseable date instead of "Invalid Date"', () => {
    expect(formatRunWhen('not-a-date')).toBe('')
  })
})

describe('formatDuration', () => {
  it('renders a genuine 0ms as "0ms", not the "—" used for unknown', () => {
    // Regression: the old `if (!ms)` guard treated a real zero-length duration
    // (a sub-millisecond step that rounds to 0) as missing, so an instantaneous
    // step showed the same dash as one that was never timed.
    expect(formatDuration(0)).toBe('0ms')
  })

  it('returns "—" for missing values (null / undefined)', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(undefined)).toBe('—')
  })

  it('returns "—" for NaN and invalid negatives (clock skew), not "NaNms"/"-5ms"', () => {
    expect(formatDuration(NaN)).toBe('—')
    expect(formatDuration(-5)).toBe('—')
  })

  it('renders sub-second durations in milliseconds', () => {
    expect(formatDuration(1)).toBe('1ms')
    expect(formatDuration(999)).toBe('999ms')
  })

  it('renders sub-minute durations in seconds to one decimal', () => {
    expect(formatDuration(1000)).toBe('1.0s')
    expect(formatDuration(1500)).toBe('1.5s')
    expect(formatDuration(59_000)).toBe('59.0s')
  })

  it('renders a minute or more as "Xm Ys"', () => {
    expect(formatDuration(60_000)).toBe('1m 0s')
    expect(formatDuration(90_000)).toBe('1m 30s')
    expect(formatDuration(125_000)).toBe('2m 5s')
  })
})

describe('shortAgo', () => {
  // Provenance rows on Coverage / Failure Analysis / Trends used to render a
  // hardcoded age ('4h ago', '12m ago') regardless of the real one, so freshly
  // ingested data claimed to be hours stale. These pin the real thresholds.
  const agoMs = (ms: number) => new Date(Date.now() - ms)

  it('reports sub-minute ages as "just now"', () => {
    expect(shortAgo(agoMs(0))).toBe('just now')
    expect(shortAgo(agoMs(59_000))).toBe('just now')
  })

  it('reports minutes, hours and days at their boundaries', () => {
    expect(shortAgo(agoMs(60_000))).toBe('1m ago')
    expect(shortAgo(agoMs(59 * 60_000))).toBe('59m ago')
    expect(shortAgo(agoMs(60 * 60_000))).toBe('1h ago')
    expect(shortAgo(agoMs(23 * 3_600_000))).toBe('23h ago')
    expect(shortAgo(agoMs(24 * 3_600_000))).toBe('1d ago')
    expect(shortAgo(agoMs(9 * 24 * 3_600_000))).toBe('9d ago')
  })

  it('never renders a negative age or Invalid Date', () => {
    // A clock skew between client and server must not produce '-3h ago'.
    expect(shortAgo(new Date(Date.now() + 3_600_000))).toBe('just now')
    expect(shortAgo('not-a-date')).toBe('just now')
  })

  it('accepts the shapes callers actually hold', () => {
    const t = Date.now() - 2 * 60_000
    expect(shortAgo(new Date(t))).toBe('2m ago')
    expect(shortAgo(t)).toBe('2m ago')
    expect(shortAgo(new Date(t).toISOString())).toBe('2m ago')
  })
})
