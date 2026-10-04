/**
 * The 3D scatter's model (VIZ-508): every test's place in the unit cube, the
 * floors, the ticks, the quadrant groups and the degenerate extents — pure,
 * so all of it is checked here without WebGL.
 */
import { describe, expect, it } from 'vitest'
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import { axisValueFormatter } from './chartText'
import { SCATTER_DENSE_POINTS, quadrantColor } from './engines/echarts/scatterOption'
import { QUADRANTS } from './testScatter.model'
import type { ChartTokens } from './tokens'
import {
  CONTEXT_LOST_NOTICE,
  NO_WEBGL_NOTICE,
  SCATTER_3D_POINT,
  VIEW_2D_LABEL,
  VIEW_3D_LABEL,
  linearScale,
  logScale,
  scatter3DColors,
  scatter3DDescription,
  scatter3DLayout,
  unavailableNotice,
} from './scatter3d.model'

const point = (id: string, x: number, y: number, size: number): PointsChartPoint => ({ id, label: id, x, y, size, n: size })

const chart = (points: PointsChartPoint[], medians: PointsChart['medians'] | null = { x: 100, y: 10 }): PointsChart => ({
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points,
  ...(medians ? { medians } : {}),
})

/** Point `i`'s x, y, z. */
const at = (positions: number[], i: number) => positions.slice(i * 3, i * 3 + 3)

describe('scatter3DLayout: where each test sits', () => {
  it('maps p95 on whole decades (log), the failure rate on 0..100, executions on decades (log)', () => {
    const layout = scatter3DLayout(chart([point('a', 1, 0, 1), point('b', 10, 50, 10), point('c', 100, 100, 100)]))
    expect(at(layout.positions, 0)).toEqual([0, 0, 0])
    expect(at(layout.positions, 1)).toEqual([0.5, 0.5, 0.5])
    expect(at(layout.positions, 2)).toEqual([1, 1, 1])
    expect(layout.positions).toHaveLength(9)
  })

  it('a p95 under 1 ms sits ON the 1 ms floor, as the 2D chart draws it', () => {
    const layout = scatter3DLayout(chart([point('a', 0, 0, 5), point('b', 0.4, 0, 5), point('c', 1000, 0, 50)]))
    expect(at(layout.positions, 0)[0]).toBe(0)
    expect(at(layout.positions, 1)[0]).toBe(0)
    expect(at(layout.positions, 2)[0]).toBe(1)
    expect(layout.x.ticks[0].text).toBe(axisValueFormatter('ms')(1))
  })

  it('every value stays in 0..1, whatever the data', () => {
    const layout = scatter3DLayout(
      chart([point('a', 3, -5, 0), point('b', 4e6, 140, 1e7), point('c', Number.NaN, 20, 7), point('d', 50, 2, -3)]),
    )
    for (const v of layout.positions) {
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
    }
  })

  it('one test, or every value equal: still a decade of room, no division by zero', () => {
    const one = scatter3DLayout(chart([point('a', 100, 0, 10)]))
    expect(one.positions.every(Number.isFinite)).toBe(true)
    expect(at(one.positions, 0)).toEqual([0, 0, 0])
    expect(one.x.ticks.map((t) => t.at)).toEqual([0, 1])
    const same = scatter3DLayout(chart([point('a', 30, 5, 8), point('b', 30, 5, 8)]))
    expect(same.positions.every(Number.isFinite)).toBe(true)
    expect(at(same.positions, 0)).toEqual(at(same.positions, 1))
  })

  it('no test at all: empty positions and groups, ticks still drawn', () => {
    const layout = scatter3DLayout(chart([]))
    expect(layout.positions).toEqual([])
    expect(QUADRANTS.every((q) => layout.groups[q].length === 0)).toBe(true)
    expect(layout.y.ticks).toHaveLength(5)
  })
})

describe('scatter3DLayout: quadrants, ticks and titles', () => {
  it('groups by the 2D rule: strictly above a median is above it', () => {
    const layout = scatter3DLayout(
      chart([point('sf', 500, 20, 5), point('ff', 50, 20, 5), point('ss', 500, 0, 5), point('fs', 50, 0, 5), point('on', 100, 10, 5)]),
    )
    expect(layout.groups).toEqual({ 'slow-flaky': [0], 'fast-flaky': [1], 'slow-stable': [2], 'fast-stable': [3, 4] })
  })

  it('with no medians every test is fast-stable, as the 2D chart colours it', () => {
    const layout = scatter3DLayout(chart([point('a', 500, 20, 5), point('b', 5, 0, 5)], null))
    expect(layout.groups['fast-stable']).toEqual([0, 1])
    expect(layout.groups['slow-flaky']).toEqual([])
  })

  it('ticks: decades on x and z, 0/25/50/75/100 % on y, each in the axis unit\'s words', () => {
    const layout = scatter3DLayout(chart([point('a', 2, 0, 3), point('b', 900, 40, 700)]))
    const ms = axisValueFormatter('ms')
    expect(layout.x.ticks).toEqual([
      { at: 0, text: ms(1) },
      { at: 1 / 3, text: ms(10) },
      { at: 2 / 3, text: ms(100) },
      { at: 1, text: ms(1000) },
    ])
    const pct = axisValueFormatter('percent')
    expect(layout.y.ticks.map((t) => t.text)).toEqual([0, 25, 50, 75, 100].map(pct))
    expect(layout.y.ticks.map((t) => t.at)).toEqual([0, 0.25, 0.5, 0.75, 1])
    expect(layout.z.ticks.map((t) => t.text)).toEqual(['1', '10', '100', '1,000'])
  })

  it('a span past six decades labels every other one', () => {
    const scale = logScale([1, 1e9], 1, 'ms')
    expect(scale.ticks).toHaveLength(5)
    expect(scale.ticks[1].at).toBeCloseTo(2 / 9)
  })

  it('a linear axis with no fixed range runs from 0 to the largest value; an empty or zero one from 0 to 1', () => {
    expect(linearScale({ unit: 'count' }, [10, 40]).at(20)).toBe(0.5)
    expect(linearScale({ unit: 'ratio' }, [0.2]).at(0.5)).toBe(0.5)
    expect(linearScale({ unit: 'count' }, []).at(0.5)).toBe(0.5)
    expect(linearScale({ unit: 'count' }, [0, 0]).at(2)).toBe(1)
  })

  it('the axis titles are the chart\'s own labels', () => {
    const layout = scatter3DLayout(chart([point('a', 5, 1, 5)]))
    expect([layout.x.title, layout.y.title, layout.z.title]).toEqual(['p95 duration (ms)', 'Failure rate (%)', 'Executions'])
  })

  it('points shrink past the 2D chart\'s dense threshold', () => {
    expect(scatter3DLayout(chart([point('a', 5, 1, 5)])).pointSize).toBe(SCATTER_3D_POINT.normal)
    const many = Array.from({ length: SCATTER_DENSE_POINTS + 1 }, (_, i) => point(`t${i}`, i + 1, i % 100, 5))
    expect(scatter3DLayout(chart(many)).pointSize).toBe(SCATTER_3D_POINT.dense)
  })
})

describe('scatter3D words and colours', () => {
  it('the toggle and the notices', () => {
    expect([VIEW_3D_LABEL, VIEW_2D_LABEL]).toEqual(['View in 3D', 'Back to 2D'])
    expect(unavailableNotice('no-webgl')).toBe(NO_WEBGL_NOTICE)
    expect(unavailableNotice('context-lost')).toBe(CONTEXT_LOST_NOTICE)
    expect(scatter3DDescription(chart([point('a', 5, 1, 5)]))).toBe(
      '3D scatter of 1 test: p95 duration (ms) across, Failure rate (%) up, Executions in depth.',
    )
  })

  it('colours are the 2D chart\'s quadrant tokens, the axis and the grid, nothing else', () => {
    const tokens = {
      status: { failed: 'red' },
      flaky: 'purple',
      series: ['teal'],
      textMuted: 'gray',
      axis: 'black',
      grid: 'silver',
    } as unknown as ChartTokens
    const colors = scatter3DColors(tokens)
    expect(colors.frame).toBe('black')
    expect(colors.grid).toBe('silver')
    for (const q of QUADRANTS) expect(colors.quadrants[q]).toBe(quadrantColor(q, tokens))
    expect(colors.quadrants).toEqual({ 'slow-flaky': 'red', 'fast-flaky': 'purple', 'slow-stable': 'teal', 'fast-stable': 'gray' })
  })
})
