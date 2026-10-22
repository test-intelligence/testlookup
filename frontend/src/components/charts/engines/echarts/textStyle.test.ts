/**
 * W29b UIX-05 — ONE chart text style across the engines.
 *
 * Every ECharts option the kit builds draws its text in the PAGE's font
 * (`--font-sans`, resolved into `ChartTokens.fontSans`) at the kit's sizes
 * (`CHART_FONT`: 11 for ticks, axis names and labels, 12 for a legend), and its
 * gridlines solid. ECharts' own defaults are 12 px "Microsoft YaHei" on Windows
 * and `sans-serif` elsewhere, and an axis label or a legend that sets no size
 * keeps ECharts' 12 px whatever the option's root says — so this BUILDS every
 * option builder's option and walks the whole object:
 *
 *   - the root `textStyle` (ECharts' global default) names the fixture family;
 *   - EVERY object that holds a `fontSize` also holds that family, so a label
 *     that sets its own size cannot fall back to the canvas default font;
 *   - at text scale 1 every `fontSize` is 11, except a legend's text (12);
 *   - every `splitLine.lineStyle.type` is `'solid'` or absent;
 *   - at the presentation scale (16/11) the heatmap, scatter and treemap text
 *     reaches 16 px (a legend 17): the existing scaling, kept.
 *
 * The tooltips are DOM (`domTooltipFormatter`), sized by `TIP_STYLE`, and are
 * not part of this walk's sizes (their option `textStyle` sets a colour only).
 */
import { describe, expect, it } from 'vitest'
import type { PointsChart, PointsChartPoint, TreeNodeStats } from '@/lib/viz/contracts'
import type { CoverageChild } from '../../coverageMap.model'
import { sankeyOption } from '../../StatusSankey'
import { buildStatusSankey } from '../../statusSankeyModel'
import { buildTimeSeriesModel, timeSeriesFromTrends } from '../../timeSeriesModel'
import { CHART_FONT, type ChartTokens } from '../../tokens'
import { trendWithReleasesFixture } from '../../__fixtures__/wave2Fixtures'
import { buildHeatmapOption, type NumericMatrix, type StatusMatrix } from './heatmapOption'
import { buildScatterOption } from './scatterOption'
import { buildTimeSeriesOption } from './timeSeriesOption'
import { buildTreemapOption } from './treemapOption'

const FAMILY = 'TestSans, sans-serif'

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
  measure: 'measure',
  fontSans: FAMILY,
}

// ── The five option builders' inputs ─────────────────────────────────────────

/** Release markers (markLine labels) AND timeline notes (a band's and a line's labels). */
const DAYS = ['2026-09-10', '2026-09-11', '2026-09-12', '2026-09-13', '2026-09-14', '2026-09-15']
const notedTrend = buildTimeSeriesModel({
  points: timeSeriesFromTrends(
    DAYS.map((date, i) => ({ date, passed: 8 + (i % 2), failed: 2 - (i % 2), skipped: 0, broken: 0, total: 10, pass_rate: 80 + (i % 2) * 10 })),
  ),
  notes: [
    { id: 'band', from: '2026-09-11', to: '2026-09-13', label: 'release freeze', author: 'Ada' },
    { id: 'line', from: '2026-09-14', to: '2026-09-14', label: 'hotfix', author: null },
  ],
})

/** A rate matrix with a hatched (no-data) cell: the colour bar, its words, the "No data" legend and a column title. */
const rateMatrix: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['2026-09-01', '2026-09-02', '2026-09-03'],
  y_labels: ['auth', 'billing'],
  cells: [
    { x: 0, y: 0, value: 0.98, n: 40 },
    { x: 1, y: 0, value: 0.6, n: 20 },
    { x: 2, y: 0, value: null, n: 0 },
    { x: 0, y: 1, value: 0.9, n: 12 },
    { x: 1, y: 1, value: 0.7, n: 10 },
    { x: 2, y: 1, value: 0.5, n: 8 },
  ],
}
const statusMatrix: StatusMatrix = {
  kind: 'matrix',
  value_type: 'status',
  x_labels: ['run 1', 'run 2'],
  y_labels: ['checkout'],
  cells: [
    { x: 0, y: 0, value: 'failed', n: 1 },
    { x: 1, y: 0, value: null, n: 0 },
  ],
}

const point = (id: string, x: number, y: number, size = 10): PointsChartPoint => ({ id, label: id, x, y, size, n: size })
const scatter: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [point('a', 3, 50, 100), point('b', 4200, 0, 25), point('c', 40, 10, 1), point('d', 900, 80, 4)],
  medians: { x: 470, y: 30 },
  excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 },
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
const child = (id: string, label: string, s: TreeNodeStats): CoverageChild => ({
  node: { id, parent_id: 'all', label, value: s.test_count, measure: s.pass_rate, stats: s },
  kind: 'suite',
  key: id.slice(2),
})
const treemapChildren: CoverageChild[] = [
  child('s:payments', 'Payments', stats({ test_count: 9, pass_rate: 20 })),
  child('s:auth', 'Auth', stats({ test_count: 5 })),
]

const sankeyModel = buildStatusSankey([
  { before: 'passed', after: 'passed', count: 80 },
  { before: 'passed', after: 'failed', count: 5 },
  { before: 'failed', after: 'passed', count: 3 },
])

/** Every builder's option at a text scale (the time series and the sankey have no scale of their own). */
function options(textScale: number): Record<string, object> {
  if (!sankeyModel) throw new Error('the sankey fixture must build a model')
  return {
    'time series (releases)': buildTimeSeriesOption({ model: trendWithReleasesFixture, tokens, description: 'd' }),
    'time series (notes)': buildTimeSeriesOption({ model: notedTrend, tokens, description: 'd' }),
    'heatmap (rate, hatched, titled)': buildHeatmapOption({ data: rateMatrix, tokens, description: 'd', textScale, columnAxisName: 'Day' }),
    'heatmap (status)': buildHeatmapOption({ data: statusMatrix, tokens, description: 'd', textScale }),
    scatter: buildScatterOption({ data: scatter, tokens, textScale }),
    treemap: buildTreemapOption({ children: treemapChildren, colorBy: 'pass_rate', tokens, textScale }),
    sankey: sankeyOption(sankeyModel, tokens),
  }
}

// ── The walk ─────────────────────────────────────────────────────────────────

interface Sized {
  path: string
  fontSize: unknown
  fontFamily: unknown
}

/**
 * Every object in `value` that sets a `fontSize`, with its path — an object a
 * builder reuses (one axis-label style on three axes) is reported at each place.
 * Functions (formatters, positions) are not walked; an option holds no cycle.
 */
function sizedObjects(value: unknown, path = '', out: Sized[] = []): Sized[] {
  if (value === null || typeof value !== 'object') return out
  if (Array.isArray(value)) {
    value.forEach((item, i) => sizedObjects(item, `${path}[${i}]`, out))
    return out
  }
  const record = value as Record<string, unknown>
  if (record.fontSize !== undefined) out.push({ path: path || '(root)', fontSize: record.fontSize, fontFamily: record.fontFamily })
  for (const [key, child] of Object.entries(record)) sizedObjects(child, path ? `${path}.${key}` : key, out)
  return out
}

/** Every `splitLine` in `value`, with its path. */
function splitLines(value: unknown, path = '', out: { path: string; type: unknown }[] = []): { path: string; type: unknown }[] {
  if (value === null || typeof value !== 'object') return out
  if (Array.isArray(value)) {
    value.forEach((item, i) => splitLines(item, `${path}[${i}]`, out))
    return out
  }
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const at = path ? `${path}.${key}` : key
    if (key === 'splitLine' && child && typeof child === 'object') {
      out.push({ path: at, type: (child as { lineStyle?: { type?: unknown } }).lineStyle?.type })
    }
    splitLines(child, at, out)
  }
  return out
}

/** A legend's own text: the one size that is not the tick size. */
const isLegendText = (path: string) => /(^|\.)legend(\.|$)/.test(path)

describe('one ECharts text style (UIX-05)', () => {
  const plain = options(1)

  it.each(Object.keys(plain))('%s: the option root sets the page font at the kit tick size', (name) => {
    const root = (plain[name] as { textStyle?: Record<string, unknown> }).textStyle
    expect(root).toEqual({ fontFamily: FAMILY, fontSize: CHART_FONT.tick, color: 'axis' })
  })

  it.each(Object.keys(plain))('%s: every object that sets a font size names the page font too', (name) => {
    const sized = sizedObjects(plain[name])
    expect(sized.length).toBeGreaterThan(1)
    for (const entry of sized) expect(entry.fontFamily, entry.path).toBe(FAMILY)
  })

  it.each(Object.keys(plain))('%s: at text scale 1 every size is 11, a legend’s text 12', (name) => {
    for (const entry of sizedObjects(plain[name])) {
      expect(entry.fontSize, entry.path).toBe(isLegendText(entry.path) ? CHART_FONT.legend : CHART_FONT.tick)
    }
  })

  it.each(Object.keys(plain))('%s: every gridline is solid', (name) => {
    for (const line of splitLines(plain[name])) expect([undefined, 'solid'], line.path).toContain(line.type)
  })

  it('the walk reaches the labels that matter: axis names and ticks, marker and note labels, the ramp, the legends', () => {
    const paths = Object.entries(plain).flatMap(([name, option]) => sizedObjects(option).map((entry) => `${name}: ${entry.path}`))
    for (const expected of [
      /^time series \(releases\): legend\.textStyle$/,
      /^time series \(releases\): xAxis\[0\]\.axisLabel$/,
      /^time series \(releases\): yAxis\[1\]\.nameTextStyle$/,
      /^time series \(releases\): series\[1\]\.markLine\.label$/,
      /^time series \(notes\): series\[1\]\.markLine\.data\[\d+\]\.label$/,
      /^time series \(notes\): series\[1\]\.markArea\.label$/,
      /^heatmap \(rate, hatched, titled\): xAxis\.nameTextStyle$/,
      /^heatmap \(rate, hatched, titled\): yAxis\.axisLabel$/,
      /^heatmap \(rate, hatched, titled\): visualMap\[0\]\.textStyle$/,
      /^heatmap \(rate, hatched, titled\): legend\.textStyle$/,
      /^heatmap \(status\): xAxis\.axisLabel$/,
      /^scatter: xAxis\.nameTextStyle$/,
      /^scatter: yAxis\.axisLabel$/,
      /^treemap: series\[0\]\.label$/,
      /^sankey: series\[0\]\.label$/,
    ]) {
      expect(paths.some((path) => expected.test(path)), String(expected)).toBe(true)
    }
  })

  it('presentation mode (16/11): the heatmap, scatter and treemap text reaches 16 px, a legend 17', () => {
    const scaled = options(16 / 11)
    for (const name of ['heatmap (rate, hatched, titled)', 'heatmap (status)', 'scatter', 'treemap']) {
      const sized = sizedObjects(scaled[name])
      expect(sized.length).toBeGreaterThan(1)
      for (const entry of sized) {
        expect(entry.fontFamily, `${name}: ${entry.path}`).toBe(FAMILY)
        expect(entry.fontSize, `${name}: ${entry.path}`).toBe(isLegendText(entry.path) ? 17 : 16)
      }
    }
  })
})
