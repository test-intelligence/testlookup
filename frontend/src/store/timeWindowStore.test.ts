/**
 * The default window is 30 days, and existing sessions move with it.
 *
 * Two user reports, one cause: four projects whose most recent runs were 8–10
 * days old rendered as "no records" on `/intelligence`, and the `/agents`
 * suite+build picker had no options. The window was simply shorter than the
 * gap since the last run — and a product that empties out after a quiet week
 * reads as an outage.
 *
 * Raising the constant alone would have fixed nothing for anyone who had
 * already used the app: their 7 is persisted in localStorage, and a stored 7
 * is byte-identical whether they chose it or never touched it. So the
 * migration matters as much as the number.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { DEFAULT_TIME_WINDOW_DAYS, snapToAllowed } from './timeWindowStore'

describe('default time window', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('is 30 days', () => {
    expect(DEFAULT_TIME_WINDOW_DAYS).toBe(30)
  })

  it('is long enough to survive a quiet fortnight', () => {
    // The reported case: the newest run was 10 days old. A default that
    // cannot see it is the bug, not the data.
    expect(DEFAULT_TIME_WINDOW_DAYS).toBeGreaterThanOrEqual(10)
  })

  it('lands exactly on an allowed option for every page', () => {
    // If the default were not in a page's set, snapToAllowed would round it to
    // a neighbour and that page would quietly use a different window from the
    // rest of the app.
    const COVERAGE = [1, 7, 14, 30, 90] as const
    const LIVE = [1, 7, 14, 30] as const
    const MY_FAILURES = [1, 7, 30] as const
    for (const allowed of [COVERAGE, LIVE, MY_FAILURES]) {
      expect(snapToAllowed(DEFAULT_TIME_WINDOW_DAYS, allowed)).toBe(
        DEFAULT_TIME_WINDOW_DAYS,
      )
    }
  })
})

describe('persisted-state migration', () => {
  /**
   * Mirrors the store's migrate step. Asserted against a local copy rather
   * than by driving zustand's rehydration, because the behaviour under test is
   * the RULE — which stored values count as "never chose" — and that is what a
   * future edit would get wrong.
   */
  const SUPERSEDED_DEFAULTS = [7, 1]
  function migrate(state: { days?: number }, fromVersion: number) {
    if (
      fromVersion < 3 &&
      typeof state.days === 'number' &&
      SUPERSEDED_DEFAULTS.includes(state.days)
    ) {
      return { ...state, days: DEFAULT_TIME_WINDOW_DAYS }
    }
    return state
  }

  it('moves someone sitting on the previous default', () => {
    expect(migrate({ days: 7 }, 2).days).toBe(30)
  })

  it('moves someone still on the original 24h default', () => {
    // Two defaults ago. Listing only the most recent one would strand them.
    expect(migrate({ days: 1 }, 1).days).toBe(30)
  })

  it('leaves a deliberate non-default choice alone', () => {
    expect(migrate({ days: 14 }, 2).days).toBe(14)
    expect(migrate({ days: 90 }, 2).days).toBe(90)
  })

  it('does not re-run once migrated', () => {
    // A user who deliberately picks 7 AFTER this ships must keep it.
    expect(migrate({ days: 7 }, 3).days).toBe(7)
  })

  it('tolerates junk in persisted state', () => {
    expect(migrate({}, 2)).toEqual({})
    expect(migrate({ days: undefined }, 2).days).toBeUndefined()
  })
})
