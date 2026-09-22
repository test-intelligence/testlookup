import { describe, expect, it } from 'vitest'
import type { ChartSeries, MatrixChart, SeriesChart } from '@/lib/viz/contracts'
import {
  chartTableModel,
  defaultFormatter,
  formatChartValue,
  formatPlainValue,
  NO_DATA,
  NO_VALUE,
  summarizeChart,
} from './chartText'

const LINE: SeriesChart = {
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [
    {
      key: 'passed',
      label: 'Passed',
      points: [
        { x: '09-01', y: 10, n: 10 },
        { x: '09-02', y: 4, n: 4 },
        { x: '09-03', y: null, n: 0 },
        { x: '09-04', y: 12, n: 12 },
      ],
    },
    { key: 'failed', label: 'Failed', points: [{ x: '09-01', y: 1, n: 1 }, { x: '09-05', y: 3, n: 3 }] },
    { key: 'none', label: 'Broken', points: [{ x: '09-01', y: null, n: 0 }] },
  ],
}

const MATRIX: MatrixChart = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['d1', 'd2'],
  y_labels: ['auth', 'cart'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 2 },
    { x: 1, y: 0, value: 1, n: 2 },
    { x: 0, y: 1, value: 0.25, n: 4 },
    { x: 1, y: 1, value: null, n: 0 },
  ],
}

describe('formatChartValue', () => {
  it('prints no data as "—", never zero', () => {
    const fmt = defaultFormatter(LINE)
    expect(formatChartValue(null, fmt)).toBe(NO_VALUE)
    expect(formatChartValue(undefined, fmt)).toBe(NO_VALUE)
    expect(formatChartValue(Number.NaN, fmt)).toBe(NO_VALUE)
    expect(formatChartValue(0, fmt)).toBe('0')
    expect(formatChartValue('failed', fmt)).toBe('failed')
    expect(defaultFormatter(MATRIX)(0.123)).toBe('12.3%')
  })
})

describe('summarizeChart', () => {
  it('names the chart type, axes, scope, and min / max / latest per series', () => {
    const text = summarizeChart({ chartType: 'Line chart', series: LINE, axes: { y: 'Tests' }, scope: 'payments, last 7 days' })
    expect(text).toContain('Line chart of 3 series.')
    expect(text).toContain('X axis: day (5 values).')
    expect(text).toContain('Y axis: Tests.')
    expect(text).toContain('Scope: payments, last 7 days.')
    // null is skipped, never read as a zero minimum; latest is the last MEASURED point.
    expect(text).toContain('Passed: min 4 (09-02), max 12 (09-04), latest 12 (09-04).')
    expect(text).toContain('Failed: min 1 (09-01), max 3 (09-05), latest 3 (09-05).')
    expect(text).toContain('Broken: no data.')
  })

  it('summarises a matrix with its extremes, the latest column and the missing cells', () => {
    const text = summarizeChart({ chartType: 'Heatmap', series: MATRIX, axes: { x: 'Day', y: 'Suite' } })
    expect(text).toContain('Heatmap. X axis: Day (2 values). Y axis: Suite (2 values).')
    expect(text).toContain('Min 25.0% (cart, d1), max 100.0% (auth, d2).')
    expect(text).toContain('Latest (d2): auth 100.0%.')
    expect(text).toContain('1 cell with no data.')
  })

  it('handles status matrices, an all-empty matrix, trees and graphs', () => {
    const status: MatrixChart = { ...MATRIX, value_type: 'status', cells: [{ x: 0, y: 0, value: 'failed', n: 1 }, { x: 1, y: 0, value: 'failed', n: 1 }] }
    expect(summarizeChart({ chartType: 'Grid', series: status })).toContain('Cells by status: failed 2.')
    expect(summarizeChart({ chartType: 'Grid', series: { ...status, cells: [] } })).toContain('No cell has a status.')
    const empty: MatrixChart = { ...MATRIX, cells: [{ x: 0, y: 0, value: null, n: 0 }] }
    expect(summarizeChart({ chartType: 'Heatmap', series: empty })).toContain('No cell has a measured value.')
    const tree: ChartSeries = {
      kind: 'tree',
      nodes: [
        { id: 'r', parent_id: null, label: 'all', value: 10, measure: null },
        { id: 'a', parent_id: 'r', label: 'auth', value: 3, measure: 0.5 },
      ],
    }
    expect(summarizeChart({ chartType: 'Treemap', series: tree })).toBe('Treemap of 2 nodes. Largest all 10, smallest auth 3.')
    const graph: ChartSeries = { kind: 'graph', nodes: [{ id: 'a', label: 'A', size: 1 }], edges: [] }
    expect(summarizeChart({ chartType: 'Network', series: graph })).toBe('Network of 1 node and 0 links.')
  })
})

describe('chartTableModel', () => {
  it('series: one row per x, one column per series, gaps as "—"', () => {
    const model = chartTableModel(LINE)
    expect(model.columns).toEqual(['day', 'Passed', 'Failed', 'Broken'])
    expect(model.rows).toEqual([
      { header: '09-01', cells: ['10', '1', NO_VALUE] },
      { header: '09-02', cells: ['4', NO_VALUE, NO_VALUE] },
      { header: '09-03', cells: [NO_VALUE, NO_VALUE, NO_VALUE] },
      { header: '09-04', cells: ['12', NO_VALUE, NO_VALUE] },
      { header: '09-05', cells: [NO_VALUE, '3', NO_VALUE] },
    ])
  })

  it('matrix: one row per y label, one column per x label', () => {
    const model = chartTableModel(MATRIX, { y: 'Suite' })
    expect(model.columns).toEqual(['Suite', 'd1', 'd2'])
    expect(model.rows).toEqual([
      { header: 'auth', cells: ['50.0%', '100.0%'] },
      { header: 'cart', cells: ['25.0%', NO_DATA] },
    ])
  })

  it('tree and graph', () => {
    const tree = chartTableModel({
      kind: 'tree',
      nodes: [
        { id: 'r', parent_id: null, label: 'all', value: 10, measure: null },
        { id: 'a', parent_id: 'r', label: 'auth', value: 3, measure: 0.5 },
        { id: 'o', parent_id: 'gone', label: 'orphan', value: 1, measure: null },
      ],
    })
    expect(tree.columns).toEqual(['Node', 'Parent', 'Value', 'Measure'])
    expect(tree.rows).toEqual([
      { header: 'all', cells: [NO_VALUE, '10', NO_VALUE] },
      { header: 'auth', cells: ['all', '3', '0.5'] },
      { header: 'orphan', cells: ['gone', '1', NO_VALUE] },
    ])
    const graph = chartTableModel({
      kind: 'graph',
      nodes: [
        { id: 'a', label: 'A', size: 1 },
        { id: 'b', label: 'B', size: 1 },
      ],
      edges: [
        { source: 'a', target: 'b', weight: 0.5 },
        { source: 'x', target: 'y', weight: 1 },
      ],
    })
    expect(graph.rows).toEqual([
      { header: 'A', cells: ['B', '0.5'] },
      { header: 'x', cells: ['y', '1'] },
    ])
  })

  it('time series: rows are chronological, not in order of first appearance', () => {
    const chart: SeriesChart = {
      kind: 'series',
      dimensions: ['day'],
      x_type: 'time',
      series: [
        { key: 'a', label: 'A', points: [{ x: '2026-09-03', y: 3, n: 1 }] },
        {
          key: 'b',
          label: 'B',
          points: [
            { x: '2026-09-01', y: 1, n: 1 },
            { x: '2026-09-02', y: 2, n: 1 },
            { x: '2026-09-03', y: 4, n: 1 },
          ],
        },
      ],
    }
    const model = chartTableModel(chart)
    expect(model.rows.map((r) => r.header)).toEqual(['2026-09-01', '2026-09-02', '2026-09-03'])
    expect(model.rows[2].cells).toEqual(['3', '4'])
  })

  it('category series: rows keep the given order (stable)', () => {
    const chart: SeriesChart = {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: [{ key: 'a', label: 'A', points: ['zeta', 'alpha', 'mid'].map((x, i) => ({ x, y: i, n: 1 })) }],
    }
    expect(chartTableModel(chart).rows.map((r) => r.header)).toEqual(['zeta', 'alpha', 'mid'])
  })

  it('a duplicate x within one series is kept and flagged — never silently overwritten', () => {
    const chart: SeriesChart = {
      kind: 'series',
      dimensions: ['day'],
      x_type: 'category',
      series: [
        { key: 'a', label: 'A', points: [{ x: 'd1', y: 1, n: 1 }, { x: 'd2', y: 5, n: 1 }, { x: 'd1', y: 2, n: 1 }] },
        { key: 'b', label: 'B', points: [{ x: 'd1', y: 7, n: 1 }] },
      ],
    }
    const model = chartTableModel(chart)
    expect(model.rows).toEqual([
      { header: 'd1', cells: ['1', '7'] },
      { header: 'd1 (duplicate)', cells: ['2', NO_VALUE], duplicate: true },
      { header: 'd2', cells: ['5', NO_VALUE] },
    ])
    expect(model.warnings).toEqual(['A has more than one value at d1.'])
    expect(chartTableModel(LINE).warnings).toEqual([])
  })

  it('numbers go through the shared formatter: no float noise, grouped thousands', () => {
    expect(defaultFormatter(LINE)(0.1 + 0.2)).toBe('0.3')
    expect(defaultFormatter(LINE)(1234)).toBe('1,234')
  })

  it('a matrix far past the spread-argument limit still summarises', () => {
    const cells = Array.from({ length: 300_000 }, (_, i) => ({ x: i, y: 0, value: 0.5, n: 1 }))
    const big: MatrixChart = { kind: 'matrix', value_type: 'rate', x_labels: cells.map((c) => `d${c.x}`), y_labels: ['s'], cells }
    expect(summarizeChart({ chartType: 'Heatmap', series: big })).toContain('Latest (d299999)')
  })

  it('property: every plotted value appears exactly once, at its (row, column) — seeded random series', () => {
    // A tiny deterministic PRNG (mulberry32): the property is checked over many shapes, reproducibly.
    let seed = 0x5eed
    const random = () => {
      seed = (seed + 0x6d2b79f5) | 0
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
    for (let trial = 0; trial < 50; trial++) {
      const seriesCount = 1 + Math.floor(random() * 8)
      const xs = Array.from({ length: 1 + Math.floor(random() * 40) }, (_, i) => `x${i}`)
      const chart: SeriesChart = {
        kind: 'series',
        dimensions: ['x'],
        x_type: 'category',
        series: Array.from({ length: seriesCount }, (_, s) => ({
          key: `s${s}`,
          label: `S${s}`,
          points: xs.filter(() => random() > 0.2).map((x) => ({ x, y: random() > 0.1 ? Math.round(random() * 1000) : null, n: 1 })),
        })),
      }
      const model = chartTableModel(chart)
      expect(model.columns).toEqual(['x', ...chart.series.map((s) => s.label)])
      for (const [s, one] of chart.series.entries()) {
        for (const point of one.points) {
          const row = model.rows.find((r) => r.header === point.x)
          expect(row?.cells[s]).toBe(point.y === null ? NO_VALUE : formatPlainValue(point.y))
        }
      }
      // …and nothing that was not plotted: every filled cell traces back to a point.
      const plotted = new Set(chart.series.flatMap((one, s) => one.points.map((p) => `${p.x}|${s}`)))
      for (const row of model.rows)
        row.cells.forEach((cell, s) => {
          if (cell !== NO_VALUE) expect(plotted.has(`${row.header}|${s}`)).toBe(true)
        })
    }
  })
})
