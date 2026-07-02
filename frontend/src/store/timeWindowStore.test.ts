/**
 * Tests for the global time-window preference store.
 *
 * v2 (2026-05-18): default flipped from 24h → 7d. The migrate
 * function re-seeds users who had taken the implicit-default 24h
 * but preserves users who explicitly picked 24h post-v2.
 *
 * Contracts pinned here:
 *   1. Default on a fresh load is ``DEFAULT_TIME_WINDOW_DAYS`` (now 7).
 *   2. ``setDays`` is reflected by subsequent reads.
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

  it('defaults to 7d (DEFAULT_TIME_WINDOW_DAYS = 7) — bumped from 24h in v2', () => {
    expect(DEFAULT_TIME_WINDOW_DAYS).toBe(7)
    expect(useTimeWindowStore.getState().days).toBe(7)
  })

  it('setDays mutates the shared state', () => {
    useTimeWindowStore.getState().setDays(14)
    expect(useTimeWindowStore.getState().days).toBe(14)
    useTimeWindowStore.getState().setDays(30)
    expect(useTimeWindowStore.getState().days).toBe(30)
  })

  it('explicitly preserves 24h once a user picks it', () => {
    // The v2 migrate only re-seeds the implicit-default v1 state.
    // Picking 1 (24h) post-v2 should be a stable preference.
    useTimeWindowStore.getState().setDays(1)
    expect(useTimeWindowStore.getState().days).toBe(1)
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
    // 0 ("all time") is in RunsPage's allowed set — keep it.
    expect(snapToAllowed(0, [1, 7, 14, 30, 90, 0])).toBe(0)
    // Regression: RunsPage's set now includes 7 (was an anomalous 6), so the
    // global 7-day default stays 7 here instead of silently drifting to 6 —
    // the window a user set elsewhere is the window the Runs page queries.
    expect(snapToAllowed(7, [1, 7, 14, 30, 90, 0])).toBe(7)
  })
})
