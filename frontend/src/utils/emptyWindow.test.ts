import { describe, expect, it } from 'vitest'

import { describeEmptyWindow, formatAgeDays } from './emptyWindow'

const OPTIONS = [1, 7, 14, 30, 90] as const
const NOW = new Date('2026-08-21T12:00:00Z')

function at(daysAgo: number): string {
  return new Date(NOW.getTime() - daysAgo * 24 * 60 * 60 * 1000).toISOString()
}

describe('describeEmptyWindow', () => {
  it('says nothing when the window has data', () => {
    expect(
      describeEmptyWindow({
        totalInWindow: 390, newestRunAt: at(1), days: 30, options: OPTIONS, now: NOW,
      }),
    ).toEqual({ kind: 'has-data' })
  })

  it('distinguishes "no runs at all" from "none in this window"', () => {
    // A project that has never ingested must NOT be told to widen the window:
    // no window can help, and the suggestion sends them round a loop.
    expect(
      describeEmptyWindow({
        totalInWindow: 0, newestRunAt: null, days: 7, options: OPTIONS, now: NOW,
      }),
    ).toEqual({ kind: 'no-runs-at-all' })
  })

  it('reports the real measured case: 16-day-old data, 7-day window', () => {
    // This is what the deployment actually looked like — every active
    // project's newest run was 14-16 days old while the dashboard sat on a
    // window that excluded it and said nothing.
    const out = describeEmptyWindow({
      totalInWindow: 0, newestRunAt: at(16), days: 7, options: OPTIONS, now: NOW,
    })
    expect(out).toEqual({ kind: 'outside-window', ageDays: 16, suggestedDays: 30 })
  })

  it('never suggests a window that still excludes the run', () => {
    // 14 days old on a 7-day window: 14 is NOT enough (a run exactly 14 days
    // old sits on the boundary), so the suggestion must clear it.
    const out = describeEmptyWindow({
      totalInWindow: 0, newestRunAt: at(14), days: 7, options: OPTIONS, now: NOW,
    })
    expect(out).toMatchObject({ kind: 'outside-window', ageDays: 14 })
    expect(out.kind === 'outside-window' && out.suggestedDays).toBe(30)
  })

  it('never suggests the window the user is already on', () => {
    const out = describeEmptyWindow({
      totalInWindow: 0, newestRunAt: at(20), days: 30, options: OPTIONS, now: NOW,
    })
    expect(out).toMatchObject({ kind: 'outside-window', suggestedDays: 90 })
  })

  it('offers no suggestion when even the widest window cannot reach', () => {
    const out = describeEmptyWindow({
      totalInWindow: 0, newestRunAt: at(400), days: 30, options: OPTIONS, now: NOW,
    })
    expect(out).toMatchObject({ kind: 'outside-window', ageDays: 400, suggestedDays: null })
  })

  it('clamps a future timestamp to 0 rather than rendering a negative age', () => {
    const future = new Date(NOW.getTime() + 6 * 60 * 60 * 1000).toISOString()
    const out = describeEmptyWindow({
      totalInWindow: 0, newestRunAt: future, days: 7, options: OPTIONS, now: NOW,
    })
    expect(out).toMatchObject({ kind: 'outside-window', ageDays: 0 })
  })

  it('treats an unparseable timestamp as "no runs" rather than NaN days', () => {
    expect(
      describeEmptyWindow({
        totalInWindow: 0, newestRunAt: 'not-a-date', days: 7, options: OPTIONS, now: NOW,
      }),
    ).toEqual({ kind: 'no-runs-at-all' })
  })

  it('treats a null total as empty, not as data', () => {
    // The summary can be undefined while loading; a nullish total must not be
    // read as "has data" or the banner would never appear.
    expect(
      describeEmptyWindow({
        totalInWindow: null, newestRunAt: at(20), days: 7, options: OPTIONS, now: NOW,
      }),
    ).toMatchObject({ kind: 'outside-window' })
  })
})

describe('formatAgeDays', () => {
  it('reads naturally at the boundaries', () => {
    expect(formatAgeDays(0)).toBe('earlier today')
    expect(formatAgeDays(1)).toBe('yesterday')
    expect(formatAgeDays(16)).toBe('16 days ago')
  })
})
