/**
 * K5: the Trends day x suite `chart-data` response as a heatmap matrix.
 */
import { describe, expect, it } from 'vitest'
import { VIZ_LIMITS, validateChartSeries, type SeriesChart } from '@/lib/viz/contracts'
import { heatmapFromChartData, OTHER_ROW_LABEL } from './heatmapFromChartData'
import { OTHER_KEY } from './multiSeriesModel'
import { heatmapFrameDense, heatmapFrameMeta, heatmapFrameWorstFirst } from './__fixtures__/heatmapFrame'

const days = ['2026-09-01', '2026-09-02', '2026-09-03']

function chart(series: { key: string; label?: string; ys: (number | null)[]; n?: number[] }[]): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day', 'suite'],
    x_type: 'time',
    series: series.map((s) => ({
      key: s.key,
      label: s.label ?? s.key,
      points: s.ys.map((y, i) => ({ x: days[i], y, n: s.n?.[i] ?? 10 })),
    })),
  }
}

const cellAt = (matrix: ReturnType<typeof heatmapFromChartData>['matrix'], row: string, x: number) => {
  const y = matrix.y_labels.indexOf(row)
  return matrix.cells.find((cell) => cell.x === x && cell.y === y)
}

describe('heatmapFromChartData (K5)', () => {
  it('divides by 100: chart-data sends percentage points, the renderer reads a 0..1 ratio', () => {
    const { matrix } = heatmapFromChartData(chart([{ key: 'auth', ys: [96.4, 100, 0] }]), null)
    expect(matrix.value_type).toBe('rate')
    expect(cellAt(matrix, 'auth', 0)?.value).toBeCloseTo(0.964, 10)
    expect(cellAt(matrix, 'auth', 1)?.value).toBe(1)
    expect(cellAt(matrix, 'auth', 2)?.value).toBe(0)
  })

  it('never hands the renderer a value above 1 from a real pass rate', () => {
    const { matrix } = heatmapFromChartData(heatmapFrameWorstFirst.series as SeriesChart, heatmapFrameMeta)
    for (const cell of matrix.cells) if (cell.value !== null) expect(cell.value).toBeLessThanOrEqual(1)
  })

  it('orders rows worst first by the n-weighted rate', () => {
    const { matrix } = heatmapFromChartData(
      chart([
        { key: 'good', ys: [99, 98, 97] },
        // 50% on a tiny day and 90% on a big one: weighted ~88.2, not the plain mean 76.7.
        { key: 'weighted', ys: [50, 90, null], n: [1, 50, 0] },
        { key: 'bad', ys: [40, 60, 50] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['bad', 'weighted', 'good'])
  })

  it('keeps the server "Other" row last, labelled, whatever its rate', () => {
    const { matrix, rows } = heatmapFromChartData(
      chart([
        { key: OTHER_KEY, label: '__other__', ys: [10, 10, 10] },
        { key: 'a', ys: [90, 90, 90] },
        { key: 'b', ys: [80, 80, 80] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['b', 'a', OTHER_ROW_LABEL])
    expect(OTHER_ROW_LABEL).toBe('Other')
    expect(rows.other).toBe(true)
    expect(rows.shown).toBe(2)
  })

  it('a row with nothing measured sorts after the measured rows (it is not the worst, it is unknown)', () => {
    const { matrix } = heatmapFromChartData(
      chart([
        { key: 'silent', ys: [null, null, null] },
        { key: 'ok', ys: [90, 90, 90] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['ok', 'silent'])
  })

  it('ties keep a stable alphabetical order', () => {
    const { matrix } = heatmapFromChartData(
      chart([
        { key: 'zeta', ys: [80, 80, 80] },
        { key: 'alpha', ys: [80, 80, 80] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['alpha', 'zeta'])
  })

  it('a null point is a null cell (hatched), never the lowest colour', () => {
    const { matrix } = heatmapFromChartData(chart([{ key: 'auth', ys: [90, null, 80] }]), null)
    const cell = cellAt(matrix, 'auth', 1)
    expect(cell?.value).toBeNull()
    expect(cell?.n).toBe(10)
  })

  it('a day a series has no point for is a null cell with n 0', () => {
    const series = chart([
      { key: 'a', ys: [90, 90, 90] },
      { key: 'b', ys: [80, 80, 80] },
    ])
    series.series[1].points = series.series[1].points.slice(0, 1)
    const { matrix } = heatmapFromChartData(series, null)
    expect(cellAt(matrix, 'b', 2)).toEqual({ x: 2, y: matrix.y_labels.indexOf('b'), value: null, n: 0 })
  })

  it('columns are the days in calendar order, whatever order the points came in', () => {
    const series = chart([{ key: 'a', ys: [90, 80, 70] }])
    series.series[0].points.reverse()
    const { matrix } = heatmapFromChartData(series, null)
    expect(matrix.x_labels).toEqual(days)
    expect(cellAt(matrix, 'a', 0)?.value).toBeCloseTo(0.9, 10)
  })

  it('every (row, day) has exactly one cell', () => {
    const { matrix } = heatmapFromChartData(heatmapFrameWorstFirst.series as SeriesChart, heatmapFrameMeta)
    expect(matrix.cells).toHaveLength(matrix.x_labels.length * matrix.y_labels.length)
    expect(new Set(matrix.cells.map((cell) => `${cell.x}:${cell.y}`)).size).toBe(matrix.cells.length)
  })

  it('"top 7 of N" comes from meta.truncated_axes.series', () => {
    const { rows } = heatmapFromChartData(heatmapFrameWorstFirst.series as SeriesChart, heatmapFrameMeta)
    expect(rows).toEqual({ shown: 7, total: 12, other: true })
  })

  it('without truncation info the total is what is shown', () => {
    const { rows } = heatmapFromChartData(chart([{ key: 'a', ys: [1, 2, 3] }]), null)
    expect(rows).toEqual({ shown: 1, total: 1, other: false })
  })

  it('8 rows x 90 days fits the contract cell cap and validates as a C3 matrix', () => {
    const { matrix } = heatmapFromChartData(heatmapFrameDense.series as SeriesChart, null)
    expect(matrix.y_labels).toHaveLength(8)
    expect(matrix.x_labels).toHaveLength(90)
    expect(matrix.cells).toHaveLength(720)
    expect(matrix.cells.length).toBeLessThanOrEqual(VIZ_LIMITS.matrixCells)
    const checked = validateChartSeries(matrix)
    expect(checked.ok ? [] : checked.errors).toEqual([])
  })

  it('a non-finite y is no data, not NaN on a canvas', () => {
    const series = chart([{ key: 'a', ys: [90, 90, 90] }])
    series.series[0].points[0].y = Number.NaN
    const { matrix } = heatmapFromChartData(series, null)
    expect(cellAt(matrix, 'a', 0)?.value).toBeNull()
  })

  it('a series without a label is named by its key', () => {
    const { matrix } = heatmapFromChartData(chart([{ key: 'payments', label: '', ys: [1, 2, 3] }]), null)
    expect(matrix.y_labels).toEqual(['payments'])
  })

  it('with no sample sizes at all, a row is ranked by its plain mean', () => {
    const { matrix } = heatmapFromChartData(
      chart([
        { key: 'mean-60', ys: [50, 70, null], n: [0, 0, 0] },
        { key: 'mean-65', ys: [65, 65, null], n: [0, 0, 0] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['mean-60', 'mean-65'])
  })

  it('two rows with one label and one rate are ordered by key, and two unmeasured rows by label', () => {
    const { matrix } = heatmapFromChartData(
      chart([
        // Same label, same rate; told apart by their sample sizes.
        { key: 'k2', label: 'same', ys: [80, 80, 80], n: [1, 1, 1] },
        { key: 'k1', label: 'same', ys: [80, 80, 80], n: [2, 2, 2] },
        { key: 'z-silent', ys: [null, null, null] },
        { key: 'a-silent', ys: [null, null, null] },
      ]),
      null,
    )
    expect(matrix.y_labels).toEqual(['same', 'same', 'a-silent', 'z-silent'])
    // k1 first: row 0's cells carry k1's sample size.
    expect(matrix.cells.filter((cell) => cell.y === 0).map((cell) => cell.n)).toEqual([2, 2, 2])
  })

  it('a category axis keeps the server order of its columns', () => {
    const series: SeriesChart = { ...chart([{ key: 'a', ys: [1, 2, 3] }]), x_type: 'category' }
    series.series[0].points = [
      { x: 'zeta', y: 1, n: 1 },
      { x: 'alpha', y: 2, n: 1 },
    ]
    expect(heatmapFromChartData(series, null).matrix.x_labels).toEqual(['zeta', 'alpha'])
  })

  it('a sample size that is not a count is written as 0, never NaN or negative', () => {
    const series = chart([{ key: 'a', ys: [90, 90, 90] }])
    series.series[0].points[0].n = Number.NaN
    series.series[0].points[1].n = -4
    series.series[0].points[2].n = 2.6
    const { matrix } = heatmapFromChartData(series, null)
    expect(matrix.cells.map((cell) => cell.n)).toEqual([0, 0, 3])
  })

  it('an empty response is an empty matrix', () => {
    const { matrix, rows } = heatmapFromChartData({ ...chart([]), series: [] }, null)
    expect(matrix.cells).toEqual([])
    expect(rows).toEqual({ shown: 0, total: 0, other: false })
  })
})
