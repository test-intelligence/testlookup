/**
 * The unit suite's time zone is pinned (vitest.config.ts, R1 F12). These tests
 * fail if the pin stops applying — for example if a shell's `TZ=UTC` (CI's own
 * zone) wins over it — because every date test in the suite silently changes
 * meaning when it does.
 */
import { describe, expect, it } from 'vitest'

describe('the unit suite time zone', () => {
  it('is America/Chicago, whatever the machine or shell zone', () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('America/Chicago')
  })

  it('is west of UTC in both halves of the year, so local midnight is still the previous UTC day', () => {
    // getTimezoneOffset is minutes BEHIND UTC: positive means west.
    expect(new Date('2026-01-15T12:00:00Z').getTimezoneOffset()).toBe(360) // CST
    expect(new Date('2026-07-15T12:00:00Z').getTimezoneOffset()).toBe(300) // CDT
    const utcMidnight = new Date('2026-09-18T00:00:00Z')
    expect(utcMidnight.getDate()).toBe(17)
    expect(utcMidnight.getUTCDate()).toBe(18)
  })
})
