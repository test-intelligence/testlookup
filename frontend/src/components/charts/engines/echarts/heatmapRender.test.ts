/**
 * The heatmap option, drawn by the REAL ECharts (server-side SVG renderer, so
 * jsdom needs no canvas), and asserted on what it draws.
 *
 * Option-level tests say what we ASKED for; these catch what ECharts then does
 * on its own — thinning an axis's labels, drawing a colour bar for a ramp with
 * nothing on it.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { HeatmapChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import type { ChartTokens } from '../../tokens'
import { buildHeatmapOption, HEATMAP_ALL_LABELS_MAX, type HeatmapMatrix, type NumericMatrix } from './heatmapOption'

// The production base (`core.ts`) registers the legend too: the "No data" key is one.
echarts.use([SVGRenderer, GridComponent, TooltipComponent, LegendComponent, AriaComponent, HeatmapChart, VisualMapComponent])
// jsdom has no 2d context. With no canvas, zrender measures text from its own
// per-character width table (sans-serif metrics) — close to a browser, and
// silent, where jsdom's canvas logs "not implemented" on every measurement.
echarts.setPlatformAPI({ createCanvas: () => null as unknown as HTMLCanvasElement })

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

const WIDTH = 640
const HEIGHT = 320
let chart: ReturnType<typeof echarts.init> | null = null
afterEach(() => {
  chart?.dispose()
  chart = null
})

/** Every `<text>` ECharts drew, decoded. */
function drawnTexts(data: HeatmapMatrix, width = WIDTH): string[] {
  chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width, height: HEIGHT })
  // The tooltip formatter is a DOM builder; the SSR render never calls it.
  chart.setOption(buildHeatmapOption({ data, tokens, description: 'test', chartWidth: width }))
  const svg = chart.renderToSVGString()
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml')
  return Array.from(doc.querySelectorAll('text'), (node) => node.textContent ?? '')
}

/** Has ECharts drawn a colour bar? Its handle-free continuous visualMap draws the ramp as a gradient-filled rect. */
function drawsColourBar(data: HeatmapMatrix): boolean {
  chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: WIDTH, height: HEIGHT })
  chart.setOption(buildHeatmapOption({ data, tokens, description: 'test', chartWidth: WIDTH }))
  return /<linearGradient/.test(chart.renderToSVGString())
}

const HOSTILE = '<img src=x onerror="window.__xss=1">'
const twoCells: NumericMatrix = {
  kind: 'matrix',
  value_type: 'count',
  x_labels: [HOSTILE, 'benign'],
  y_labels: ['<b>suite</b>'],
  cells: [
    { x: 0, y: 0, value: 7, n: 7 },
    { x: 1, y: 0, value: 3, n: 3 },
  ],
}

function matrixOf(xLabels: string[], yLabels: string[], value: (x: number, y: number) => number | null): NumericMatrix {
  return {
    kind: 'matrix',
    value_type: 'rate',
    x_labels: xLabels,
    y_labels: yLabels,
    cells: yLabels.flatMap((_, y) => xLabels.map((__, x) => ({ x, y, value: value(x, y), n: 10 }))),
  }
}

/** A drawn label is the category, or a truncation of it (ending in an ellipsis). */
function drawnAs(texts: string[], label: string): boolean {
  return texts.some((text) => {
    if (text === label) return true
    const stem = text.replace(/(\.\.\.|…)$/, '')
    return stem.length > 0 && stem.length < label.length && label.startsWith(stem)
  })
}

describe('heatmap axis labels, as ECharts draws them', () => {
  it('draws BOTH x labels of the hostile fixture: a long neighbour does not thin out "benign"', () => {
    const texts = drawnTexts(twoCells)
    expect(drawnAs(texts, 'benign'), texts.join(' | ')).toBe(true)
    expect(drawnAs(texts, HOSTILE), texts.join(' | ')).toBe(true)
    expect(drawnAs(texts, '<b>suite</b>'), texts.join(' | ')).toBe(true)
  })

  it(`draws every category, on both axes, whenever there are at most ${HEATMAP_ALL_LABELS_MAX}`, () => {
    const long = (prefix: string, i: number) => `${prefix}-${i}-a-rather-long-category-name-from-ci`
    for (const count of [2, 5, HEATMAP_ALL_LABELS_MAX]) {
      const xs = Array.from({ length: count }, (_, i) => long('run', i))
      const ys = Array.from({ length: count }, (_, i) => long('suite', i))
      const texts = drawnTexts(matrixOf(xs, ys, (x, y) => ((x + y) % 5) / 4))
      for (const label of [...xs, ...ys]) expect(drawnAs(texts, label), `${count}: ${label}`).toBe(true)
    }
  })

  it('a long label is cut to its own column (never dropped), and past the limit ECharts may thin', () => {
    type Axis = { axisLabel: { interval?: unknown; width?: number; overflow?: string } }
    const optionFor = (data: HeatmapMatrix) =>
      buildHeatmapOption({ data, tokens, description: 'test', chartWidth: WIDTH }) as unknown as { xAxis: Axis; yAxis: Axis }
    const few = optionFor(twoCells)
    expect(few.xAxis.axisLabel).toMatchObject({ interval: 0, overflow: 'truncate' })
    // The plot is 640 - 120 - 16 = 504 px, two columns of 252; each label gets its column less a gap.
    expect(few.xAxis.axisLabel.width).toBeGreaterThan(200)
    expect(few.xAxis.axisLabel.width).toBeLessThan(252)
    expect(few.yAxis.axisLabel).toMatchObject({ interval: 0, overflow: 'truncate' })

    const many = Array.from({ length: HEATMAP_ALL_LABELS_MAX + 1 }, (_, i) => `run ${i}`)
    const dense = optionFor(matrixOf(many, ['s'], () => 0.5))
    // Past the limit every k-th column is labelled, and each shown label gets
    // the k columns it stands for (13 columns of 38.8 px; "run 12" needs two).
    expect(dense.xAxis.axisLabel.interval).toBe(1)
    expect(dense.xAxis.axisLabel.width).toBeGreaterThan(60)
  })

  // Wave 2.6: the Trends heatmap is a day axis of 14 to 90 columns. Each label
  // used to be cut to ONE column's width however thinned the axis was: 14 days
  // in a 470 px plot printed "S…" under every other column, and 90 days (the
  // width clamped to 1 px) printed nothing at all.
  const days = (count: number) =>
    Array.from({ length: count }, (_, i) => new Date(Date.UTC(2026, 6, 1 + i)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }))
  it.each([
    [14, 606],
    [30, 606],
    [90, 606],
    [90, 1100],
  ])('a %i-day axis in a %i px chart prints whole day labels, never a cut stub or nothing', (count, width) => {
    const labels = days(count)
    const texts = drawnTexts(matrixOf(labels, ['a', 'b'], () => 0.9), width).filter((text) => labels.some((l) => drawnAs([text], l)))
    const whole = texts.filter((text) => labels.includes(text))
    expect(whole.length, texts.join(' | ')).toBeGreaterThanOrEqual(4)
    expect(texts.filter((text) => !labels.includes(text)), 'cut labels').toEqual([])
  })
})

describe('the heatmap colour bar', () => {
  it('is drawn when there is a measured value to read against it', () => {
    expect(drawsColourBar(twoCells)).toBe(true)
  })

  it('is NOT drawn when no cell has a value: a scale for nothing reads as data', () => {
    const empty = matrixOf(['a', 'b', 'c'], ['s', 't'], () => null)
    expect(drawsColourBar(empty)).toBe(false)
    // …and its end labels ("0.0%" / "100.0%") are gone with it.
    const texts = drawnTexts(empty)
    expect(texts.filter((text) => /%$/.test(text)), texts.join(' | ')).toEqual([])
  })
})

/**
 * R2-18: the hatched no-data cell had no key. The key is ECharts' own legend
 * entry for the no-data series, so these read what ECharts DRAWS: the swatch
 * filled with the very pattern the hatched cells use, in its own row below
 * the colour bar (at a phone width the two would collide side by side).
 */
describe('the "No data" key, as ECharts draws it (R2-18)', () => {
  const NARROW = 320
  function drawn(data: HeatmapMatrix): Document {
    chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: NARROW, height: HEIGHT })
    chart.setOption(buildHeatmapOption({ data, tokens, description: 'test', chartWidth: NARROW }))
    return new DOMParser().parseFromString(chart.renderToSVGString(), 'image/svg+xml')
  }
  /** `translate(x y)` / `matrix(a,b,c,d,x,y)` -> its y offset. */
  const offsetY = (node: Element | null | undefined) => {
    const t = node?.getAttribute('transform') ?? ''
    const m = /translate\(\s*[-\d.]+[\s,]+([-\d.]+)\)/.exec(t) ?? /matrix\((?:[-\d.]+,){5}([-\d.]+)\)/.exec(t)
    return m ? Number(m[1]) : Number.NaN
  }
  const gap = matrixOf(['a', 'b', 'c'], ['s', 't'], (x, y) => (x === 1 && y === 0 ? null : 0.5))

  it('a hatched cell is keyed "No data", with a swatch drawn in the cell\'s own pattern', () => {
    const doc = drawn(gap)
    const label = Array.from(doc.querySelectorAll('text')).find((node) => node.textContent === 'No data')
    expect(label, 'no "No data" key drawn').toBeDefined()
    // The hatched cell: the no-data series' rect, outlined in the grid colour.
    const cell = doc.querySelector('path[stroke="grid"][fill^="url(#"]')
    const pattern = cell?.getAttribute('fill')
    expect(pattern).toMatch(/^url\(#.+\)$/)
    expect(doc.querySelector(`pattern#${pattern?.slice(5, -1)}`)).not.toBeNull()
    // The swatch, beside its label: the SAME pattern, outlined like the kit's legend swatches.
    const swatch = doc.querySelector(`path[stroke="border"][fill="${pattern}"]`)
    expect(swatch, 'the swatch is not filled with the cell pattern').not.toBeNull()
    expect(offsetY(swatch)).toBeCloseTo(offsetY(label), 0)
  })

  it('in its own row, BELOW the colour bar (never on top of it, at a phone width)', () => {
    const doc = drawn(gap)
    const bar = doc.querySelector('polygon[fill^="url(#"]')
    // The bar is drawn turned (its matrix's y offset is its bottom edge).
    const barBottom = offsetY(bar)
    expect(Number.isFinite(barBottom)).toBe(true)
    const label = Array.from(doc.querySelectorAll('text')).find((node) => node.textContent === 'No data')
    expect(offsetY(label)).toBeGreaterThanOrEqual(barBottom)
    expect(offsetY(label)).toBeLessThan(HEIGHT)
  })

  it('nothing hatched, no key', () => {
    const texts = Array.from(drawn(matrixOf(['a', 'b'], ['s'], () => 0.5)).querySelectorAll('text'), (node) => node.textContent)
    expect(texts).not.toContain('No data')
  })
})
