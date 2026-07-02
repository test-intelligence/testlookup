import { describe, expect, it } from 'vitest'

import { dayTimeAgo, formatRunWhen } from './formatters'

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
