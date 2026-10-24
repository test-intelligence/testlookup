/**
 * W11 REL-03: the KPI cards' trend as drawn — captions are counts over their
 * stated denominators (and say so when there is nothing), titles name the
 * week and the approximation, a `null` point stays not measured.
 */
import { describe, expect, it } from 'vitest'

import type { ReleasePortfolioTotals, ReleasePortfolioTrend } from '@/types/releases'
import { packCaption, pointTitle, readyCaption, sparklineFor, utcDayLabel } from './portfolioModel'

const TOTALS: ReleasePortfolioTotals = {
  evaluated: 4, go: 3, conditional_go: 1, no_go: 0, not_evaluated: 0, released: 5, released_with_pack: 2,
}

function trend(overrides: Partial<ReleasePortfolioTrend> = {}): ReleasePortfolioTrend {
  return {
    weeks: 4,
    points_at: ['2026-09-28T00:00:00Z', '2026-10-05T00:00:00Z', '2026-10-12T00:00:00Z', '2026-10-19T00:00:00Z', '2026-10-23T12:00:00Z'],
    in_progress: [null, 1, 2, 2, 3],
    ready_to_ship: [null, 0, 1, 1, 1],
    blocked: [null, 0, 0, 1, 1],
    released_30d: [null, 0, 0, 1, 2],
    evaluated_in_week: [0, 0, 3, 1, 2],
    go_in_week: [0, 0, 1, 1, 1],
    totals: TOTALS,
    approximated: ['in_progress', 'ready_to_ship', 'blocked'],
    ...overrides,
  }
}

describe('captions', () => {
  it('Ready to ship: GO on N of M evaluated, or that none was', () => {
    expect(readyCaption(TOTALS, 12)).toBe('GO on 3 of 4 evaluated (12 w)')
    expect(readyCaption({ ...TOTALS, evaluated: 0, go: 0 }, 12)).toBe('No releases evaluated in 12 weeks')
  })
  it('Released · 30d: N of M released with a pack, or that none was released', () => {
    expect(packCaption(TOTALS, 12)).toBe('2 of 5 released with a pack (12 w)')
    expect(packCaption({ ...TOTALS, released: 0, released_with_pack: 0 }, 12)).toBe('No releases released in 12 weeks')
  })
})

describe('point titles', () => {
  it('names the week (its Monday, a UTC day) and the approximation of a today-status series', () => {
    expect(utcDayLabel('2026-10-13T00:00:00Z')).toBe('13 Oct')
    expect(pointTitle('in_progress', '2026-10-13T00:00:00Z', 2, { approximated: true }))
      .toBe("Week to 13 Oct: 2 in progress (approximation: today's status)")
    expect(pointTitle('ready_to_ship', '2026-10-13T00:00:00Z', 1, { approximated: true, evaluatedInWeek: 3, goInWeek: 1 }))
      .toBe("Week to 13 Oct: 1 ready to ship · GO 1 of 3 evaluated that week (approximation: today's status)")
    expect(pointTitle('released_30d', '2026-10-13T00:00:00Z', 3)).toBe('Week to 13 Oct: 3 released in 30 days')
  })
  it('the last point is now, and says when the card has moved on since the read', () => {
    expect(pointTitle('in_progress', '2026-10-23T12:00:00Z', 2, { isNow: true, approximated: true })).toBe('Now: 2 in progress')
    expect(pointTitle('blocked', '2026-10-23T12:00:00Z', 1, { isNow: true, stale: true }))
      .toBe('Now (as of the trend read): 1 blocked')
  })
  it('a point that was not measured has no title', () => {
    expect(pointTitle('in_progress', '2026-09-28T00:00:00Z', null)).toBeNull()
  })
})

describe('sparklineFor', () => {
  it('keeps null points null, titles each measured point, and is drawable from two measured points', () => {
    const line = sparklineFor(trend(), 'ready_to_ship', 1)
    expect(line.series).toEqual([null, 0, 1, 1, 1])
    expect(line.pointTitles[0]).toBeNull()
    expect(line.pointTitles[2]).toBe("Week to 12 Oct: 1 ready to ship · GO 1 of 3 evaluated that week (approximation: today's status)")
    expect(line.pointTitles[4]).toBe('Now: 1 ready to ship')
    expect(line.label).toBe('Ready to ship, 4 weeks')
    expect(line.drawable).toBe(true)
  })
  it('the card\'s own count differing from the trend\'s now point marks the title', () => {
    expect(sparklineFor(trend(), 'in_progress', 4).pointTitles[4]).toBe('Now (as of the trend read): 3 in progress')
  })
  it('fewer than two measured points is not drawable; every-null draws nothing', () => {
    expect(sparklineFor(trend({ blocked: [null, null, null, null, 0] }), 'blocked').drawable).toBe(false)
    expect(sparklineFor(trend({ in_progress: [null, null, null, null, null] }), 'in_progress').drawable).toBe(false)
  })
})
