/**
 * VIZ-501: the one adapter between `/analytics/heatmap`'s C3 matrix and the
 * renderer. The three mutations the plan names live here: M-501a (forget the
 * `/ 100`), M-501b (`null -> 0`) and M-501c (re-sorting moves the cells but
 * not `y_keys`, so a drill would open the wrong suite).
 */
import { describe, expect, it } from 'vitest'
import { validateChartSeries, type EnvelopeMeta, type MatrixChart, type StatusCounts } from '@/lib/viz/contracts'
import {
  columnsAreDays,
  fittedDomain,
  fittedDomainNote,
  HEATMAP_ROW_LABEL_CHARS,
  heatmapCellMark,
  heatmapColumnsNote,
  heatmapDescription,
  heatmapFromMatrix,
  heatmapOrderNote,
  heatmapRowOrder,
  heatmapRowsNote,
  disambiguatedLabels,
  HEATMAP_MIN_CUT,
  PARTIAL_DAY_SUFFIX,
  printedRowLabels,
  printedRunLabels,
  runColumnLabel,
  SUITE_DAY_NOUNS,
} from './heatmapFromMatrix'
import { heatmapTooltipContent } from './engines/echarts/heatmapOption'
import { tooltipText } from './tooltip'
import type { HeatmapMatrix, NumericMatrix } from './engines/echarts/heatmapOption'
import {
  HEATMAP_FRAME_HOSTILE_NAME,
  HEATMAP_FRAME_LONG_NAME,
  heatmapFrameDense,
  heatmapFrameEdges,
  heatmapFrameHostile,
  heatmapFrameMeta,
  heatmapFrameStatus,
  heatmapFrameWorstFirst,
} from './__fixtures__/heatmapFrame'

const counts = (over: Partial<StatusCounts>): StatusCounts => ({ passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 0, ...over })

/** A one-column rate matrix, one row per entry. */
function column(rows: { key: string; label?: string; value: number | null; n: number; counts?: StatusCounts }[], unit?: MatrixChart['unit']): MatrixChart {
  return {
    kind: 'matrix',
    value_type: 'rate',
    ...(unit ? { unit } : {}),
    x_labels: ['2026-09-01'],
    x_keys: ['2026-09-01'],
    y_labels: rows.map((row) => row.label ?? row.key),
    y_keys: rows.map((row) => row.key),
    cells: rows.map((row, y) => ({ x: 0, y, value: row.value, n: row.n, ...(row.counts ? { counts: row.counts } : {}) })),
  }
}

const values = (matrix: HeatmapMatrix) => matrix.cells.map((cell) => cell.value)
const ratios = (matrix: HeatmapMatrix) => values(matrix).filter((v): v is number => typeof v === 'number')

describe('heatmapFromMatrix: units (M-501a)', () => {
  it('divides percentage points by 100 once, and says the result is a ratio', () => {
    const { matrix } = heatmapFromMatrix(column([{ key: 'a', value: 96.4, n: 10 }], 'percent'), null)
    expect(matrix.cells[0].value).toBeCloseTo(0.964, 10)
    expect(matrix).toMatchObject({ value_type: 'rate', unit: 'ratio' })
  })

  it('reads an ABSENT unit as percent (contract OD-7)', () => {
    const { matrix } = heatmapFromMatrix(column([{ key: 'a', value: 50, n: 10 }]), null)
    expect(matrix.cells[0].value).toBe(0.5)
  })

  it('passes a matrix that already says ratio through, undivided', () => {
    const { matrix } = heatmapFromMatrix(column([{ key: 'a', value: 0.5, n: 10 }], 'ratio'), null)
    expect(matrix.cells[0].value).toBe(0.5)
  })

  it('never hands the renderer a value above 1 from a real endpoint response', () => {
    for (const fixture of [heatmapFrameWorstFirst, heatmapFrameDense, heatmapFrameHostile, heatmapFrameEdges]) {
      const { matrix } = heatmapFromMatrix(fixture.series, fixture.meta)
      expect(Math.max(...ratios(matrix))).toBeLessThanOrEqual(1)
      expect(Math.max(...ratios(matrix))).toBeGreaterThan(0.5)
    }
  })

  it('a count matrix is not divided, and gets no unit', () => {
    const chart: MatrixChart = { ...column([{ key: 'a', value: 7, n: 7 }]), value_type: 'count' }
    const { matrix } = heatmapFromMatrix(chart, null)
    expect(matrix.cells[0].value).toBe(7)
    expect('unit' in matrix).toBe(false)
  })
})

describe('heatmapFromMatrix: null is never zero (M-501b)', () => {
  it('a cell nobody ran stays null, n 0', () => {
    const { matrix } = heatmapFromMatrix(column([{ key: 'a', value: null, n: 0, counts: counts({}) }]), null)
    expect(matrix.cells[0]).toMatchObject({ value: null, n: 0 })
  })

  it('a skipped-only cell stays null with its n and counts (the tooltip says "Nothing evaluated")', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameEdges.series, heatmapFrameEdges.meta)
    const proto = matrix.y_labels.indexOf('__proto__')
    const cell = matrix.cells.find((c) => c.y === proto && c.x === 2)
    expect(cell).toMatchObject({ value: null, n: 4, counts: counts({ skipped: 4 }) })
  })

  it('a non-finite value is no data, not NaN on a canvas', () => {
    const chart = column([{ key: 'a', value: Number.NaN, n: 3 }])
    expect(heatmapFromMatrix(chart, null).matrix.cells[0].value).toBeNull()
  })

  it('keeps the two hatched cells of the Trends fixture', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameWorstFirst.series, heatmapFrameMeta)
    expect(matrix.cells.filter((cell) => cell.value === null)).toHaveLength(2)
    expect(matrix.cells).toHaveLength(7 * 14)
  })
})

describe('heatmapFromMatrix: row order', () => {
  it('worst first draws the lowest pass rate on top, not the server’s most-failures row', () => {
    const series = heatmapFrameWorstFirst.series
    // The server ranked by failures: the busiest bad suite first, not the worst rate.
    expect(series.y_labels[0]).not.toBe('legacy-import')
    const { matrix } = heatmapFromMatrix(series, heatmapFrameMeta)
    expect(matrix.y_labels[0]).toBe('legacy-import')
    expect(matrix.y_labels[matrix.y_labels.length - 1]).toBe('auth')
  })

  it('the window rate comes from the counts, skipped OUTSIDE the denominator', () => {
    // a: 8 of 10 evaluated passed (80%), 90 skipped. b: 70 of 100 (70%).
    // Skipped in the denominator would make a 8% and put it first.
    const chart = column([
      { key: 'a', value: 80, n: 100, counts: counts({ passed: 8, failed: 2, skipped: 90 }) },
      { key: 'b', value: 70, n: 100, counts: counts({ passed: 70, failed: 30 }) },
    ])
    expect(heatmapFromMatrix(chart, null).matrix.y_keys).toEqual(['b', 'a'])
  })

  it('without counts, a row is ranked by its n-weighted rate', () => {
    const chart: MatrixChart = {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['d1', 'd2'],
      y_labels: ['a', 'b'],
      cells: [
        // a: 50% on 1 execution, 95% on 99: weighted 94.55%, plain mean 72.5%.
        { x: 0, y: 0, value: 50, n: 1 },
        { x: 1, y: 0, value: 95, n: 99 },
        // b: 80% flat.
        { x: 0, y: 1, value: 80, n: 50 },
        { x: 1, y: 1, value: 80, n: 50 },
      ],
    }
    expect(heatmapFromMatrix(chart, null).matrix.y_labels).toEqual(['b', 'a'])
  })

  it('with no sample sizes at all, the plain mean ranks', () => {
    const chart = column([
      { key: 'a', value: 90, n: 0 },
      { key: 'b', value: 40, n: 0 },
    ])
    expect(heatmapFromMatrix(chart, null).matrix.y_keys).toEqual(['b', 'a'])
  })

  it('a row with nothing measured sorts after every measured row (unknown, not worst)', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameEdges.series, heatmapFrameEdges.meta)
    expect(matrix.y_labels[matrix.y_labels.length - 1]).toBe('dormant')
    const chart = column([
      { key: 'z', value: null, n: 0 },
      { key: 'y', value: null, n: 0 },
      { key: 'x', value: 10, n: 4 },
    ])
    expect(heatmapFromMatrix(chart, null).matrix.y_keys).toEqual(['x', 'y', 'z'])
  })

  it('ties are broken by label, then key, by code unit (never the reader’s locale)', () => {
    const chart = column([
      { key: 'k2', label: 'b', value: 50, n: 2 },
      { key: 'k1', label: 'b', value: 50, n: 2 },
      { key: 'k3', label: 'B', value: 50, n: 2 },
    ])
    expect(heatmapFromMatrix(chart, null).matrix.y_keys).toEqual(['k3', 'k1', 'k2'])
  })

  it('by name and by volume', () => {
    const chart = column([
      { key: 'b', value: 10, n: 5 },
      { key: 'a', value: 90, n: 1 },
      { key: 'c', value: 50, n: 9 },
    ])
    expect(heatmapFromMatrix(chart, null, 'name').matrix.y_keys).toEqual(['a', 'b', 'c'])
    expect(heatmapFromMatrix(chart, null, 'volume').matrix.y_keys).toEqual(['c', 'b', 'a'])
  })

  it('volume ties fall back to the name', () => {
    const chart = column([
      { key: 'b', value: 10, n: 5 },
      { key: 'a', value: 90, n: 5 },
    ])
    expect(heatmapFromMatrix(chart, null, 'volume').matrix.y_keys).toEqual(['a', 'b'])
  })

  it('a status matrix keeps the server’s ranking worst first (most failures over the window)', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameStatus.series, heatmapFrameStatus.meta)
    expect(matrix.y_keys).toEqual(heatmapFrameStatus.series.y_keys)
    expect(heatmapFromMatrix(heatmapFrameStatus.series, null, 'name').matrix.y_labels[0]).toBe(HEATMAP_FRAME_HOSTILE_NAME)
  })

  it('a count matrix is worst-first by its HIGHEST total', () => {
    const chart: MatrixChart = {
      ...column([
        { key: 'low', value: 1, n: 1 },
        { key: 'high', value: 9, n: 9 },
        { key: 'none', value: null, n: 0 },
      ]),
      value_type: 'count',
    }
    expect(heatmapFromMatrix(chart, null).matrix.y_keys).toEqual(['high', 'low', 'none'])
  })

  it('heatmapRowOrder is a permutation of the source rows', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameDense.series, null, 'name')
    const order = heatmapRowOrder(matrix, 'worst')
    expect([...order].sort((a, b) => a - b)).toEqual(matrix.y_labels.map((_, i) => i))
  })

  it('a matrix without keys orders by its labels', () => {
    const chart = column([
      { key: 'b', value: 10, n: 5 },
      { key: 'a', value: 90, n: 1 },
    ])
    delete chart.y_keys
    const { matrix } = heatmapFromMatrix(chart, null, 'name')
    expect(matrix.y_labels).toEqual(['a', 'b'])
    expect('y_keys' in matrix).toBe(false)
  })
})

describe('heatmapFromMatrix: re-sorting moves keys WITH the cells (M-501c)', () => {
  it.each(['worst', 'name', 'volume'] as const)('%s: every drawn row keeps its own key, label and cells', (sort) => {
    const source = heatmapFrameWorstFirst.series
    const { matrix } = heatmapFromMatrix(source, heatmapFrameMeta, sort)
    const sourceRow = (key: string) => source.y_keys?.indexOf(key) ?? -1
    expect(matrix.y_keys).toHaveLength(source.y_labels.length)
    matrix.y_keys?.forEach((key, y) => {
      const from = sourceRow(key)
      expect(matrix.y_labels[y]).toBe(source.y_labels[from])
      const drawn = matrix.cells.filter((cell) => cell.y === y).map((cell) => [cell.x, cell.n])
      const original = source.cells.filter((cell) => cell.y === from).map((cell) => [cell.x, cell.n])
      expect(drawn).toEqual(original)
    })
  })

  it('cells come out row-major in drawn order (the keyboard walks them so)', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameHostile.series, null)
    matrix.cells.forEach((cell, index) => {
      expect(cell.y).toBe(Math.floor(index / 7))
      expect(cell.x).toBe(index % 7)
    })
  })

  it('the columns and their keys never move', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameWorstFirst.series, heatmapFrameMeta, 'name')
    expect(matrix.x_keys).toEqual(heatmapFrameWorstFirst.series.x_keys)
  })
})

describe('heatmapFromMatrix: envelope', () => {
  it('rows: the server’s cut from meta.truncated_axes.series', () => {
    const { rows } = heatmapFromMatrix(heatmapFrameWorstFirst.series, heatmapFrameMeta)
    expect(rows).toEqual({ shown: 7, total: 12, dimension: 'suite' })
  })

  it('without truncation info the total is what is shown', () => {
    expect(heatmapFromMatrix(heatmapFrameHostile.series, null).rows).toEqual({ shown: 3, total: 3, dimension: null })
  })

  it('columns: the server’s cut from meta.truncated_axes.x', () => {
    const meta: EnvelopeMeta = {
      ...heatmapFrameMeta,
      truncated_axes: { x: { dimension: 'environment', kept: 1, total: 34 } },
    }
    expect(heatmapFromMatrix(column([{ key: 'a', value: 1, n: 1 }]), meta).columns).toEqual({
      shown: 1,
      total: 34,
      dimension: 'environment',
    })
  })

  it('labels today’s partial column for every text reader; its key stays a day', () => {
    const { matrix, partialColumn } = heatmapFromMatrix(heatmapFrameEdges.series, heatmapFrameEdges.meta)
    expect(partialColumn).toBe(6)
    expect(matrix.x_labels[6]).toBe(`2026-09-14${PARTIAL_DAY_SUFFIX}`)
    expect(matrix.x_labels[5]).toBe('2026-09-13')
    expect(matrix.x_keys?.[6]).toBe('2026-09-14')
  })

  it('a partial day outside the columns labels nothing', () => {
    const meta = { ...heatmapFrameMeta, partial_day: '2030-01-01' }
    const { matrix, partialColumn } = heatmapFromMatrix(heatmapFrameWorstFirst.series, meta)
    expect(partialColumn).toBeNull()
    expect(matrix.x_labels).toEqual(heatmapFrameWorstFirst.series.x_labels)
  })

  it('the drawable matrix is still a valid C3 matrix (counts add up, 0..1 inside its unit)', () => {
    for (const fixture of [heatmapFrameWorstFirst, heatmapFrameDense, heatmapFrameHostile, heatmapFrameEdges, heatmapFrameStatus]) {
      for (const sort of ['worst', 'name', 'volume'] as const) {
        const result = validateChartSeries(heatmapFromMatrix(fixture.series, fixture.meta, sort).matrix)
        expect(result.ok ? [] : result.errors).toEqual([])
      }
    }
  })

  it('every fixture is a valid C3 matrix as the endpoint sends it', () => {
    for (const fixture of [heatmapFrameWorstFirst, heatmapFrameDense, heatmapFrameHostile, heatmapFrameEdges, heatmapFrameStatus]) {
      const result = validateChartSeries(fixture.series)
      expect(result.ok ? [] : result.errors).toEqual([])
    }
  })

  it('a hostile name is carried as data, unchanged', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameHostile.series, null)
    expect(matrix.y_labels[0]).toBe(HEATMAP_FRAME_HOSTILE_NAME)
    expect(matrix.y_keys?.[0]).toBe(HEATMAP_FRAME_HOSTILE_NAME)
  })
})

describe('fittedDomain', () => {
  const numeric = (cells: (number | null)[]): NumericMatrix => ({
    kind: 'matrix',
    value_type: 'rate',
    x_labels: cells.map((_, i) => `d${i}`),
    y_labels: ['r'],
    cells: cells.map((value, x) => ({ x, y: 0, value, n: 1 })),
  })

  it('is the lowest and highest MEASURED value (a null is not 0)', () => {
    expect(fittedDomain(numeric([0.6, null, 0.9, 0.75]))).toEqual([0.6, 0.9])
  })

  it('nothing measured, or a status matrix: no range to fit', () => {
    expect(fittedDomain(numeric([null, null]))).toBeNull()
    expect(fittedDomain(heatmapFromMatrix(heatmapFrameStatus.series, null).matrix)).toBeNull()
  })

  it('a flat rate is widened one point, inside 0..1', () => {
    expect(fittedDomain(numeric([1, 1]))).toEqual([0.99, 1])
    expect(fittedDomain(numeric([0, 0]))).toEqual([0, 0.01])
    expect(fittedDomain(numeric([0.5, 0.5]))).toEqual([0.49, 0.51])
  })

  it('a flat count is widened one unit, never below 0', () => {
    expect(fittedDomain({ ...numeric([3, 3]), value_type: 'count' })).toEqual([2, 4])
    expect(fittedDomain({ ...numeric([0, 0]), value_type: 'count' })).toEqual([0, 1])
  })

  it('says the range in words', () => {
    expect(fittedDomainNote(numeric([0.6]), [0.6, 0.9])).toBe('Colour scale fitted to the data: 60.0% to 90.0%.')
    expect(fittedDomainNote({ ...numeric([1]), value_type: 'count' }, [2, 40])).toBe('Colour scale fitted to the data: 2 to 40.')
  })
})

describe('the words', () => {
  it('row labels are cut in the MIDDLE, so two long names that share a prefix stay apart', () => {
    const [a, b, short] = printedRowLabels(['payments-integration-suite-eu-west', 'payments-integration-suite-us-east', 'auth'])
    expect(a).not.toBe(b)
    expect(a).toMatch(/^payments.*….*eu-west$/)
    expect([...a]).toHaveLength(HEATMAP_ROW_LABEL_CHARS)
    expect(short).toBe('auth')
    expect([...printedRowLabels([HEATMAP_FRAME_LONG_NAME])[0]]).toHaveLength(HEATMAP_ROW_LABEL_CHARS)
  })

  it('names the order rule per sort and kind', () => {
    expect(heatmapOrderNote('rate', 'worst')).toBe('Rows: lowest pass rate first.')
    expect(heatmapOrderNote('status', 'worst')).toBe('Rows: most failures first.')
    expect(heatmapOrderNote('count', 'worst')).toBe('Rows: highest first.')
    expect(heatmapOrderNote('rate', 'name')).toBe('Rows: by name.')
    expect(heatmapOrderNote('status', 'volume')).toBe('Rows: most executions first.')
  })

  it('states the row cut and what picked the rows', () => {
    expect(heatmapRowsNote({ shown: 40, total: 200, dimension: 'suite' }, SUITE_DAY_NOUNS)).toBe('Top 40 of 200 suites by failures.')
    expect(heatmapRowsNote({ shown: 3, total: 3, dimension: null }, SUITE_DAY_NOUNS)).toBe('')
  })

  it('states the column cut by what the columns are', () => {
    expect(heatmapColumnsNote({ shown: 20, total: 34, dimension: 'environment' })).toBe('The 20 busiest of 34 environments.')
    expect(heatmapColumnsNote({ shown: 20, total: 25, dimension: 'release' })).toBe('The 20 most recent of 25 releases.')
    expect(heatmapColumnsNote({ shown: 30, total: 120, dimension: 'run' })).toBe('The last 30 of 120 runs.')
    expect(heatmapColumnsNote({ shown: 5, total: 9, dimension: null })).toBe('5 of 9 columns shown.')
    expect(heatmapColumnsNote({ shown: 5, total: 5, dimension: 'run' })).toBe('')
  })

  it('describes the chart in one sentence, singular where it is one', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameWorstFirst.series, heatmapFrameMeta)
    expect(heatmapDescription('Suites', matrix, SUITE_DAY_NOUNS, 'worst')).toBe(
      'Suites: pass rate for 7 suites over 14 days, lowest pass rate first.',
    )
    const one = heatmapFromMatrix(column([{ key: 'a', value: 1, n: 1 }]), null).matrix
    expect(heatmapDescription('S', one, SUITE_DAY_NOUNS, 'name')).toBe('S: pass rate for 1 suite over 1 day, by name.')
    const status = heatmapFromMatrix(heatmapFrameStatus.series, null).matrix
    expect(heatmapDescription('T', status, { rows: ['test', 'tests'], columns: ['run', 'runs'] }, 'worst')).toBe(
      'T: results for 4 tests over 8 runs, most failures first.',
    )
    const count: HeatmapMatrix = { ...(one as NumericMatrix), value_type: 'count' }
    expect(heatmapDescription('C', count, SUITE_DAY_NOUNS, 'volume')).toBe('C: counts for 1 suite over 1 day, most executions first.')
  })

  it('knows a day axis by its keys', () => {
    expect(columnsAreDays(['2026-09-01', '2026-09-02'])).toBe(true)
    expect(columnsAreDays(['run-001'])).toBe(false)
    expect(columnsAreDays(['2026-09-01', 'staging'])).toBe(false)
    expect(columnsAreDays([])).toBe(false)
  })
})

describe('heatmapCellMark (the VIZ-602 seam)', () => {
  const SUITE_DAY = { row: 'suite', column: 'day' } as const

  it('a cell is its row KEY under the row dimension, with its column KEY as context', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameWorstFirst.series, heatmapFrameMeta)
    const index = matrix.cells.findIndex((cell) => cell.y === 0 && cell.x === 3)
    const mark = heatmapCellMark(matrix, index, SUITE_DAY)
    expect(mark).toMatchObject({
      dimension: 'suite',
      value: 'legacy-import',
      label: 'legacy-import, 2026-09-04',
      n: matrix.cells[index].n,
      context: [{ dimension: 'day', value: '2026-09-04' }],
    })
    // The rate as the server sent it (percentage points), not the canvas ratio.
    expect(mark?.y).toBeCloseTo((matrix.cells[index].value as number) * 100, 6)
  })

  it('re-sorted, a cell still names ITS row (keys move with the rows)', () => {
    for (const sort of ['worst', 'name', 'volume'] as const) {
      const { matrix } = heatmapFromMatrix(heatmapFrameHostile.series, null, sort)
      matrix.cells.forEach((cell, index) => {
        const mark = heatmapCellMark(matrix, index, SUITE_DAY)
        expect(mark?.value).toBe(matrix.y_keys?.[cell.y])
        expect(mark?.label.startsWith(matrix.y_labels[cell.y])).toBe(true)
      })
    }
  })

  it('a key, never a label: a matrix whose labels repeat still names each row apart', () => {
    const chart = column([
      { key: 'k1', label: 'same', value: 50, n: 2 },
      { key: 'k2', label: 'same', value: 60, n: 2 },
    ])
    const { matrix } = heatmapFromMatrix(chart, null, 'name')
    expect([0, 1].map((index) => heatmapCellMark(matrix, index, SUITE_DAY)?.value)).toEqual(['k1', 'k2'])
  })

  it('a cell nobody ran is not a mark; a skipped-only one is (its rows exist), with no value', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameEdges.series, heatmapFrameEdges.meta)
    const dormant = matrix.y_labels.indexOf('dormant')
    expect(heatmapCellMark(matrix, matrix.cells.findIndex((cell) => cell.y === dormant), SUITE_DAY)).toBeNull()
    const proto = matrix.y_labels.indexOf('__proto__')
    const skipped = heatmapCellMark(matrix, matrix.cells.findIndex((cell) => cell.y === proto && cell.x === 2), SUITE_DAY)
    expect(skipped).toMatchObject({ value: '__proto__', y: null, n: 4 })
  })

  it('a status cell has no numeric value; a column that is not a dimension gives no context', () => {
    const { matrix } = heatmapFromMatrix(heatmapFrameStatus.series, null)
    const mark = heatmapCellMark(matrix, 0, { row: 'test', column: null })
    expect(mark).toMatchObject({ dimension: 'test', value: 'fp-0001', y: null, n: 1 })
    expect(mark && 'context' in mark).toBe(false)
  })

  it('a count cell keeps its count; an index past the cells is nothing', () => {
    const chart: MatrixChart = { ...column([{ key: 'a', value: 7, n: 7 }]), value_type: 'count' }
    const { matrix } = heatmapFromMatrix(chart, null)
    expect(heatmapCellMark(matrix, 0, SUITE_DAY)?.y).toBe(7)
    expect(heatmapCellMark(matrix, 5, SUITE_DAY)).toBeNull()
  })

  it('a cell pointing past the labels is nothing (never a mark with an undefined key)', () => {
    const matrix: NumericMatrix = {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['d'],
      y_labels: ['r'],
      cells: [{ x: 3, y: 0, value: 0.5, n: 1 }],
    }
    expect(heatmapCellMark(matrix, 0, SUITE_DAY)).toBeNull()
  })
})

describe('run columns and repeated labels (F-04, F-19), the smallest cut (F-12)', () => {
  it('a repeated label is told apart by its place among the equals; a unique one is unchanged', () => {
    expect(disambiguatedLabels(['218', '228', '230', '228', '__proto__', '__proto__'])).toEqual([
      '218',
      '228 (1)',
      '230',
      '228 (2)',
      '__proto__ (1)',
      '__proto__ (2)',
    ])
    expect(disambiguatedLabels(['a', 'b'])).toEqual(['a', 'b'])
  })

  it('a run is read as a build: "218" -> "Build 218", a label that already says it is kept; the axis prints the number', () => {
    expect(runColumnLabel('218')).toBe('Build 218')
    expect(runColumnLabel('Build 1200')).toBe('Build 1200')
    expect(printedRunLabels(['Build 218', 'Build 228 (2)', 'Builder', 'Build '])).toEqual(['218', '228 (2)', 'Builder', 'Build '])
  })

  it('heatmapFromMatrix with run columns: every reader (tooltip, announcement, table) says "Build 228 (2)"', () => {
    const chart: MatrixChart = {
      kind: 'matrix',
      value_type: 'status',
      x_labels: ['218', '228', '228'],
      x_keys: ['r1', 'r2', 'r3'],
      y_labels: ['logout clears session'],
      y_keys: ['fp-1'],
      cells: [
        { x: 0, y: 0, value: 'passed', n: 1 },
        { x: 1, y: 0, value: 'failed', n: 1 },
        { x: 2, y: 0, value: 'passed', n: 1 },
      ],
    }
    const { matrix } = heatmapFromMatrix(chart, null, 'worst', { runColumns: true })
    expect(matrix.x_labels).toEqual(['Build 218', 'Build 228 (1)', 'Build 228 (2)'])
    expect(matrix.x_keys).toEqual(['r1', 'r2', 'r3'])
    expect(tooltipText(heatmapTooltipContent(matrix, matrix.cells[0]))).toMatch(/Build 218: Passed/)
    // Without the option (every other kind): the labels as sent, repeats told apart all the same.
    expect(heatmapFromMatrix(chart, null).matrix.x_labels).toEqual(['218', '228 (1)', '228 (2)'])
    const unique = { ...chart, x_labels: ['218', '228', '230'] }
    expect(heatmapFromMatrix(unique, null).matrix.x_labels).toEqual(['218', '228', '230'])
  })

  it('a middle cut that would save fewer than 3 characters is not made', () => {
    const max = HEATMAP_ROW_LABEL_CHARS
    const near = 'x'.repeat(max + HEATMAP_MIN_CUT - 1)
    expect(printedRowLabels([near])).toEqual([near])
    const far = 'y'.repeat(max + HEATMAP_MIN_CUT)
    expect([...printedRowLabels([far])[0]]).toHaveLength(max)
  })
})
