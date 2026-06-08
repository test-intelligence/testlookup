import { describe, expect, it } from 'vitest'

import { formatRunWhen } from './formatters'

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
