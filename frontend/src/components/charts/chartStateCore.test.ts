/**
 * `seriesHasPoints` — whether a drawn state has anything to plot, tabulate or
 * export (ChartFrame's "plotted"), for every kind of series. A position whose
 * value is null still COUNTS (a gap the table states as "—"); only a series
 * with no positions at all is empty. Unlike `isChartSeriesEmpty`, which asks
 * whether anything was MEASURED.
 */
import { describe, expect, it } from 'vitest'
import type { ChartSeries } from '@/lib/viz/contracts'
import { seriesHasPoints } from './chartStateCore'
import { isChartSeriesEmpty } from './chartState'

const series = (points: { x: string; y: number | null; n: number }[][]): ChartSeries => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: points.map((p, i) => ({ key: `s${i}`, label: `s${i}`, points: p })),
})

describe('seriesHasPoints — a position counts, measured or not', () => {
  it('a line series: any line with a point, a null-valued one included', () => {
    expect(seriesHasPoints(series([[{ x: '2026-09-01', y: 90, n: 4 }]]))).toBe(true)
    const gapOnly = series([[], [{ x: '2026-09-01', y: null, n: 0 }]])
    expect(seriesHasPoints(gapOnly)).toBe(true)
    // …which is exactly where it parts from "anything measured".
    expect(isChartSeriesEmpty(gapOnly)).toBe(true)
    expect(seriesHasPoints(series([[], []]))).toBe(false)
    expect(seriesHasPoints(series([]))).toBe(false)
  })

  it('a matrix: any cell, an empty (null) one included', () => {
    const matrix = (cells: { x: number; y: number; value: number | null; n: number }[]): ChartSeries => ({
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['a'],
      y_labels: ['b'],
      cells,
    })
    expect(seriesHasPoints(matrix([{ x: 0, y: 0, value: null, n: 0 }]))).toBe(true)
    expect(seriesHasPoints(matrix([]))).toBe(false)
  })

  it('a tree: any node', () => {
    expect(seriesHasPoints({ kind: 'tree', nodes: [{ id: 'a', parent_id: null, label: 'a', value: 0, measure: null }] })).toBe(true)
    expect(seriesHasPoints({ kind: 'tree', nodes: [] })).toBe(false)
  })

  it('a graph: any node, with or without edges', () => {
    expect(seriesHasPoints({ kind: 'graph', nodes: [{ id: 'a', label: 'a', size: 0 }], edges: [] })).toBe(true)
    expect(seriesHasPoints({ kind: 'graph', nodes: [], edges: [] })).toBe(false)
  })
})
