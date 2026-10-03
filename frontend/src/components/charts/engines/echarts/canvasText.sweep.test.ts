/**
 * R1B-1: zrender cuts a too-wide label by measuring its PREFIXES, each cached
 * in a plain-object LRU, so a label that only STARTS WITH `__proto__` or
 * `constructor` polluted `Object.prototype` / `Object` once it was cut at the
 * right width (R1 found 61 px and 69 px). These tests drive zrender's REAL
 * truncation and measurement over a width sweep, directly and through the
 * real ECharts renderer fed by our option builders, and assert that
 * `Object.prototype` and `Object` gain no own key.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { BarChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { truncateText } from 'zrender/lib/graphic/helper/parseText.js'
import type { PointsChart } from '@/lib/viz/contracts'
import { buildTimeSeriesModel, timeSeriesFromTrends } from '../../timeSeriesModel'
import { readChartTokens } from '../../tokens'
import { canvasSafeLabels, canvasSafeText } from './canvasText'
import { buildHeatmapOption, type NumericMatrix } from './heatmapOption'
import { buildScatterOption } from './scatterOption'
import { buildTimeSeriesOption } from './timeSeriesOption'

echarts.use([
  SVGRenderer,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AriaComponent,
  MarkLineComponent,
  VisualMapComponent,
  HeatmapChart,
  LineChart,
  BarChart,
  ScatterChart,
])
// No canvas in jsdom: zrender measures from its own width table, silently.
echarts.setPlatformAPI({ createCanvas: () => null as unknown as HTMLCanvasElement })

const tokens = readChartTokens()

/** Every option builder's source, as text (`./xOption.ts`). */
const BUILDERS = import.meta.glob(['./*Option.ts'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>

/** Labels that START WITH a member name (R1's four, plus every other member and a two-line one). */
const PREFIXED = [
  '__proto__bomb_label_long',
  'constructor_payment_tests',
  'toString_formatting_suite',
  'valueOf_checks_and_more',
  ...Object.getOwnPropertyNames(Object.prototype).map((member) => `${member} and a long tail to cut`),
  'checkout\n__proto__ second line that is long',
]

type Snapshot = { proto: (string | symbol)[]; ctor: (string | symbol)[] }
const snapshot = (): Snapshot => ({ proto: Reflect.ownKeys(Object.prototype), ctor: Reflect.ownKeys(Object) })

/** Removes whatever a polluting run added, so no later test runs on a broken prototype. */
function restore(before: Snapshot) {
  for (const key of Reflect.ownKeys(Object.prototype)) {
    if (!before.proto.includes(key)) delete (Object.prototype as Record<string | symbol, unknown>)[key]
  }
  for (const key of Reflect.ownKeys(Object)) {
    if (!before.ctor.includes(key)) delete (Object as unknown as Record<string | symbol, unknown>)[key]
  }
}

/** The own keys `run` added to `Object.prototype` and to `Object` (cleaned up again before returning). */
function addedKeys(run: () => void): { proto: string[]; ctor: string[] } {
  const before = snapshot()
  try {
    run()
    const after = snapshot()
    return {
      proto: after.proto.filter((key) => !before.proto.includes(key)).map(String),
      ctor: after.ctor.filter((key) => !before.ctor.includes(key)).map(String),
    }
  } finally {
    restore(before)
  }
}

const WIDTHS = Array.from({ length: 181 }, (_, i) => 20 + i) // 20..200 px, step 1

let chart: ReturnType<typeof echarts.init> | null = null
afterEach(() => {
  chart?.dispose()
  chart = null
})

function render(option: unknown, width: number, height = 320) {
  chart?.dispose()
  chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width, height })
  chart.setOption(option as Parameters<typeof chart.setOption>[0])
  chart.renderToSVGString()
}

describe('canvasSafeText against zrender’s real truncation (R1B-1)', () => {
  it('a member-PREFIXED label gets a leading word joiner on every line that starts with a member; others are untouched', () => {
    expect(canvasSafeText('__proto__bomb_label_long')).toBe(`${String.fromCharCode(0x2060)}__proto__bomb_label_long`)
    expect(canvasSafeText('__proto__')).toBe(`${String.fromCharCode(0x2060)}__proto__`)
    expect(canvasSafeText('a\nconstructor_x')).toBe(`a\n${String.fromCharCode(0x2060)}constructor_x`)
    for (const text of ['payments', '', 'proto', 'my constructor', ' __proto__', 'x__proto__', 'Constructor']) {
      expect(canvasSafeText(text)).toBe(text)
    }
    expect(canvasSafeLabels(['a', 'b'])).toEqual(['a', 'b'])
  })

  it('cutting every prefixed label at every width 20..200 px leaves Object.prototype and Object untouched', () => {
    const added = addedKeys(() => {
      for (const label of PREFIXED.map(canvasSafeText)) {
        for (const line of label.split('\n')) {
          for (const width of WIDTHS) truncateText(line, width, '12px sans-serif', '…')
        }
      }
    })
    expect(added).toEqual({ proto: [], ctor: [] })
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
  })

  it('the heatmap, drawn by the real ECharts, cuts prefixed row and column labels at every column width without pollution', () => {
    const matrix: NumericMatrix = {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: ['constructor_payment_tests', '__proto__bomb_label_long'],
      y_labels: ['__proto__bomb_label_long_enough_to_be_cut_in_the_gutter', 'toString_formatting_suite', 'checkout'],
      cells: [
        { x: 0, y: 0, value: 0.5, n: 2 },
        { x: 1, y: 1, value: 1, n: 1 },
        { x: 0, y: 2, value: null, n: 0 },
      ],
    }
    // Two columns: each x label's box is (width - 136) / 2 - 8 px, so 192..552 sweeps it 20..200 px.
    const added = addedKeys(() => {
      for (let width = 192; width <= 552; width += 2) {
        render(buildHeatmapOption({ data: matrix, tokens, description: 'd', chartWidth: width }), width)
      }
      // The row gutter grows with the text scale: sweep it too.
      for (let scale = 1; scale <= 2; scale += 0.05) {
        render(buildHeatmapOption({ data: matrix, tokens, description: 'd', chartWidth: 640, textScale: scale }), 640)
      }
    })
    expect(added).toEqual({ proto: [], ctor: [] })
  })

  it('time-series marker labels and scatter axis names, drawn by the real ECharts, leave the prototype untouched', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends([
        { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
        { date: '2026-03-02', passed: 8, failed: 2, skipped: 0, broken: 0, total: 10, pass_rate: 80 },
      ]),
      releases: [
        { id: 'r1', name: '__proto__bomb_label_long', date: '2026-03-02T00:00:00Z' },
        { id: 'r2', name: 'constructor_payment_tests', date: '2026-03-01T00:00:00Z' },
      ],
    })
    const points: PointsChart = {
      kind: 'points',
      x: { key: 'p95_duration_ms', label: 'constructor_payment_tests', unit: 'ms', scale: 'linear' },
      y: { key: 'failure_rate', label: '__proto__bomb_label_long', unit: 'percent', scale: 'linear' },
      size: { key: 'executions', label: 'Executions' },
      points: [{ id: 'a', label: '__proto__x', x: 3, y: 50, size: 10, n: 10 }],
      medians: { x: 3, y: 50 },
      excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 },
    }
    const added = addedKeys(() => {
      for (const width of [120, 200, 320, 640]) {
        render(buildTimeSeriesOption({ model, tokens, description: 'd' }), width)
        render(buildScatterOption({ data: points, tokens }), width)
      }
    })
    expect(added).toEqual({ proto: [], ctor: [] })
  })

  it('control: the same sweep WITHOUT the helper does pollute (the test can see the defect)', () => {
    // A font of its own, so the polluted LRU is never used again by another test.
    const added = addedKeys(() => {
      for (const label of PREFIXED.slice(0, 2)) {
        for (const width of WIDTHS) truncateText(label, width, '13px control-only-font', '…')
      }
    })
    expect([...added.proto, ...added.ctor].length).toBeGreaterThan(0)
  })

  it('no option builder wraps text (`overflow: break | breakAll` cuts lines mid-label, which the helper does not cover)', () => {
    expect(Object.keys(BUILDERS).length).toBeGreaterThanOrEqual(4)
    for (const [name, source] of Object.entries(BUILDERS)) {
      expect(source, name).not.toMatch(/overflow:\s*['"]break(All)?['"]/)
    }
    // The scan can see a wrapping builder (a self-check).
    expect("overflow: 'breakAll'").toMatch(/overflow:\s*['"]break(All)?['"]/)
  })
})
