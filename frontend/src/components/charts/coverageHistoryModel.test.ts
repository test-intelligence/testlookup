/**
 * W08 TRND-07: the Coverage History chart's model — a day without a snapshot
 * is a gap in both series (never 0), the axis ends yesterday (a snapshot is a
 * finished day), and timeline notes are placed by the time-series kit's rule.
 */
import { describe, expect, it } from 'vitest'
import type { CoverageHistoryPoint } from '@/types/analytics'
import {
  buildCoverageHistoryModel,
  coverageHistoryToChartSeries,
  EMPTY_CATALOGUE_REASON,
  historyAxis,
  NO_SNAPSHOT_REASON,
} from './coverageHistoryModel'

const p = (day: string, catalogue: number, pct: number | null, exercised = 0): CoverageHistoryPoint => ({
  day,
  catalogue_tests: catalogue,
  exercised_tests: exercised,
  exercised_pct: pct,
  suites: 3,
})

describe('historyAxis', () => {
  it('is `days` UTC days ending yesterday', () => {
    expect(historyAxis(3, '2026-10-16')).toEqual(['2026-10-13', '2026-10-14', '2026-10-15'])
    expect(historyAxis(1, '2026-10-16')).toEqual(['2026-10-15'])
  })

  it('an unparseable today is an empty axis, a bad window one day', () => {
    expect(historyAxis(7, 'today')).toEqual([])
    expect(historyAxis(Number.NaN, '2026-10-16')).toEqual(['2026-10-15'])
  })
})

describe('buildCoverageHistoryModel', () => {
  const points = [p('2026-10-13', 100, 70, 70), p('2026-10-15', 0, null)]

  it('a missing day is a gap in both series; an empty catalogue has no share (null, not 0)', () => {
    const model = buildCoverageHistoryModel({ points, days: 3, todayIso: '2026-10-16' })
    expect(model.rows.map((r) => [r.x, r.catalogue, r.exercisedPct, r.stored])).toEqual([
      ['2026-10-13', 100, 70, true],
      ['2026-10-14', null, null, false],
      ['2026-10-15', 0, null, true],
    ])
    expect(model.stored).toBe(2)
    expect(model.largest).toBe(100)
  })

  it('the table series says why a value is missing', () => {
    const model = buildCoverageHistoryModel({ points, days: 3, todayIso: '2026-10-16' })
    const series = coverageHistoryToChartSeries(model)
    expect(series.series.map((s) => s.label)).toEqual(['Tests in the catalogue', 'Exercised in the last 30 days (%)'])
    expect(series.series[0].points[1]).toMatchObject({ y: null, measured: false, reason: NO_SNAPSHOT_REASON })
    expect(series.series[1].points[2]).toMatchObject({ y: null, measured: false, reason: EMPTY_CATALOGUE_REASON })
    expect(series.series[1].points[0]).toEqual({ x: '2026-10-13', y: 70, n: 100 })
  })

  it('places notes on the axis: a one-day note is a line, a range a band, one outside is counted', () => {
    const model = buildCoverageHistoryModel({
      points,
      days: 3,
      todayIso: '2026-10-16',
      notes: [
        { id: 'a', from: '2026-10-14', to: '2026-10-14', label: 'freeze', author: 'Ana' },
        { id: 'b', from: '2026-10-10', to: '2026-10-13', label: 'migration', author: null },
        { id: 'c', from: '2026-09-01', to: '2026-09-02', label: 'old', author: null },
      ],
    })
    expect(model.notes.map((n) => [n.id, n.kind, n.from, n.to])).toEqual([
      ['a', 'line', '2026-10-14', '2026-10-14'],
      ['b', 'band', '2026-10-13', '2026-10-13'],
    ])
    expect(model.notesOutsideWindow).toBe(1)
  })
})
