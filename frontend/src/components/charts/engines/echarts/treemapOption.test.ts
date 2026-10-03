import { describe, expect, it } from 'vitest'
import type { TreeNodeStats } from '@/lib/viz/contracts'
import { decalOf, type ChartTokens } from '../../tokens'
import { assertSafeChartOption, TOOLTIP_NODE_ATTRIBUTE } from '../../tooltip'
import type { CoverageChild } from '../../coverageMap.model'
import {
  buildTreemapOption,
  nodeFill,
  TREEMAP_BORDER_WIDTH,
  TREEMAP_FONT_SIZE,
  treemapFontSize,
  treemapNodeMark,
  treemapTarget,
} from './treemapOption'

const HOSTILE = '<img src=x onerror="window.__xss=1">'
const WJ = String.fromCharCode(0x2060)

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

const stats = (over: Partial<TreeNodeStats> = {}): TreeNodeStats => ({
  test_count: 4,
  executions: 12,
  pass_rate: 95,
  flaky_count: 0,
  flaky_share: 0,
  last_executed_at: '2026-09-30T00:00:00Z',
  staleness_days: 1,
  recency: 'seen',
  ...over,
})

const child = (id: string, label: string, s: TreeNodeStats | undefined, kind: CoverageChild['kind'] = 'suite'): CoverageChild => ({
  node: { id, parent_id: 'all', label, value: s?.test_count ?? 3, measure: s?.pass_rate ?? null, ...(s ? { stats: s } : {}) },
  kind,
  key: kind === 'other' ? null : id.slice(2),
})

const CHILDREN: CoverageChild[] = [
  child('s:payments', 'Payments', stats({ test_count: 9, pass_rate: 20 })),
  child('s:auth', HOSTILE, stats({ test_count: 5 })),
  child('s:idle', 'tests/idle/test_idle.py', stats({ executions: 0, pass_rate: null, recency: 'unknown', last_executed_at: null, staleness_days: null }), 'class'),
  child('s:never', 'never', stats({ executions: 0, pass_rate: null, recency: 'never', last_executed_at: null, staleness_days: null })),
  child('other:all', 'Other (3)', stats({ pass_rate: null, test_count: 2 }), 'other'),
]

type Loose = Record<string, unknown>
const build = (over: Partial<Parameters<typeof buildTreemapOption>[0]> = {}) =>
  buildTreemapOption({ children: CHILDREN, colorBy: 'pass_rate', tokens, ...over }) as unknown as Loose
const seriesOf = (option: Loose) => (option.series as Loose[])[0]
const dataOf = (option: Loose) => seriesOf(option).data as Loose[]

describe('treemapTarget (spike S1)', () => {
  it('addresses child k as dataIndex k + 1: the virtual root is 0', () => {
    expect(treemapTarget(0)).toEqual({ seriesIndex: 0, dataIndex: 1 })
    expect(treemapTarget(4)).toEqual({ seriesIndex: 0, dataIndex: 5 })
  })
})

describe('buildTreemapOption', () => {
  it('is one flat level, in the server order, with no ECharts navigation', () => {
    const series = seriesOf(build())
    expect(series).toMatchObject({
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      sort: false,
      visibleMin: 0,
      drillDownIcon: '',
    })
    expect(dataOf(build()).map((d) => d.value)).toEqual([9, 5, 4, 4, 2])
    // The root draws no border, so the children reach the edges.
    expect((series.levels as Loose[])[0]).toMatchObject({ itemStyle: { borderWidth: 0 } })
  })

  it('draws a name that STARTS WITH an Object member behind a word joiner, never as itself (R1B-1, canvasText.ts)', () => {
    const option = build({
      children: [
        child('s:__proto__', '__proto__', stats()),
        child('s:constructor', 'constructor_payment_tests', stats()),
        child('s:p', '__proto__bomb_label_long', stats({ pass_rate: null, executions: 0, recency: 'never', staleness_days: null })),
        child('s:ok', 'checkout', stats()),
      ],
    })
    expect(dataOf(option).map((d) => d.name)).toEqual([
      `${WJ}__proto__\n95.0%`,
      `${WJ}constructor_payment_tests\n95.0%`,
      `${WJ}__proto__bomb_label_long`,
      'checkout\n95.0%',
    ])
  })

  it('labels with the DATA NAMES (shortened), cut by ECharts, and has no formatter but the DOM tooltip', () => {
    const option = build()
    expect(dataOf(option).map((d) => d.name)).toEqual(['Payments\n20.0%', `${HOSTILE}\n95.0%`, 'test_idle.py', 'never', 'Other (3)'])
    const label = seriesOf(option).label as Loose
    expect(label).toMatchObject({ show: true, overflow: 'truncate', color: 'text', textBorderColor: 'card' })
    expect(JSON.stringify(option, (key, value: unknown) => (typeof value === 'function' ? undefined : value))).not.toMatch(
      /formatter/i,
    )
    expect(() => assertSafeChartOption(option)).not.toThrow()
    expect(option.aria).toEqual({ enabled: false })
  })

  it('draws the keyboard ring as the emphasis border COLOUR at the base border width (spike S1)', () => {
    const series = seriesOf(build())
    expect(series.itemStyle).toEqual({ borderColor: 'card', borderWidth: TREEMAP_BORDER_WIDTH, gapWidth: 0 })
    expect((series.emphasis as Loose).itemStyle).toEqual({ borderColor: 'text' })
    expect(TREEMAP_BORDER_WIDTH).toBeGreaterThanOrEqual(2)
    // At once: a clock-driven 300 ms blend never finishes where Date is pinned (the e2e and visual harnesses).
    expect(series.stateAnimation).toEqual({ duration: 0 })
  })

  it('colours a value by its bin, a gap by its pattern, and Other in the "Other" grey', () => {
    const fills = dataOf(build()).map((d) => d.itemStyle as Loose)
    expect(fills[0]).toEqual({ color: 'seq-7' }) // 20%: the salient end
    expect(fills[1]).toEqual({ color: 'seq-1' }) // 95%
    expect(fills[2]).toEqual({ color: 'card', decal: decalOf('dots', 'axis') }) // last run unknown
    expect(fills[3]).toEqual({ color: 'card', decal: decalOf('crosshatch', 'axis') }) // never run
    expect(fills[4]).toEqual({ color: 'series-8' })
  })

  it('puts the measure under the name, so colour is never the only channel (F-03); a gap is its pattern', () => {
    // Built into the data (no formatter): ECharts drops a line the tile has no height for.
    expect(dataOf(build({ colorBy: 'staleness' })).map((d) => d.name)).toEqual([
      'Payments\n1 day',
      `${HOSTILE}\n1 day`,
      'test_idle.py',
      'never',
      // The combined node HAS a staleness.
      'Other (3)\n1 day',
    ])
    const flaky = build({
      colorBy: 'flaky_share',
      children: [child('s:a', 'a', stats({ flaky_share: 0.125 })), child('s:b', 'b', stats({ flaky_share: 0, staleness_days: 40 }))],
    })
    expect(dataOf(flaky).map((d) => d.name)).toEqual(['a\n12.5% flaky', 'b\n0.0% flaky'])
    expect(dataOf(build({ colorBy: 'staleness', children: [child('s:a', 'a', stats({ staleness_days: 40 }))] }))[0].name).toBe('a\n40 days')
    // ECharts drops the lines a tile has no room for: never wrapped into the name.
    expect((seriesOf(build()).label as Loose).overflow).toBe('truncate')
  })

  it('re-colours by staleness and flaky share without moving a node', () => {
    const staleness = dataOf(build({ colorBy: 'staleness' }))
    expect(staleness.map((d) => d.value)).toEqual(dataOf(build()).map((d) => d.value))
    expect((staleness[0].itemStyle as Loose).color).toBe('seq-1')
    // Other HAS a staleness: coloured, not grey.
    expect((staleness[4].itemStyle as Loose).color).toBe('seq-1')
    const flaky = dataOf(build({ colorBy: 'flaky_share' }))
    expect((flaky[2].itemStyle as Loose).decal).toEqual(decalOf('dots', 'axis'))
  })

  it('names a gap distinctly from a value in its fill', () => {
    expect(nodeFill(CHILDREN[0], 'pass_rate', tokens)).toEqual({ color: 'seq-7', decal: null, gap: null, bin: 4 })
    expect(nodeFill(CHILDREN[2], 'pass_rate', tokens).gap).toBe('unknown')
    expect(nodeFill(CHILDREN[3], 'pass_rate', tokens).gap).toBe('never')
    const notRun = child('s:n', 'n', stats({ executions: 0, pass_rate: null, staleness_days: 40 }))
    expect(nodeFill(notRun, 'pass_rate', tokens)).toMatchObject({ gap: 'not_run', decal: decalOf('diagonal', 'axis') })
    expect(nodeFill(child('s:b', 'b', undefined), 'pass_rate', tokens).gap).toBe('unknown')
  })

  it('never draws a negative size', () => {
    const bad = { ...CHILDREN[0], node: { ...CHILDREN[0].node, value: -3 } }
    expect(dataOf(build({ children: [bad] }))[0].value).toBe(0)
  })

  it('scales the canvas text in full screen, never shrinks it', () => {
    expect(treemapFontSize(1)).toBe(TREEMAP_FONT_SIZE)
    expect(treemapFontSize(0.5)).toBe(TREEMAP_FONT_SIZE)
    expect(treemapFontSize(Number.NaN)).toBe(TREEMAP_FONT_SIZE)
    expect(treemapFontSize(1.5)).toBe(18)
    expect((seriesOf(build({ textScale: 1.5 })).label as Loose).fontSize).toBe(18)
  })

  it('animates only when asked', () => {
    expect(build().animation).toBe(false)
    expect(build({ animate: true }).animation).toBe(true)
  })
})

describe('the tooltip', () => {
  const formatter = () => (build().tooltip as Loose).formatter as (params: unknown) => HTMLElement

  it('is the node of dataIndex k + 1, built as DOM text (a hostile label stays text)', () => {
    const el = formatter()({ dataIndex: 2 })
    expect(el.getAttribute(TOOLTIP_NODE_ATTRIBUTE)).not.toBeNull()
    expect(el.textContent).toContain(HOSTILE)
    expect(el.querySelector('img')).toBeNull()
    expect(formatter()([{ dataIndex: 1 }]).textContent).toContain('Payments')
  })

  it('is empty for the virtual root and for params it cannot read', () => {
    for (const params of [{ dataIndex: 0 }, { dataIndex: 99 }, { dataIndex: '1' }, { dataIndex: 1.5 }, null, undefined]) {
      expect(formatter()(params).textContent).toBe('')
    }
  })

  it('sits beside the node rectangle ECharts hands over, else at the pointer', () => {
    expect(treemapNodeMark([5, 6], { x: 1, y: 2, width: 3, height: 4 }, { width: 100, height: 100 })).toEqual({
      left: 1,
      top: 2,
      width: 3,
      height: 4,
    })
    expect(treemapNodeMark([5, 6], undefined, { width: 100, height: 100 })).toEqual({ left: 5, top: 6, width: 0, height: 0 })
    expect(treemapNodeMark([], undefined, { width: 100, height: 100 })).toEqual({ left: 0, top: 0, width: 0, height: 0 })
  })
})
