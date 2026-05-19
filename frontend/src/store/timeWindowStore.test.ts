/**
 * Tests for the global time-window preference store.
 *
 * Three contracts:
 *   1. Default is 24h (1 day) on a fresh load — matches what the user
 *      asked for ("retained for that user unless changed").
 *   2. ``setDays`` is reflected by subsequent reads (basic store sanity).
 *   3. ``snapToAllowed`` maps any number to the closest value in a
 *      page-specific allowed set so non-overlapping option sets don't
 *      break when the shared window is set elsewhere.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_TIME_WINDOW_DAYS,
  snapToAllowed,
  useTimeWindowStore,
} from './timeWindowStore'


describe('useTimeWindowStore', () => {
  beforeEach(() => {
    // Reset between tests so state doesn't bleed across cases.
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('defaults to 24h (DEFAULT_TIME_WINDOW_DAYS = 1)', () => {
    expect(DEFAULT_TIME_WINDOW_DAYS).toBe(1)
    expect(useTimeWindowStore.getState().days).toBe(1)
  })

  it('setDays mutates the shared state', () => {
    useTimeWindowStore.getState().setDays(7)
    expect(useTimeWindowStore.getState().days).toBe(7)
    useTimeWindowStore.getState().setDays(30)
    expect(useTimeWindowStore.getState().days).toBe(30)
  })
})


describe('snapToAllowed', () => {
  it('returns the value unchanged when it is in the allowed set', () => {
    expect(snapToAllowed(7, [1, 7, 30, 90])).toBe(7)
    expect(snapToAllowed(1, [1, 7, 30, 90])).toBe(1)
  })

  it('snaps to the closest value when not allowed', () => {
    // 14 isn't in [1, 7, 30, 90]; closest is 7 (delta 7) vs 30 (delta 16).
    expect(snapToAllowed(14, [1, 7, 30, 90])).toBe(7)
    // 60 isn't in [1, 7, 30, 90]; closest is 90 (delta 30) vs 30 (delta 30) — ties go to the first one encountered with strict ``<``, so 30 wins because we iterate left-to-right and the first 30 < 30 comparison fails.
    expect(snapToAllowed(60, [1, 7, 30, 90])).toBe(30)
  })

  it('handles the "year" case by snapping down to the page max', () => {
    // 365 (Value Metrics' "Last year") on a page that tops out at 90 should
    // snap to 90, not jump back to a small window.
    expect(snapToAllowed(365, [1, 7, 30, 90])).toBe(90)
  })

  it('handles the special "all time" (0) case used by RunsPage', () => {
    // 0 is in RunsPage's allowed set — keep it.
    expect(snapToAllowed(0, [1, 6, 14, 30, 90, 0])).toBe(0)
    // A shared 7d, snapped to RunsPage's [1,6,14,30,90,0] gives 6 (closer than 14).
    expect(snapToAllowed(7, [1, 6, 14, 30, 90, 0])).toBe(6)
  })
})
