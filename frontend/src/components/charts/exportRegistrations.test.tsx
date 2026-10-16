/**
 * TRND-11: each ECharts chart of G-D's (the heatmap, the coverage treemap,
 * the test scatter, the status Sankey) registers an export option that is the
 * SAME builder as its on-screen option but built on the tokens Export GIVES
 * it — the light theme's for "Light background" — never on the page's own,
 * and never animated (R-9). The engine is mocked at the registry (jsdom has
 * no canvas); the page's computed style answers `screen:<token>`, the given
 * tokens are `given-<token>`, so where every colour came from is traceable
 * (no colour literal in this directory: check:theme).
 */
import type { ReactElement } from 'react'
import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import type { PointsChart, TreeNodeStats } from '@/lib/viz/contracts'
import CoverageTreemap from './CoverageTreemap'
import HeatmapChart from './HeatmapChart'
import StatusSankey from './StatusSankey'
import TestScatter from './TestScatter'
import { TOP_LEVEL, type CoverageChild } from './coverageMap.model'
import type { NumericMatrix } from './engines/echarts/heatmapOption'
import { exportOptionOf } from './engines/echarts/exportRegistry'
import { buildStatusSankey } from './statusSankeyModel'
import { resetChartTokenCache, type ChartTokens } from './tokens'

const engine = vi.hoisted(() => {
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    isDisposed: vi.fn(() => false),
    on: vi.fn(),
    off: vi.fn(),
  }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const given = (name: string) => `given-${name}`
const GIVEN: ChartTokens = {
  theme: 'lab',
  series: Array.from({ length: 8 }, (_, i) => given(`series-${i + 1}`)),
  seq: Array.from({ length: 7 }, (_, i) => given(`seq-${i + 1}`)),
  div: Array.from({ length: 7 }, (_, i) => given(`div-${i + 1}`)),
  status: { passed: given('passed'), failed: given('failed'), broken: given('broken'), skipped: given('skipped'), unknown: given('unknown') },
  flaky: given('flaky'),
  grid: given('grid'),
  axis: given('axis'),
  card: given('card'),
  border: given('border'),
  text: given('text'),
  textMuted: given('text-muted'),
}

const stats = (over: Partial<TreeNodeStats> = {}): TreeNodeStats => ({
  test_count: 4, executions: 12, pass_rate: 95, flaky_count: 0, flaky_share: 0,
  last_executed_at: '2026-09-30T00:00:00Z', staleness_days: 1, recency: 'seen', ...over,
})
const SUITES: CoverageChild[] = [
  { node: { id: 's:payments', parent_id: 'all', label: 'Payments', value: 4, measure: 95, stats: stats() }, kind: 'suite', key: 'payments' },
  { node: { id: 's:idle', parent_id: 'all', label: 'idle', value: 2, measure: null, stats: stats({ executions: 0, pass_rate: null, recency: 'never', last_executed_at: null, staleness_days: null }) }, kind: 'suite', key: 'idle' },
]
const MATRIX: NumericMatrix = {
  kind: 'matrix', value_type: 'count', x_labels: ['a', 'b'], y_labels: ['s'],
  cells: [{ x: 0, y: 0, value: 1, n: 1 }, { x: 1, y: 0, value: 2, n: 2 }],
}
const POINTS: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [{ id: 'fp-a', label: 'a', x: 12, y: 0, size: 10, n: 9 }, { id: 'fp-b', label: 'b', x: 900, y: 20, size: 5, n: 5 }],
  medians: { x: 100, y: 0 },
  excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 },
}
const SANKEY = buildStatusSankey([
  { before: 'passed', after: 'failed', count: 2 },
  { before: 'passed', after: 'passed', count: 8 },
])

let computed: MockInstance
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', NoopResizeObserver)
  resetChartTokenCache()
  computed = vi.spyOn(window, 'getComputedStyle').mockImplementation(
    () => ({ getPropertyValue: (name: string) => `screen:${name}` }) as unknown as CSSStyleDeclaration,
  )
  engine.load.mockReset().mockResolvedValue({ init: engine.init })
  engine.init.mockClear()
})
afterEach(() => {
  computed.mockRestore()
  vi.unstubAllGlobals()
  resetChartTokenCache()
})

const CHARTS: [string, () => ReactElement][] = [
  ['HeatmapChart', () => <HeatmapChart data={MATRIX} description="Two cells." />],
  ['CoverageTreemap', () => <CoverageTreemap items={SUITES} level={TOP_LEVEL} colorBy="pass_rate" description="d" />],
  ['TestScatter', () => <TestScatter data={POINTS} description="Two tests." />],
  ['StatusSankey', () => (SANKEY ? <StatusSankey model={SANKEY} /> : <div />)],
]

describe('each ECharts chart registers its export option on the GIVEN tokens', () => {
  it.each(CHARTS)('%s', async (_name, ui) => {
    const { container } = render(ui())
    const el = () => container.querySelector('[data-chart-engine="echarts"], [data-status-sankey]') as HTMLElement
    await waitFor(() => expect(exportOptionOf(el())).not.toBeNull())
    const screenOption = JSON.stringify(engine.instance.setOption.mock.calls[engine.instance.setOption.mock.calls.length - 1]?.[0])
    expect(screenOption).toContain('screen:')
    const exported = exportOptionOf(el())?.build(GIVEN) as Record<string, unknown>
    const text = JSON.stringify(exported)
    expect(text).toContain('given-')
    expect(text).not.toContain('screen:')
    // Never animated in a file.
    expect(exported.animation === undefined || exported.animation === false).toBe(true)
  })
})
