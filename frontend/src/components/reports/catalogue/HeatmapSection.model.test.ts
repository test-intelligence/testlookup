/**
 * FK4-1 (integrator): which rows selections a heatmap kind can own, and what a
 * panel opened from a pasted link is called.
 */
import { describe, expect, it } from 'vitest'
import type { MatrixChart } from '@/lib/viz/contracts'
import { HEATMAP_KIND_SPECS, heatmapOpenedRows, heatmapOwnRows, heatmapRowsTitle } from './HeatmapSection.model'

const matrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['Sep 1', 'Sep 2'],
  x_keys: ['2026-09-01', '2026-09-02'],
  y_labels: ['Payments', 'constructor'],
  y_keys: ['payments', '__proto__'],
  cells: [],
} as unknown as MatrixChart

describe('heatmapOwnRows', () => {
  it('keeps a selection only in the kind’s own shape: the row, then the column', () => {
    const cell = [
      { dimension: 'suite' as const, value: 'payments' },
      { dimension: 'day' as const, value: '2026-09-01' },
    ]
    expect(heatmapOwnRows(HEATMAP_KIND_SPECS.suite_day, cell)).toBe(cell)
    expect(heatmapOwnRows(HEATMAP_KIND_SPECS.suite_environment, cell)).toEqual([])
    expect(heatmapOwnRows(HEATMAP_KIND_SPECS.suite_day, [...cell].reverse())).toEqual([])
    expect(heatmapOwnRows(HEATMAP_KIND_SPECS.suite_day, cell.slice(0, 1))).toEqual([])
    expect(heatmapOwnRows(HEATMAP_KIND_SPECS.test_run, [{ dimension: 'test', value: 'fp' }])).toHaveLength(1)
  })
})

describe('heatmapRowsTitle', () => {
  it('names the cell by the drawn labels, matched by KEY (never by label)', () => {
    expect(
      heatmapRowsTitle(matrix, [
        { dimension: 'suite', value: '__proto__' },
        { dimension: 'day', value: '2026-09-02' },
      ]),
    ).toBe('constructor, Sep 2')
    expect(heatmapRowsTitle(matrix, [{ dimension: 'test', value: 'payments' }])).toBe('Payments')
  })

  it('falls back to the keys when the matrix is not drawn or no longer has them', () => {
    const rows = [
      { dimension: 'suite' as const, value: 'gone' },
      { dimension: 'day' as const, value: '2026-08-01' },
    ]
    expect(heatmapRowsTitle(null, rows)).toBe('gone, 2026-08-01')
    expect(heatmapRowsTitle(matrix, rows)).toBe('gone, 2026-08-01')
    expect(heatmapRowsTitle(matrix, [])).toBe('')
  })
})

describe('heatmapOpenedRows (R1B-2)', () => {
  const statusMatrix = {
    ...matrix,
    value_type: 'status',
    x_labels: ['Build 228', 'Build 228'],
    x_keys: ['run-a', 'run-b'],
    y_labels: ['logout clears session'],
    y_keys: ['fp-1'],
  } as unknown as MatrixChart

  it('a test x run cell: titled by the test alone, no expected count (the panel lists every run of the test)', () => {
    const mark = { dimension: 'test' as const, value: 'fp-1', label: 'logout clears session, Build 228', y: null, n: 1 }
    const opened = heatmapOpenedRows(HEATMAP_KIND_SPECS.test_run, mark, [{ dimension: 'test', value: 'fp-1' }], statusMatrix, 'T')
    expect(opened).toEqual({ title: 'logout clears session', expected: null })
    // Not drawn any more: the key, never the cell's run.
    expect(heatmapOpenedRows(HEATMAP_KIND_SPECS.test_run, mark, [{ dimension: 'test', value: 'fp-1' }], null, null).title).toBe('fp-1')
  })

  it('a cell of a kind with a selectable column: the cell, and its n to reconcile with', () => {
    const mark = { dimension: 'suite' as const, value: 'payments', label: 'Payments, Sep 1', y: 50, n: 4 }
    const selectors = [
      { dimension: 'suite' as const, value: 'payments' },
      { dimension: 'day' as const, value: '2026-09-01' },
    ]
    expect(heatmapOpenedRows(HEATMAP_KIND_SPECS.suite_day, mark, selectors, matrix, 'T')).toEqual({
      title: 'Payments, Sep 1',
      expected: { y: 4, n: 4, asOf: 'T' },
    })
  })
})
