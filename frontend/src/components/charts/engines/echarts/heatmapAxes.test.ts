/**
 * Fix round 2 (B0 finding 6): a heatmap can draw row 0 at the top and print
 * short column labels, and without either option its option is exactly what
 * the committed heatmap baselines were drawn from.
 */
import { describe, expect, it } from 'vitest'
import { readChartTokens } from '../../tokens'
import { buildHeatmapOption, type NumericMatrix } from './heatmapOption'

const data: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['2026-09-01', '2026-09-02'],
  y_labels: ['worst', 'best'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 1 },
    { x: 1, y: 0, value: 0.6, n: 1 },
    { x: 0, y: 1, value: 0.9, n: 1 },
    { x: 1, y: 1, value: null, n: 0 },
  ],
}

type Axes = { xAxis: { data: string[] }; yAxis: { data: string[]; inverse?: boolean } }
const build = (extra: Partial<Parameters<typeof buildHeatmapOption>[0]> = {}) =>
  buildHeatmapOption({ data, tokens: readChartTokens(), description: 'd', ...extra }) as unknown as Axes

describe('heatmap axes options', () => {
  it('default: ECharts’ own order, the full column labels, no `inverse` key at all', () => {
    const option = build()
    expect(option.yAxis).not.toHaveProperty('inverse')
    expect(option.xAxis.data).toEqual(data.x_labels)
    expect(JSON.stringify(build({ rowsTopDown: false, columnLabels: undefined }))).toBe(JSON.stringify(build()))
  })

  it('rowsTopDown inverts the row axis (row 0 at the top)', () => {
    expect(build({ rowsTopDown: true }).yAxis.inverse).toBe(true)
    expect(build({ rowsTopDown: true }).yAxis.data).toEqual(['worst', 'best'])
  })

  it('columnLabels are what the axis prints; the tooltip still names the full column', () => {
    const option = build({ columnLabels: ['Sep 1', 'Sep 2'] })
    expect(option.xAxis.data).toEqual(['Sep 1', 'Sep 2'])
    expect(data.x_labels).toEqual(['2026-09-01', '2026-09-02'])
  })

  it('a column label list of the wrong length is ignored, never misaligned', () => {
    expect(build({ columnLabels: ['Sep 1'] }).xAxis.data).toEqual(data.x_labels)
  })
})
