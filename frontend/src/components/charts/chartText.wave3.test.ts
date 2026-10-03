/**
 * Wave 3 (FK0): the text alternatives read every C3 kind and the Wave-3
 * additions, from the SHARED contract fixtures (the ones both validators run),
 * so the table and the summary describe exactly what the server may send.
 */
import { describe, expect, it } from 'vitest'
import type { MatrixChart, PointsChart, TreeChart } from '@/lib/viz/contracts'
import matrixKeysCounts from '../../../../contracts/viz/fixtures/chart_series/valid/matrix_rate_keys_counts.json'
import matrixRatio from '../../../../contracts/viz/fixtures/chart_series/valid/matrix_ratio_unit.json'
import pointsFixture from '../../../../contracts/viz/fixtures/chart_series/valid/points.json'
import pointsHostile from '../../../../contracts/viz/fixtures/chart_series/valid/points_hostile.json'
import pointsAllExcluded from '../../../../contracts/viz/fixtures/chart_series/valid/points_all_excluded.json'
import treeStats from '../../../../contracts/viz/fixtures/chart_series/valid/tree_stats.json'
import {
  axisValueFormatter,
  chartTableModel,
  defaultFormatter,
  matrixAxisKeys,
  NO_DATA,
  NOTHING_EVALUATED,
  statusCountsText,
  summarizeChart,
} from './chartText'
import { NO_VALUE } from '@/utils/formatters'

const percentMatrix = matrixKeysCounts.payload as MatrixChart
const ratioMatrix = matrixRatio.payload as MatrixChart
const points = pointsFixture.payload as PointsChart
const tree = treeStats.payload as TreeChart

describe('matrix unit', () => {
  it('a matrix that says percent is read in percentage points: 96.36 is 96.4%, not 9,636%', () => {
    expect(defaultFormatter(percentMatrix)(96.36)).toBe('96.4%')
  })

  it('a ratio matrix, and a matrix with no unit (every pre-Wave-3 producer), are read as 0..1', () => {
    expect(defaultFormatter(ratioMatrix)(0.964)).toBe('96.4%')
    const { unit: _unit, ...legacy } = percentMatrix
    expect(defaultFormatter(legacy as MatrixChart)(0.964)).toBe('96.4%')
  })

  it('a count matrix ignores the unit', () => {
    expect(defaultFormatter({ ...percentMatrix, value_type: 'count' })(12)).toBe('12')
  })
})

describe('matrix keys and counts', () => {
  it('keys are the stable ids; labels stand in only when the server sent none', () => {
    expect(matrixAxisKeys(percentMatrix)).toEqual({ x: ['2026-09-17', '2026-09-18'], y: ['payments', 'cart'] })
    const { x_keys: _x, y_keys: _y, ...legacy } = percentMatrix
    expect(matrixAxisKeys(legacy as MatrixChart)).toEqual({ x: ['Sep 17', 'Sep 18'], y: ['Payments', 'cart'] })
  })

  it('status counts read non-zero statuses only, in vocabulary order', () => {
    expect(statusCountsText({ passed: 212, failed: 3, broken: 5, skipped: 0, unknown: 0 })).toBe('212 passed, 3 failed, 5 broken')
    expect(statusCountsText({ passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 1200 })).toBe('1,200 unknown')
    expect(statusCountsText({ passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 0 })).toBe('no executions')
  })

  it('the table states the counts behind a value, "No data" for an empty cell and "Nothing evaluated" for a skipped-only one', () => {
    const table = chartTableModel(percentMatrix, { y: 'Suite' })
    expect(table.columns).toEqual(['Suite', 'Sep 17', 'Sep 18'])
    expect(table.rows).toEqual([
      { header: 'Payments', cells: ['96.4% (212 passed, 3 failed, 5 broken)', NO_DATA] },
      // Skipped-only is null like an empty cell, but it is NOT "no runs".
      { header: 'cart', cells: [`${NOTHING_EVALUATED} (4 skipped)`, '0.0% (12 failed)'] },
    ])
  })

  it('a matrix without counts reads exactly as before Wave 3', () => {
    const plain: MatrixChart = {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['a', 'b'],
      y_labels: ['r'],
      cells: [
        { x: 0, y: 0, value: 0.5, n: 2 },
        { x: 1, y: 0, value: null, n: 3 },
      ],
    }
    expect(chartTableModel(plain).rows).toEqual([{ header: 'r', cells: ['50.0%', NO_DATA] }])
  })

  it('the summary reads a percent matrix in percentage points', () => {
    const text = summarizeChart({ chartType: 'Heatmap', series: percentMatrix })
    expect(text).toContain('Min 0.0% (cart, Sep 18), max 96.4% (Payments, Sep 17).')
    expect(text).toContain('2 cells with no data.')
  })
})

describe('tree stats', () => {
  it('the table grows the stats columns, and never prints an unknown last run as "never run"', () => {
    const table = chartTableModel(tree)
    expect(table.columns).toEqual([
      'Node', 'Parent', 'Tests', 'Pass rate', 'Executions', 'Flaky tests', 'Flaky share', 'Last executed', 'Days since last run',
    ])
    expect(table.rows).toEqual([
      { header: 'All suites', cells: [NO_VALUE, '20', '96.4%', '220', '1', '5.0%', '2026-09-30', '1'] },
      { header: 'payments', cells: ['All suites', '12', '96.4%', '220', '1', '8.3%', '2026-09-30', '1'] },
      { header: 'legacy', cells: ['All suites', '6', NO_VALUE, '0', '0', '0.0%', 'Unknown', NO_VALUE] },
      { header: 'new', cells: ['All suites', '2', NO_VALUE, '0', '0', '0.0%', 'Never run', NO_VALUE] },
    ])
  })

  it('a node without stats in a stats tree shows its size and dashes', () => {
    const other: TreeChart = {
      kind: 'tree',
      nodes: [...tree.nodes, { id: 'other:all', parent_id: 'all', label: 'Other (3)', value: 3, measure: null }],
    }
    const rows = chartTableModel(other).rows
    expect(rows[rows.length - 1]).toEqual({
      header: 'Other (3)',
      cells: ['All suites', '3', NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE],
    })
  })

  it('a tree with no stats keeps its pre-Wave-3 columns', () => {
    const plain: TreeChart = { kind: 'tree', nodes: [{ id: 'r', parent_id: null, label: 'root', value: 4, measure: 1 }] }
    expect(chartTableModel(plain).columns).toEqual(['Node', 'Parent', 'Value', 'Measure'])
  })

  it('the summary counts never-run and unknown nodes apart', () => {
    const text = summarizeChart({ chartType: 'Treemap', series: tree })
    expect(text).toContain('1 node never run.')
    expect(text).toContain('1 node with an unknown last run.')
    expect(summarizeChart({ chartType: 'Treemap', series: { kind: 'tree', nodes: tree.nodes.slice(0, 2) } })).not.toMatch(/never run|unknown/)
  })
})

describe('points', () => {
  it('formats each axis by its declared unit', () => {
    // F-08: one rule for milliseconds: whole from 10 ms up, a tenth below (3.4 ms is not 3 ms).
    expect(axisValueFormatter('ms')(1840.5)).toBe('1,841 ms')
    expect(axisValueFormatter('ms')(1778.3)).toBe('1,778 ms')
    expect(axisValueFormatter('ms')(10)).toBe('10 ms')
    expect(axisValueFormatter('ms')(9.94)).toBe('9.9 ms')
    expect(axisValueFormatter('ms')(3.45)).toBe('3.5 ms')
    expect(axisValueFormatter('percent')(12.5)).toBe('12.5%')
    expect(axisValueFormatter('ratio')(0.125)).toBe('12.5%')
    expect(axisValueFormatter('count')(1200)).toBe('1,200')
  })

  it('the table is one row per point, in the server’s order, every column by its unit', () => {
    const table = chartTableModel(points)
    expect(table.columns).toEqual(['Name', 'p95 duration (ms)', 'Failure rate (%)', 'Executions', 'Evaluated'])
    expect(table.rows[0]).toEqual({ header: 'test_checkout', cells: ['1,841 ms', '12.5%', '40', '40'] })
    expect(table.rows.map((r) => r.header)).toEqual(['test_checkout', 'test_login', 'test_refund', 'test_cart'])
  })

  it('the summary names the axes, their units and scales, the medians and what was left out', () => {
    const text = summarizeChart({ chartType: 'Scatter chart', series: points, scope: 'Project payments, last 30 days' })
    expect(text).toBe(
      'Scatter chart of 4 points. X axis: p95 duration (ms) (milliseconds, log scale). Y axis: Failure rate (%) (percent). ' +
        'Size: Executions. Scope: Project payments, last 30 days. Medians: p95 duration (ms) 203 ms, Failure rate (%) 7.5%. ' +
        'Not shown: 14 below the minimum executions, 2 with no duration, 1 with nothing evaluated.',
    )
  })

  it('a chart with every test excluded says why, and claims no medians', () => {
    const empty = pointsAllExcluded.payload as PointsChart
    const text = summarizeChart({ chartType: 'Scatter chart', series: empty })
    expect(text).toContain('of 0 points.')
    expect(text).not.toContain('Medians')
    expect(text).toMatch(/Not shown: .+\./)
    expect(chartTableModel(empty).rows).toEqual([])
  })

  it('omits the exclusion sentence when nothing was left out, and the axis names when the frame gives its own', () => {
    const text = summarizeChart({
      chartType: 'Scatter chart',
      series: { ...points, excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 } },
      axes: { x: 'Duration', y: 'Failures' },
    })
    expect(text).toContain('X axis: Duration. Y axis: Failures.')
    expect(text).not.toContain('Not shown')
  })

  it('hostile labels reach the table and summary as plain text', () => {
    const hostile = pointsHostile.payload as PointsChart
    const table = chartTableModel(hostile)
    for (const row of table.rows) expect(typeof row.header).toBe('string')
    expect(table.rows.map((r) => r.header)).toEqual(hostile.points.map((p) => p.label))
  })
})
