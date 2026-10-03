import { describe, expect, it } from 'vitest'
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import type { ChartTokens } from '../../tokens'
import { TOOLTIP_NODE_ATTRIBUTE, assertSafeChartOption } from '../../tooltip'
import {
  OUT_OF_BRUSH_ALPHA,
  QUADRANT_SYMBOLS,
  SCATTER_ALPHA,
  SCATTER_DENSE_POINTS,
  SCATTER_EDGE_PAD,
  SCATTER_SYMBOL,
  SCATTER_SYMBOL_DENSE,
  axisName,
  buildScatterOption,
  logExtent,
  padExtent,
  quadrantColor,
  scatterGrid,
  scatterPointMark,
  symbolDiameter,
  xNameGap,
} from './scatterOption'

const HOSTILE = '<img src=x onerror="window.__xss=1">'

// Token values are names, not colours (check:theme forbids colour literals here).
const tokens: ChartTokens = {
  theme: 'signal',
  series: Array.from({ length: 8 }, (_, i) => `series-${i + 1}`),
  seq: Array.from({ length: 7 }, (_, i) => `seq-${i + 1}`),
  div: Array.from({ length: 7 }, (_, i) => `div-${i + 1}`),
  status: { passed: 'passed', failed: 'failed', broken: 'broken', skipped: 'skipped', unknown: 'unknown' },
  flaky: 'flaky',
  grid: 'grid',
  axis: 'axis',
  card: 'card',
  border: 'border',
  text: 'text',
  textMuted: 'muted',
}

const point = (id: string, x: number, y: number, size = 10, label = id): PointsChartPoint => ({ id, label, x, y, size, n: size })

const chart: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [point('a', 3, 50, 100, HOSTILE), point('b', 4200, 0, 25), point('c', 40, 10, 1), point('d', 900, 80, 4)],
  medians: { x: 470, y: 30 },
  excluded: { below_min_executions: 1, no_duration: 0, no_evaluated: 0 },
}

type Loose = Record<string, unknown>
const build = (data: PointsChart = chart, textScale?: number) =>
  buildScatterOption({ data, tokens, textScale }) as unknown as Loose
const seriesOf = (option: Loose) => (option.series as Loose[])[0]
const items = (option: Loose) => seriesOf(option).data as Loose[]

describe('buildScatterOption', () => {
  it('puts x on a LOG axis snapped to whole decades, the unit and scale in its name', () => {
    const x = build().xAxis as Loose
    expect(x.type).toBe('log')
    expect(x.logBase).toBe(10)
    expect([x.min, x.max]).toEqual([1, 10000])
    expect(x.name).toBe('p95 duration (ms), log scale')
  })

  it('a linear x starts at 0 and has no scale words', () => {
    const x = build({ ...chart, x: { ...chart.x, scale: 'linear' } }).xAxis as Loose
    expect(x.type).toBe('value')
    // From 0, less the edge pad: the 3 ms test sits on that edge (its span is the data's, 0..4200).
    expect(x.min).toBe(-4200 * SCATTER_EDGE_PAD)
    expect(build({ ...chart, x: { ...chart.x, scale: 'linear' }, points: [point('a', 900, 50)] }).xAxis).toMatchObject({ min: 0 })
    expect(x.name).toBe('p95 duration (ms)')
  })

  it('fixes a percent y at 0..100 and a ratio y at 0..1', () => {
    // No point near an edge here: the plain range, every tick label shown.
    const inner: PointsChart = { ...chart, points: chart.points.map((p) => ({ ...p, y: p.y === 0 ? 20 : p.y })) }
    const y = build(inner).yAxis as Loose
    expect([y.type, y.min, y.max]).toEqual(['value', 0, 100])
    expect(y.axisLabel).toEqual({ color: 'axis' })
    const ratio = build({ ...inner, y: { ...chart.y, unit: 'ratio' }, points: inner.points.map((p) => ({ ...p, y: p.y / 100 })) })
      .yAxis as Loose
    expect([ratio.min, ratio.max]).toEqual([0, 1])
    const count = build({ ...inner, y: { ...chart.y, unit: 'count' } }).yAxis as Loose
    expect([count.min, count.max]).toEqual([0, undefined])
    const logY = build({ ...chart, y: { ...chart.y, scale: 'log' } }).yAxis as Loose
    // The zero is ignored for the extent (a log axis cannot hold it); 10..80 snaps to 10..100, and the
    // 10 sits on the low edge, so that edge moves out by the pad (in decades).
    expect([logY.type, logY.max]).toEqual(['log', 100])
    expect(Math.log10(logY.min as number)).toBeCloseTo(1 - SCATTER_EDGE_PAD, 9)
  })

  it('F-07: the stable tests at 0% get room below the axis, so no mark runs past the plot or over a tick label', () => {
    // Point b sits at 0%: the plot starts below 0 by the edge pad, the axis
    // line moves to the plot's bottom (not y = 0, where the marks are), and
    // the padded edge's label is hidden so the ticks stay 0, 20, ... 100.
    const option = build()
    const y = option.yAxis as Loose
    expect(y.min).toBeCloseTo(-100 * SCATTER_EDGE_PAD, 9)
    expect(y.max).toBe(100)
    expect(y.axisLabel).toEqual({ color: 'axis', showMinLabel: false })
    const x = option.xAxis as Loose
    expect((x.axisLine as Loose).onZero).toBe(false)
    expect(((option.yAxis as Loose).axisLine as Loose).onZero).toBe(false)
    // Marks never draw outside the grid.
    expect(seriesOf(option).clip).toBe(true)
  })

  it('F-07: a test at the first or last decade (1 ms, 10 s) gets room past that edge; the decade ticks stay', () => {
    const edges: PointsChart = { ...chart, points: [point('fast', 1, 50), point('slow', 10000, 100), point('mid', 300, 20)] }
    const option = build(edges)
    const x = option.xAxis as Loose
    const pad = 4 * SCATTER_EDGE_PAD
    expect(Math.log10(x.min as number)).toBeCloseTo(-pad, 9)
    expect(Math.log10(x.max as number)).toBeCloseTo(4 + pad, 9)
    expect(x.axisLabel).toEqual({ color: 'axis', showMinLabel: false, showMaxLabel: false })
    const y = option.yAxis as Loose
    expect([y.min, y.max]).toEqual([0, 100 + 100 * SCATTER_EDGE_PAD])
    expect(y.axisLabel).toEqual({ color: 'axis', showMaxLabel: false })
  })

  it('carries no formatter but the DOM tooltip, and passes the runtime guard', () => {
    const option = build()
    expect(() => assertSafeChartOption(option)).not.toThrow()
    expect(JSON.stringify(option)).not.toMatch(/formatter/i)
    expect(((option.xAxis as Loose).axisLabel as Loose).formatter).toBeUndefined()
  })

  it('NEVER uses large mode (spike S2), even past the dense threshold', () => {
    const many = Array.from({ length: SCATTER_DENSE_POINTS + 1 }, (_, i) => point(`t${i}`, 1 + i, i % 100))
    const option = build({ ...chart, points: many })
    expect(seriesOf(option).large).toBe(false)
    expect(items(option)).toHaveLength(SCATTER_DENSE_POINTS + 1)
  })

  it('draws each point at its data, coloured AND shaped by quadrant', () => {
    const drawn = items(build())
    expect(drawn.map((d) => d.value)).toEqual([[3, 50, 100], [4200, 0, 25], [40, 10, 1], [900, 80, 4]])
    expect(drawn.map((d) => d.symbol)).toEqual(['diamond', 'rect', 'circle', 'triangle'])
    expect(drawn.map((d) => (d.itemStyle as Loose).color)).toEqual(['flaky', 'series-1', 'muted', 'failed'])
    for (const d of drawn) expect((d.itemStyle as Loose).opacity).toBe(SCATTER_ALPHA)
  })

  it('without medians every point is drawn alike', () => {
    const drawn = items(build({ ...chart, medians: undefined }))
    expect(new Set(drawn.map((d) => d.symbol))).toEqual(new Set(['circle']))
    expect(seriesOf(build({ ...chart, medians: undefined })).markLine).toBeUndefined()
  })

  it('sizes by area: the biggest test at the maximum, the others by the square root', () => {
    const drawn = items(build())
    expect(drawn[0].symbolSize).toBe(SCATTER_SYMBOL.max)
    expect(drawn[1].symbolSize).toBe(symbolDiameter(25, 100, false))
    expect(drawn[1].symbolSize).toBe(SCATTER_SYMBOL.min + (SCATTER_SYMBOL.max - SCATTER_SYMBOL.min) * 0.5)
  })

  it('draws the medians as silent, unlabelled dashed lines', () => {
    const markLine = seriesOf(build()).markLine as Loose
    expect(markLine.silent).toBe(true)
    expect((markLine.label as Loose).show).toBe(false)
    expect(markLine.data).toEqual([{ xAxis: 470 }, { yAxis: 30 }])
    expect((markLine.lineStyle as Loose).color).toBe('axis')
  })

  it('has a toolbox-free rectangle brush that fades what is outside it', () => {
    const brush = build().brush as Loose
    expect(brush.toolbox).toEqual([])
    expect([brush.xAxisIndex, brush.yAxisIndex, brush.brushType, brush.brushMode]).toEqual([0, 0, 'rect', 'single'])
    expect(brush.outOfBrush).toEqual({ opacity: OUT_OF_BRUSH_ALPHA })
    expect(brush.inBrush).toEqual({ opacity: 1 })
  })

  it('names the chart only through the wrapper (ECharts aria off)', () => {
    expect(build().aria).toEqual({ enabled: false })
  })

  // Linux e2e (final round): an enterable tooltip on the drag's path took the pointer and the brush never ended.
  it('the tooltip is hoverable (enterable) except in drag mode, where it never takes the pointer', () => {
    expect((build().tooltip as Loose).enterable).toBe(true)
    expect((buildScatterOption({ data: chart, tokens, brushing: true }) as unknown as Loose).tooltip).toMatchObject({ enterable: false })
  })

  it('renders a hostile test name in the tooltip as TEXT', () => {
    const tooltip = build().tooltip as Loose
    const node = (tooltip.formatter as (p: unknown) => HTMLElement)({ dataIndex: 0 })
    expect(node.hasAttribute(TOOLTIP_NODE_ATTRIBUTE)).toBe(true)
    expect(node.textContent).toContain(HOSTILE)
    expect(node.querySelector('img')).toBeNull()
    expect(node.textContent).toContain('Fast and flaky')
  })

  it('a tooltip for params it cannot read is empty', () => {
    const tooltip = build().tooltip as Loose
    const format = tooltip.formatter as (p: unknown) => HTMLElement
    expect(format({}).textContent).toBe('')
    expect(format([{ dataIndex: 1.5 }]).textContent).toBe('')
    expect(format([{ dataIndex: 1 }]).textContent).toContain('4,200 ms')
  })

  it('draws every colour from the resolved tokens', () => {
    const option = build()
    expect(((option.xAxis as Loose).axisLine as Loose).lineStyle).toEqual({ color: 'grid' })
    expect(((option.yAxis as Loose).axisLabel as Loose).color).toBe('axis')
    expect((option.tooltip as Loose).backgroundColor).toBe('card')
    expect(((seriesOf(option).emphasis as Loose).itemStyle as Loose).borderColor).toBe('text')
  })

  it('scales its text and insets in full screen, and is unchanged at 1', () => {
    const plain = build(chart, 1)
    expect((plain.xAxis as Loose).axisLabel).toEqual({ color: 'axis' })
    expect(plain.grid).toEqual(scatterGrid(1))
    const big = build(chart, 1.5)
    expect((big.xAxis as Loose).axisLabel).toEqual({ color: 'axis', fontSize: 18 })
    expect((big.grid as Loose).bottom).toBeGreaterThan((plain.grid as Loose).bottom as number)
    expect(xNameGap(1.5)).toBeGreaterThan(xNameGap(1))
    expect(scatterGrid(Number.NaN)).toEqual(scatterGrid(1))
  })
})

describe('pieces', () => {
  it('logExtent snaps to decades, gives one value a decade, and survives nothing', () => {
    expect(logExtent([3, 4200])).toEqual([1, 10000])
    expect(logExtent([100])).toEqual([100, 1000])
    expect(logExtent([20, 20])).toEqual([10, 100])
    expect(logExtent([])).toEqual([1, 10])
    expect(logExtent([0.5, 2])).toEqual([0.1, 10])
  })

  it('padExtent: only a touched edge moves, by the share of the span; log in decades; nothing drawable, nothing moves', () => {
    const plain = { min: 0, max: 100, showMinLabel: true, showMaxLabel: true }
    expect(padExtent({ min: 0, max: 100 }, [10, 50, 90], false)).toEqual(plain)
    // Just inside the pad counts as touching; just outside does not.
    expect(padExtent({ min: 0, max: 100 }, [100 * SCATTER_EDGE_PAD - 0.01], false).showMinLabel).toBe(false)
    expect(padExtent({ min: 0, max: 100 }, [100 * SCATTER_EDGE_PAD + 0.01], false)).toEqual(plain)
    expect(padExtent({ min: 0, max: 1 }, [1], false)).toEqual({ min: 0, max: 1 + SCATTER_EDGE_PAD, showMinLabel: true, showMaxLabel: false })
    // An open top (a count): the low edge pads against the data's own span; the top is ECharts'.
    expect(padExtent({ min: 0 }, [0, 50], false)).toEqual({ min: -50 * SCATTER_EDGE_PAD, max: undefined, showMinLabel: false, showMaxLabel: true })
    // Log: zero and negatives cannot be drawn, so they touch nothing.
    expect(padExtent({ min: 10, max: 100 }, [0, -5, 50], true)).toEqual({ min: 10, max: 100, showMinLabel: true, showMaxLabel: true })
    const log = padExtent({ min: 10, max: 1000 }, [10], true)
    expect(Math.log10(log.min as number)).toBeCloseTo(1 - 2 * SCATTER_EDGE_PAD, 9)
    expect(log.max).toBe(1000)
    // No span (a count axis with nothing above 0), no values: unchanged.
    expect(padExtent({ min: 0 }, [0], false)).toEqual({ min: 0, max: undefined, showMinLabel: true, showMaxLabel: true })
    expect(padExtent({ min: 0, max: 100 }, [], false)).toEqual(plain)
    expect(padExtent({ min: 0, max: 100 }, [Number.NaN], false)).toEqual(plain)
  })

  it('symbolDiameter: min for nothing, max for the largest, dense is smaller', () => {
    expect(symbolDiameter(0, 100, false)).toBe(SCATTER_SYMBOL.min)
    expect(symbolDiameter(5, 0, false)).toBe(SCATTER_SYMBOL.min)
    expect(symbolDiameter(100, 100, false)).toBe(SCATTER_SYMBOL.max)
    expect(symbolDiameter(100, 100, true)).toBe(SCATTER_SYMBOL_DENSE.max)
    expect(symbolDiameter(500, 100, false)).toBe(SCATTER_SYMBOL.max)
  })

  it('a quadrant colour for each quadrant, falling back to the axis without a series palette', () => {
    expect(quadrantColor('slow-flaky', tokens)).toBe('failed')
    expect(quadrantColor('slow-stable', { ...tokens, series: [] })).toBe('axis')
    expect(Object.keys(QUADRANT_SYMBOLS)).toHaveLength(4)
    expect(new Set(Object.values(QUADRANT_SYMBOLS)).size).toBe(4)
  })

  it('axisName adds the scale only for a log axis', () => {
    expect(axisName(chart.x)).toBe('p95 duration (ms), log scale')
    expect(axisName(chart.y)).toBe('Failure rate (%)')
  })

  it('the tooltip mark is a small box around the pointer', () => {
    expect(scatterPointMark([100, 50], undefined, { width: 400, height: 300 })).toEqual({ left: 94, top: 44, width: 12, height: 12 })
    expect(scatterPointMark([], undefined, { width: 400, height: 300 })).toEqual({ left: -6, top: -6, width: 12, height: 12 })
  })
})
