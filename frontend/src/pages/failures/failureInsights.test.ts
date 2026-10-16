/**
 * W07 FAIL-02: the time-to-fix words and score, alone.
 *
 * `formatFixTime`: the contract's own examples fix the hours/days boundary
 * between 30 h and 43.2 h (30 h → `30.0 h`, 43.2 h → `1.8 d`); it is 36 h.
 * `timeToFixScore`: 0 h → 100, a week or more → 0, linear between, clamped.
 * `measuredTimeToFix`: a median only counts when the server says measured.
 */
import { describe, expect, it } from 'vitest'

import {
  FIX_TIME_HOURS_LIMIT, formatFixTime, measuredTimeToFix, timeToFixHint, timeToFixScore,
} from './failureInsights'
import type { TimeToFixResponse } from '@/types/analytics'

const RESPONSE: TimeToFixResponse = {
  measured: true, reason: null, median_hours: 43.2, mean_hours: 60.5, fixed: 23, open: 4,
  excluded_flaky_tests: 2, excluded_oscillating_tests: 1, min_fixes: 5, window_days: 14,
}

describe('formatFixTime', () => {
  it.each([
    [0, '0.0 h'],
    [30, '30.0 h'],
    [35.94, '35.9 h'],
    [FIX_TIME_HOURS_LIMIT, '1.5 d'],
    [43.2, '1.8 d'],
    [168, '7.0 d'],
  ])('%s h → %s', (hours, text) => {
    expect(formatFixTime(hours)).toBe(text)
  })
})

describe('timeToFixScore', () => {
  it('scores 100 at 0 h, 0 from a week on, linear between (43.2 h → 74)', () => {
    expect(timeToFixScore(0)).toBe(100)
    expect(Math.round(timeToFixScore(43.2))).toBe(74)
    expect(timeToFixScore(84)).toBe(50)
    expect(timeToFixScore(168)).toBe(0)
    expect(timeToFixScore(500)).toBe(0)
    expect(timeToFixScore(-1)).toBe(100)
  })
})

describe('measuredTimeToFix', () => {
  it('needs the server to say measured AND a finite median', () => {
    expect(measuredTimeToFix(RESPONSE)).toBe(true)
    expect(measuredTimeToFix({ ...RESPONSE, measured: false })).toBe(false)
    expect(measuredTimeToFix({ ...RESPONSE, median_hours: null })).toBe(false)
    expect(measuredTimeToFix(undefined)).toBe(false)
  })
})

describe('timeToFixHint', () => {
  it('names the window, what is left out (flaky + oscillating) and the open episodes', () => {
    expect(timeToFixHint(RESPONSE)).toBe(
      'First failure → next pass, per test, over the last 14 days. Tests flagged flaky and tests that recovered 3+ times are left out (3); 4 still failing are not counted. A failure already open when the window starts counts from its first failure inside it.',
    )
  })
})
