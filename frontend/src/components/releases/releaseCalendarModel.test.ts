/**
 * W11 REL-06: the release calendar's model — six Monday-first week rows of
 * UTC days, a release chip on its day (released_at once released, never a
 * cancelled one), a phase bar per week row it crosses, three lanes then
 * "+N more", static placement classes, and the URL's month.
 */
import { describe, expect, it } from 'vitest'

import type { Release, ReleasePhase } from '@/types/releases'
import {
  COL_SPAN,
  COL_START,
  LANE_ROW,
  layoutWeek,
  monthGrid,
  monthLabel,
  monthParam,
  moreLabel,
  parseMonthParam,
  releaseDay,
  shiftMonth,
} from './releaseCalendarModel'

const TODAY = new Date('2026-10-23T12:00:00Z')

function phase(id: string, start: string | null, end: string | null, status = 'pending'): ReleasePhase {
  return {
    id, release_id: 'r', name: `Phase ${id}`, phase_type: 'qa_testing', status, description: null, order_index: 0,
    planned_start: start, planned_end: end, actual_start: null, actual_end: null, exit_criteria: null, notes: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  }
}

function rel(id: string, overrides: Partial<Release> = {}): Release {
  return {
    id, project_id: 'p', name: id, version: null, description: null, status: 'planning', planned_date: null,
    released_at: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', phases: [],
    ...overrides,
  }
}

describe('monthGrid', () => {
  it('is six Monday-first weeks; a month starting on a Sunday starts the Monday before', () => {
    const weeks = monthGrid('2026-11', TODAY)   // 1 November 2026 is a Sunday
    expect(weeks).toHaveLength(6)
    expect(weeks.every(w => w.length === 7)).toBe(true)
    expect(weeks[0][0]).toMatchObject({ date: '2026-10-26', day: 26, inMonth: false })
    expect(weeks[0][6]).toMatchObject({ date: '2026-11-01', day: 1, inMonth: true })
    expect(weeks[5][6].date).toBe('2026-12-06')
  })

  it('marks today (a UTC day)', () => {
    const today = monthGrid('2026-10', TODAY).flat().filter(d => d.isToday)
    expect(today.map(d => d.date)).toEqual(['2026-10-23'])
  })
})

describe('releaseDay', () => {
  it('is the planned date, the release date once released, and nothing when cancelled', () => {
    expect(releaseDay(rel('a', { planned_date: '2026-11-03T23:30:00Z' }))).toBe('2026-11-03')
    expect(releaseDay(rel('b', { planned_date: '2026-11-03T23:30:00-05:00' }))).toBe('2026-11-04')   // UTC day
    expect(releaseDay(rel('c', { status: 'released', planned_date: '2026-11-03T00:00:00Z', released_at: '2026-11-05T10:00:00Z' }))).toBe('2026-11-05')
    expect(releaseDay(rel('d', { status: 'cancelled', planned_date: '2026-11-03T00:00:00Z' }))).toBeNull()
    expect(releaseDay(rel('e'))).toBeNull()
  })
})

describe('layoutWeek', () => {
  const weeks = monthGrid('2026-11', TODAY)
  const week = (i: number) => weeks[i]

  it('a phase that crosses a week boundary is two bars, each clipped to its row', () => {
    const r = rel('2026.11', { phases: [phase('soak', '2026-11-05T00:00:00Z', '2026-11-11T00:00:00Z')] })
    const first = layoutWeek(week(1), [r]).items       // 2-8 Nov
    const second = layoutWeek(week(2), [r]).items      // 9-15 Nov
    expect(first).toHaveLength(1)
    expect(first[0]).toMatchObject({ colStart: 4, span: 4, continuesBefore: false, continuesAfter: true, lane: 0 })
    expect(second[0]).toMatchObject({ colStart: 1, span: 3, continuesBefore: true, continuesAfter: false })
    expect(first[0].label).toBe('2026.11 · Phase soak')
  })

  it('a release chip sits on its day; a cancelled release and its phases are not shown', () => {
    const items = layoutWeek(week(1), [
      rel('2026.11', { planned_date: '2026-11-04T08:00:00Z' }),
      rel('2026.09', { status: 'cancelled', planned_date: '2026-11-04T08:00:00Z', phases: [phase('x', '2026-11-03T00:00:00Z', null)] }),
    ]).items
    expect(items.map(i => [i.kind, i.releaseId, i.colStart, i.span])).toEqual([['release', '2026.11', 3, 1]])
  })

  it('four overlapping phases: three lanes, then +1 more on each day the fourth covers', () => {
    const r = rel('r', {
      phases: ['a', 'b', 'c', 'd'].map(id => phase(id, '2026-11-03T00:00:00Z', '2026-11-04T00:00:00Z')),
    })
    const layout = layoutWeek(week(1), [r])
    expect(layout.items.map(i => i.lane).sort()).toEqual([0, 1, 2])
    expect(layout.hidden.map(h => h.count)).toEqual([0, 1, 1, 0, 0, 0, 0])
    expect(moreLabel(layout.hidden[1])).toEqual({ text: '+1 more', title: 'r · Phase d' })
    expect(moreLabel(layout.hidden[0])).toBeNull()
  })

  it('a phase dated at one end is one day; an end before its start is its start day', () => {
    const items = layoutWeek(week(1), [rel('r', { phases: [
      phase('end-only', null, '2026-11-06T00:00:00Z'),
      phase('inverted', '2026-11-07T00:00:00Z', '2026-11-02T00:00:00Z'),
    ] })]).items
    expect(items.map(i => [i.label, i.colStart, i.span])).toEqual([
      ['r · Phase end-only', 5, 1], ['r · Phase inverted', 6, 1],
    ])
  })

  it('the placement classes are static and complete', () => {
    expect(COL_START).toEqual(['col-start-1', 'col-start-2', 'col-start-3', 'col-start-4', 'col-start-5', 'col-start-6', 'col-start-7'])
    expect(COL_SPAN).toEqual(['col-span-1', 'col-span-2', 'col-span-3', 'col-span-4', 'col-span-5', 'col-span-6', 'col-span-7'])
    expect(LANE_ROW).toEqual(['row-start-2', 'row-start-3', 'row-start-4'])
  })
})

describe('the month in the URL', () => {
  it('a valid YYYY-MM is kept; anything else is this month', () => {
    expect(parseMonthParam('2026-11', TODAY)).toBe('2026-11')
    for (const bad of [null, '', '2026-13', '2026-1', 'november', '2026-11-01']) {
      expect(parseMonthParam(bad, TODAY)).toBe('2026-10')
    }
    expect(monthParam(TODAY)).toBe('2026-10')
  })

  it('moves across years and reads as a name', () => {
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(monthLabel('2026-11')).toBe('November 2026')
  })
})
