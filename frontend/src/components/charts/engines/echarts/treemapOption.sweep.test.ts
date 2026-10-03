/**
 * R1B-1, the treemap part: zrender cuts a tile's label to the tile by
 * measuring PREFIXES of each line, each cached in a plain-object LRU, so a
 * suite, class or test name that only STARTS WITH `__proto__` or
 * `constructor` polluted `Object.prototype` / `Object` once a tile cut it at
 * the right width. These tests draw `buildTreemapOption`'s option with the
 * REAL ECharts treemap (SVG, server-side: jsdom has no canvas, and zrender
 * then measures from its own width table) over a sweep of tile widths and
 * heights, and assert that `Object.prototype` and `Object` gain no own key.
 * The harness is X1's (`canvasText.sweep.test.ts`).
 */
import { afterEach, describe, expect, it } from 'vitest'
import { TreemapChart } from 'echarts/charts'
import { AriaComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import type { TreeNodeStats } from '@/lib/viz/contracts'
import { readChartTokens } from '../../tokens'
import type { CoverageChild, CoverageColorBy } from '../../coverageMap.model'
import { buildTreemapOption } from './treemapOption'

echarts.use([SVGRenderer, TooltipComponent, AriaComponent, TreemapChart])
echarts.setPlatformAPI({ createCanvas: () => null as unknown as HTMLCanvasElement })

const tokens = readChartTokens()

const stats = (over: Partial<TreeNodeStats> = {}): TreeNodeStats => ({
  test_count: 10,
  executions: 40,
  pass_rate: 96,
  flaky_count: 1,
  flaky_share: 0.12,
  last_executed_at: '2026-09-30T00:00:00Z',
  staleness_days: 3,
  recency: 'seen',
  ...over,
})

const suite = (label: string, s: TreeNodeStats = stats()): CoverageChild => ({
  node: { id: `s:${label}`, parent_id: 'all', label, value: s.test_count, measure: s.pass_rate, stats: s },
  kind: 'suite',
  key: label,
})

/** Names that START WITH a member name: R1's two, every other member, a class path and a second line. */
const PREFIXED: CoverageChild[] = [
  suite('__proto__bomb_label_long'),
  suite('constructor_payment_tests'),
  ...Object.getOwnPropertyNames(Object.prototype).map((member) => suite(`${member} and a long tail to cut`)),
  // A class level draws the LAST path segment: the prefix appears only after shortening.
  { ...suite('tests/api/__proto__bomb_file_long.py'), kind: 'class' as const, key: 'k' },
  suite('checkout\n__proto__ second line that is long'),
  // A gap draws its name alone (no value line).
  suite('hasOwnProperty_idle_suite', stats({ executions: 0, pass_rate: null, recency: 'never', staleness_days: null })),
]

type Snapshot = { proto: (string | symbol)[]; ctor: (string | symbol)[] }
const snapshot = (): Snapshot => ({ proto: Reflect.ownKeys(Object.prototype), ctor: Reflect.ownKeys(Object) })

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

let chart: ReturnType<typeof echarts.init> | null = null
afterEach(() => {
  chart?.dispose()
  chart = null
})

function draw(children: readonly CoverageChild[], colorBy: CoverageColorBy, width: number, height: number, textScale = 1) {
  chart?.dispose()
  chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width, height })
  chart.setOption(buildTreemapOption({ children, colorBy, tokens, textScale }) as Parameters<typeof chart.setOption>[0])
  return chart.renderToSVGString()
}

/** Two equal tiles side by side: each tile's label box is about `width / 2 - 12` px wide. */
const pairWith = (child: CoverageChild): CoverageChild[] => [child, suite('filler')]

describe('the treemap against zrender’s real truncation (R1B-1)', () => {
  it('draws the labels it is given: the sweep below really cuts them', () => {
    const svg = draw(pairWith(PREFIXED[0]), 'pass_rate', 160, 80)
    // Cut ("…") at this width, with the value line under the name (F-03).
    expect(svg).toContain('…')
    expect(svg).toContain('96.0%')
  })

  it('cutting every member-PREFIXED name at every tile width 20..200 px leaves Object.prototype and Object untouched', () => {
    const added = addedKeys(() => {
      for (const child of PREFIXED) {
        for (let width = 64; width <= 424; width += 2) draw(pairWith(child), 'pass_rate', width, 80)
      }
    })
    expect(added).toEqual({ proto: [], ctor: [] })
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
  })

  it('every measure, the full-screen text scale and short tiles (the value line dropped) leave it untouched too', () => {
    const added = addedKeys(() => {
      for (const colorBy of ['staleness', 'flaky_share'] as const) {
        for (let width = 120; width <= 1200; width += 8) draw(PREFIXED, colorBy, width, 240)
      }
      for (let scale = 1; scale <= 2; scale += 0.1) draw(PREFIXED, 'pass_rate', 900, 360, scale)
      for (let height = 12; height <= 60; height += 2) draw(pairWith(PREFIXED[1]), 'pass_rate', 200, height)
    })
    expect(added).toEqual({ proto: [], ctor: [] })
  })
})
