/**
 * VIZ-403 — the ECharts path, taken only past `SVG_POINT_LIMIT` points. The
 * rules that must survive the engine swap: gaps stay gaps, the second axis
 * keeps its own title, release markers are still drawn, and the tooltip is DOM
 * built by `domTooltipFormatter` (a string formatter is an HTML sink).
 */
import { describe, expect, it } from 'vitest'
import {
  buildTimeSeriesModel,
  timeSeriesFromTrends,
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
} from '../../timeSeriesModel'
import { findUnsafeFormatter, TOOLTIP_NODE_ATTRIBUTE } from '../../tooltip'
import { readChartTokens } from '../../tokens'
import { buildTimeSeriesOption, timeSeriesTooltipContent, TIME_SERIES_SAMPLING } from './timeSeriesOption'

const tokens = readChartTokens()

const model = buildTimeSeriesModel({
  points: timeSeriesFromTrends([
    { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
    { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
    { date: '2026-03-03', passed: 8, failed: 2, skipped: 0, broken: 0, total: 10, pass_rate: 80 },
  ]),
  releases: [{ id: 'r1', name: '1.4.0', date: '2026-03-02T00:00:00Z' }],
})

interface OptionShape {
  series: {
    id?: string
    type: string
    sampling?: string
    data: (number | null)[]
    markLine?: { data: { xAxis: string; name?: string }[] }
    yAxisIndex?: number
  }[]
  yAxis: { name?: string; min?: number; max?: number }[]
  xAxis: { data: string[] }[]
}

describe('buildTimeSeriesOption', () => {
  const option = buildTimeSeriesOption({ model, tokens, description: 'Pass rate over time' }) as unknown as OptionShape

  it('downsamples the line rather than drawing 5 000 invisible points', () => {
    const line = option.series.find((s) => s.type === 'line')
    expect(line?.sampling).toBe(TIME_SERIES_SAMPLING)
    expect(TIME_SERIES_SAMPLING).toBe('lttb')
  })

  it('keeps a no-run day as a null, so the line breaks instead of dropping to zero', () => {
    const line = option.series.find((s) => s.type === 'line')
    expect(line?.data).toEqual([90, null, 80])
    expect(line?.data).not.toContain(0)
  })

  it('keeps the executions bar on its own axis, and that axis keeps its own title', () => {
    const bar = option.series.find((s) => s.type === 'bar')
    expect(bar?.yAxisIndex).toBe(1)
    expect(option.yAxis[0].name).toBe(RATE_AXIS_TITLE)
    expect(option.yAxis[1].name).toBe(EXECUTIONS_AXIS_TITLE)
  })

  it('holds the rate axis to the model’s domain', () => {
    expect(option.yAxis[0].min).toBe(model.rateAxis.domain[0])
    expect(option.yAxis[0].max).toBe(model.rateAxis.domain[1])
  })

  it('draws a release marker on the release’s bucket, named', () => {
    const marked = option.series.find((s) => s.markLine)
    expect(marked?.markLine?.data).toEqual([{ xAxis: '2026-03-02', name: '1.4.0' }])
  })

  it('refuses nothing: every formatter in the option was built by domTooltipFormatter', () => {
    expect(findUnsafeFormatter(option)).toBeNull()
  })

  it('builds the tooltip as DOM nodes, never a string', () => {
    const tooltip = (option as unknown as { tooltip: { formatter: (params: unknown) => unknown } }).tooltip
    const node = tooltip.formatter([{ dataIndex: 0, axisValue: '2026-03-01' }])
    expect(node).toBeInstanceOf(HTMLElement)
    expect((node as HTMLElement).getAttribute(TOOLTIP_NODE_ATTRIBUTE)).toBe('')
  })

  it('never lets a test or release name reach innerHTML', () => {
    const hostile = buildTimeSeriesModel({
      points: timeSeriesFromTrends([
        { date: '2026-03-01', passed: 1, failed: 0, skipped: 0, broken: 0, total: 1, pass_rate: 100 },
      ]),
      releases: [{ id: 'r1', name: '<img src=x onerror=alert(1)>', date: '2026-03-01T00:00:00Z' }],
    })
    const node = timeSeriesTooltipContent({ model: hostile, index: 0, locale: 'en-US', timeZone: 'UTC' })
    const release = node.rows.find((row) => row.label === 'Release')
    expect(release?.value).toBe('<img src=x onerror=alert(1)>')
  })
})

describe('timeSeriesTooltipContent', () => {
  it('names the UTC day and adds the viewer’s local equivalent', () => {
    const content = timeSeriesTooltipContent({ model, index: 0, locale: 'en-US', timeZone: 'Pacific/Auckland' })
    expect(content.title).toContain('2026-03-01')
    expect(content.title).toContain('UTC')
    const local = content.rows.find((row) => row.label === 'Local')
    expect(local?.value).toContain('Mar 1')
  })

  it('says a gap is not measured, with the reason, instead of showing 0%', () => {
    const content = timeSeriesTooltipContent({ model, index: 1, locale: 'en-US', timeZone: 'UTC' })
    const rate = content.rows.find((row) => row.label === RATE_AXIS_TITLE)
    expect(rate?.value).toBe('—')
    expect(content.rows.some((row) => /no evaluated executions/i.test(row.value))).toBe(true)
  })

  it('names the in-progress runs on a partial day', () => {
    const partial = buildTimeSeriesModel({
      points: timeSeriesFromTrends([
        { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
      ]),
      meta: { partial_day: '2026-03-01', includes_in_progress: 1 } as never,
    })
    const content = timeSeriesTooltipContent({
      model: partial,
      index: 0,
      locale: 'en-US',
      timeZone: 'UTC',
      inProgressRuns: [{ x: '2026-03-01', names: ['nightly-run 412'] }],
    })
    expect(content.rows.some((row) => row.value.includes('nightly-run 412'))).toBe(true)
    expect(content.rows.some((row) => /still filling|in progress/i.test(row.label + row.value))).toBe(true)
  })
})

// ── fix round B ──────────────────────────────────────────────────────────────

describe('fix round B · 7 the SVG → ECharts switch keeps the legend and the hatch', () => {
  const partial = buildTimeSeriesModel({
    points: timeSeriesFromTrends([
      { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
      { date: '2026-03-02', passed: 4, failed: 1, skipped: 0, broken: 0, total: 5, pass_rate: 80 },
    ]),
    meta: { partial_day: '2026-03-02', includes_in_progress: 2 } as never,
  })
  const option = buildTimeSeriesOption({ model: partial, tokens, description: 'Pass rate over time' }) as unknown as {
    legend?: { data?: string[] }
    series: { id?: string; data: unknown[] }[]
  }

  it('names both series in a legend, as the Recharts path does', () => {
    expect(option.legend).toBeDefined()
    expect(option.legend?.data).toEqual([RATE_AXIS_TITLE, EXECUTIONS_AXIS_TITLE])
  })

  it('hatches the still-filling bucket, so the partial day is not colour-only on canvas', () => {
    const bar = option.series.find((s) => s.id === 'executions')
    const cells = bar?.data as { value: number | null; itemStyle?: { decal?: unknown } }[]
    expect(cells.map((cell) => cell.value)).toEqual([10, 5])
    expect(cells[0].itemStyle?.decal).toBeUndefined()
    expect(cells[1].itemStyle?.decal).toBeDefined()
  })

  it('draws the executions bars at full opacity, like the SVG path', () => {
    const bar = option.series.find((s) => s.id === 'executions') as unknown as {
      itemStyle?: { opacity?: number }
    }
    expect(bar.itemStyle?.opacity ?? 1).toBe(1)
  })
})
