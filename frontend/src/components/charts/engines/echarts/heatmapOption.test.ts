import { describe, expect, it } from 'vitest'
import { echartsDecal, type ChartTokens } from '../../tokens'
import { TOOLTIP_NODE_ATTRIBUTE } from '../../tooltip'
import {
  buildHeatmapOption,
  defaultSalient,
  formatHeatmapValue,
  heatmapTarget,
  type NumericMatrix,
  type StatusMatrix,
} from './heatmapOption'

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

const matrix: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: [HOSTILE, 'day 2'],
  y_labels: ['<b>suite</b>'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 10 },
    { x: 1, y: 0, value: null, n: 0 },
  ],
}

type Loose = Record<string, unknown>
const ramp = (option: Loose) => (option.visualMap as Loose[]).find((v) => v.id === 'ramp') as Loose
const selfColoured = (option: Loose) => (option.visualMap as Loose[]).find((v) => v.id === 'self-coloured') as Loose
const build = (overrides: Partial<Parameters<typeof buildHeatmapOption>[0]> = {}) =>
  buildHeatmapOption({ data: matrix, tokens, description: 'Pass rate by suite.', ...overrides }) as unknown as Loose

describe('buildHeatmapOption', () => {
  it('draws every colour from the resolved tokens', () => {
    const option = build({ salient: 'high' })
    const visualMap = ramp(option)
    expect((visualMap.inRange as Loose).color).toEqual(tokens.seq)
    const xAxis = option.xAxis as Loose
    expect((xAxis.axisLabel as Loose).color).toBe('axis')
    expect(((xAxis.axisLine as Loose).lineStyle as Loose).color).toBe('grid')
    const tooltip = option.tooltip as Loose
    expect(tooltip.backgroundColor).toBe('card')
    expect(tooltip.borderColor).toBe('border')
  })

  it('keeps null as "no data" — never zero', () => {
    const series = (option: Loose) => (option.series as Loose[])[0]
    expect(series(build()).data).toEqual([
      [0, 0, 0.5],
      [1, 0, '-'],
    ])
  })

  it('scales rate to 0..1 and count to its largest measured value', () => {
    expect(ramp(build()).max).toBe(1)
    const counts: NumericMatrix = { ...matrix, value_type: 'count', cells: [{ x: 0, y: 0, value: 42, n: 42 }] }
    expect(ramp(build({ data: counts })).max).toBe(42)
  })

  it('labels both ends of the colour ramp, so a colour can be read as a value', () => {
    // [max, min], ECharts' order. Without these the first Linux baselines drew
    // a bare colour bar — and the salient end flips with the metric.
    expect(ramp(build()).text).toEqual(['100.0%', '0.0%'])
    const counts: NumericMatrix = { ...matrix, value_type: 'count', cells: [{ x: 0, y: 0, value: 42, n: 42 }] }
    expect(ramp(build({ data: counts })).text).toEqual(['42', '0'])
  })

  it('keeps animation off by default', () => {
    const option = build()
    expect(option.animation).toBe(false)
    expect(build({ animate: true }).animation).toBe(true)
  })

  it('uses a DOM-node tooltip formatter — never a string or a string-returning function', () => {
    const formatter = (build().tooltip as Loose).formatter as (params: unknown) => unknown
    expect(typeof formatter).toBe('function')
    const out = formatter({ value: [0, 0, 0.5] })
    expect(out).toBeInstanceOf(HTMLElement)
    const node = out as HTMLElement
    expect(node.hasAttribute(TOOLTIP_NODE_ATTRIBUTE)).toBe(true)
    // The hostile x label and y label are text; nothing was parsed as markup.
    expect(node.textContent).toContain(HOSTILE)
    expect(node.textContent).toContain('<b>suite</b>')
    expect(node.textContent).toContain('50.0%')
    expect(node.querySelector('img, b')).toBeNull()
  })

  it('tooltips an empty cell as "No data", and ignores params it cannot place', () => {
    const formatter = (build().tooltip as Loose).formatter as (params: unknown) => HTMLElement
    expect(formatter([{ value: [1, 0, '-'] }]).textContent).toContain('No data')
    expect(formatter({ value: 'nonsense' }).textContent).toBe('')
    expect(formatter(undefined).textContent).toBe('')
  })
})

describe('buildHeatmapOption — no data, salience, emphasis, status (fix round)', () => {
  const seriesList = (option: Loose) => option.series as Loose[]

  it('draws a no-data cell hatched on the card colour — never as the lowest ramp step', () => {
    const option = build()
    const noData = seriesList(option).find((s) => s.id === 'no-data') as Loose
    expect(noData, 'no no-data series').toBeDefined()
    expect(noData.data).toEqual([[1, 0, 0]])
    const itemStyle = noData.itemStyle as Loose
    expect(itemStyle.color).toBe('card')
    const decal = itemStyle.decal as Loose
    expect(decal).toBeDefined()
    expect(decal.color).toBe('axis')
    // The ramp applies to the measured series only; the no-data series gets a hidden,
    // colourless visualMap (ECharts requires one per heatmap series).
    expect(ramp(option).seriesIndex).toBe(0)
    expect(selfColoured(option)).toMatchObject({ show: false, seriesIndex: [1], inRange: { opacity: 1 } })
    expect(JSON.stringify(selfColoured(option))).not.toContain('color')
    // Hovering the hatched cell tooltips "No data".
    const formatter = (option.tooltip as Loose).formatter as (params: unknown) => HTMLElement
    expect(formatter({ seriesId: 'no-data', value: [1, 0, 0] }).textContent).toContain('No data')
  })

  it('no no-data series when every cell is measured', () => {
    const full: NumericMatrix = { ...matrix, cells: [{ x: 0, y: 0, value: 0.5, n: 1 }] }
    expect(seriesList(build({ data: full })).map((s) => s.id)).not.toContain('no-data')
  })

  it('the problem end is the salient end: a rate defaults to salient "low" (ramp reversed), a count to "high"', () => {
    const colours = (option: Loose) => (ramp(option).inRange as Loose).color
    expect(colours(build())).toEqual([...tokens.seq].reverse())
    const counts: NumericMatrix = { ...matrix, value_type: 'count', cells: [{ x: 0, y: 0, value: 4, n: 4 }] }
    expect(colours(build({ data: counts }))).toEqual(tokens.seq)
    expect(colours(build({ salient: 'high' }))).toEqual(tokens.seq)
    expect(colours(build({ data: counts, salient: 'low' }))).toEqual([...tokens.seq].reverse())
    expect(defaultSalient('rate')).toBe('low')
    expect(defaultSalient('count')).toBe('high')
  })

  it('every heatmap series is targeted by a visualMap (ECharts throws "Heatmap must use with visualMap" otherwise)', () => {
    const status: StatusMatrix = {
      kind: 'matrix',
      value_type: 'status',
      x_labels: ['r1', 'r2'],
      y_labels: ['s'],
      cells: [
        { x: 0, y: 0, value: 'failed', n: 1 },
        { x: 1, y: 0, value: null, n: 0 },
      ],
    }
    const full: NumericMatrix = { ...matrix, cells: [{ x: 0, y: 0, value: 0.5, n: 1 }] }
    for (const option of [
      build(),
      build({ data: full }),
      buildHeatmapOption({ data: status, tokens, description: 'd' }) as unknown as Loose,
      buildHeatmapOption({ data: { ...status, cells: [status.cells[0]] }, tokens, description: 'd' }) as unknown as Loose,
    ]) {
      const targeted = new Set(
        (option.visualMap as Loose[]).flatMap((v) => (Array.isArray(v.seriesIndex) ? v.seriesIndex : [v.seriesIndex])),
      )
      seriesList(option).forEach((_, index) => expect(targeted.has(index), `series ${index}`).toBe(true))
    }
  })

  it('turns off ECharts\' own aria label: the chart is named once, by our wrapper', () => {
    expect(build().aria).toEqual({ enabled: false })
  })

  it('the active cell has a 2px text-colour outline with a card-colour halo', () => {
    const emphasis = (seriesList(build())[0].emphasis as Loose).itemStyle as Loose
    expect(emphasis).toMatchObject({ borderColor: 'text', borderWidth: 2, shadowColor: 'card' })
    expect(emphasis.shadowBlur as number).toBeGreaterThan(0)
  })

  it('a status matrix fills each cell with its status colour AND its status decal', () => {
    const status: StatusMatrix = {
      kind: 'matrix',
      value_type: 'status',
      x_labels: ['r1', 'r2', 'r3'],
      y_labels: ['suite'],
      cells: [
        { x: 0, y: 0, value: 'passed', n: 1 },
        { x: 1, y: 0, value: 'failed', n: 1 },
        { x: 2, y: 0, value: null, n: 0 },
      ],
    }
    const option = buildHeatmapOption({ data: status, tokens, description: 'd' }) as unknown as Loose
    // No colour ramp: the status series (and the no-data one) only get the hidden pass-through.
    expect((option.visualMap as Loose[]).map((v) => v.id)).toEqual(['self-coloured'])
    expect(selfColoured(option).seriesIndex).toEqual([0, 1])
    const items = seriesList(option)[0].data as Loose[]
    expect(items[0]).toMatchObject({ value: [0, 0, 0], itemStyle: { color: 'passed' } })
    expect((items[0].itemStyle as Loose).decal).toBeUndefined() // passed is the solid encoding
    expect(items[1]).toMatchObject({ value: [1, 0, 0], itemStyle: { color: 'failed', decal: echartsDecal('failed', tokens) } })
    expect(items[2]).toEqual([2, 0, '-'])
    const noData = seriesList(option).find((s) => s.id === 'no-data') as Loose
    expect(noData.data).toEqual([[2, 0, 0]])
    const formatter = (option.tooltip as Loose).formatter as (params: unknown) => HTMLElement
    expect(formatter({ value: [1, 0, 0] }).textContent).toContain('Failed')
  })

  it('keyboard targets: a measured cell is series 0, a no-data cell is its index in the no-data series', () => {
    expect(heatmapTarget(matrix, 0)).toEqual({ seriesIndex: 0, dataIndex: 0 })
    expect(heatmapTarget(matrix, 1)).toEqual({ seriesIndex: 1, dataIndex: 0 })
  })

  it('scales a count past the spread-argument limit', () => {
    const cells = Array.from({ length: 300_000 }, (_, i) => ({ x: i, y: 0, value: i, n: 1 }))
    const big: NumericMatrix = { ...matrix, value_type: 'count', x_labels: cells.map((c) => String(c.x)), cells }
    expect(ramp(build({ data: big })).max).toBe(299_999)
  })
})

describe('formatHeatmapValue', () => {
  it('formats a rate as a percentage and a count with the shared number formatter', () => {
    expect(formatHeatmapValue('rate', 0.9234)).toBe('92.3%')
    expect(formatHeatmapValue('count', 7)).toBe('7')
    expect(formatHeatmapValue('count', 1234)).toBe('1,234')
  })
})
