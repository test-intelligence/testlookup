import { describe, expect, it } from 'vitest'

import {
  dayTimeAgo,
  formatCompactDateTime,
  formatDuration,
  formatRunWhen,
  formatTimingRange,
  timingRangeParts,
  isoTooltip,
  shortAgo,
} from './formatters'

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

describe('formatCompactDateTime', () => {
  it('renders a narrow local stamp instead of a full toLocaleString()', () => {
    // Regression (UX audit issue 1): two ~200px `toLocaleString()` columns under
    // `whitespace-nowrap` pushed End + Actions out of the scroll wrapper.
    expect(formatCompactDateTime(new Date(2026, 7, 28, 14, 32))).toBe('Aug 28, 14:32')
  })

  it('does not zero-pad a single-digit day', () => {
    expect(formatCompactDateTime(new Date(2026, 7, 8, 9, 5))).toBe('Aug 8, 09:05')
  })

  it("renders '—' for missing or unparseable input, never 'Invalid Date'", () => {
    expect(formatCompactDateTime(null)).toBe('—')
    expect(formatCompactDateTime(undefined)).toBe('—')
    expect(formatCompactDateTime('not-a-date')).toBe('—')
  })
})

describe('formatTimingRange', () => {
  it('collapses a same-day range so the date is not repeated', () => {
    expect(
      formatTimingRange(new Date(2026, 7, 28, 14, 32), new Date(2026, 7, 28, 14, 46)),
    ).toBe('Aug 28, 14:32 → 14:46')
  })

  it('REPEATS the date when the run crosses midnight', () => {
    // Without this an overnight run reads as ending ~24h before it started.
    expect(
      formatTimingRange(new Date(2026, 7, 28, 23, 50), new Date(2026, 7, 29, 0, 12)),
    ).toBe('Aug 28, 23:50 → Aug 29, 00:12')
  })

  it('marks a still-running range rather than inventing an end', () => {
    expect(formatTimingRange(new Date(2026, 7, 28, 14, 32), null)).toBe('Aug 28, 14:32 → …')
    expect(formatTimingRange(new Date(2026, 7, 28, 14, 32), 'garbage')).toBe('Aug 28, 14:32 → …')
  })

  it("renders '—' when there is no usable start", () => {
    expect(formatTimingRange(null, new Date(2026, 7, 28))).toBe('—')
    expect(formatTimingRange('garbage', null)).toBe('—')
  })
})

describe('timingRangeParts', () => {
  it('flags a same-day range as not crossing a day and drops the repeated date', () => {
    expect(
      timingRangeParts(new Date(2026, 7, 28, 14, 32), new Date(2026, 7, 28, 14, 46)),
    ).toEqual({ head: 'Aug 28, 14:32', tail: '14:46', crossDay: false })
  })

  it('flags a midnight-spanning range as cross-day via the instants, not the string', () => {
    // The overlap fix stacks on this flag; it is computed from the two dates
    // directly (isSameDay), so it holds even for a <24h run across midnight.
    expect(
      timingRangeParts(new Date(2026, 7, 28, 23, 50), new Date(2026, 7, 29, 0, 12)),
    ).toEqual({ head: 'Aug 28, 23:50', tail: 'Aug 29, 00:12', crossDay: true })
  })

  it('flags a range crossing a month boundary as cross-day', () => {
    const parts = timingRangeParts(new Date(2026, 7, 31, 23, 0), new Date(2026, 8, 1, 1, 0))
    expect(parts?.crossDay).toBe(true)
    expect(parts?.tail).toBe('Sep 1, 01:00')
  })

  it('leaves a still-running or unparseable end with a null tail and no cross-day', () => {
    expect(timingRangeParts(new Date(2026, 7, 28, 14, 32), null)).toEqual({
      head: 'Aug 28, 14:32',
      tail: null,
      crossDay: false,
    })
    expect(timingRangeParts(new Date(2026, 7, 28, 14, 32), 'garbage')?.tail).toBeNull()
  })

  it('returns null when there is no usable start', () => {
    expect(timingRangeParts(null, new Date(2026, 7, 28))).toBeNull()
    expect(timingRangeParts('garbage', null)).toBeNull()
  })
})

describe('isoTooltip', () => {
  it('carries full ISO instants so the compact cell loses no precision', () => {
    const t = isoTooltip('2026-08-28T14:32:05.000Z', '2026-08-28T14:46:11.000Z')
    expect(t).toContain('2026-08-28T14:32:05.000Z')
    expect(t).toContain('2026-08-28T14:46:11.000Z')
  })

  it('calls the second instant of a live session a LAST EVENT, not an end', () => {
    // Asserting a finish time for a running session would be a false claim.
    const t = isoTooltip('2026-08-28T14:32:05.000Z', '2026-08-28T14:40:00.000Z', true)
    expect(t).toContain('Still running')
    expect(t).not.toContain('Ended')
  })

  it('reports an unfinished run instead of a fabricated end', () => {
    expect(isoTooltip('2026-08-28T14:32:05.000Z', null)).toContain('not finished')
  })
})
