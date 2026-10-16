/**
 * TRND-11 (the TimeSeriesChart line): past `SVG_POINT_LIMIT` points the
 * chart is drawn by ECharts, and it registers its export option through
 * `useEChart`'s 4th argument — the SAME `buildTimeSeriesOption` call as the
 * screen, on the tokens Export hands it, never animated. The SVG renderer
 * registers nothing (it is exported as SVG already).
 */
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ChartTokens } from './tokens'
import { readChartTokens } from './tokens'
import { buildTimeSeriesOption } from './engines/echarts/timeSeriesOption'
import { SVG_POINT_LIMIT, buildTimeSeriesModel, timeSeriesFromTrends, utcDayRange } from './timeSeriesModel'

const calls = vi.hoisted(() => [] as unknown[][])
vi.mock('./engines/useEChart', () => ({
  useEChart: (...args: unknown[]) => {
    calls.push(args)
    return { containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }
  },
}))

import TimeSeriesChart from './TimeSeriesChart'

const LONG_DAYS = utcDayRange('2025-01-01', '2026-03-01')
const longModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends(LONG_DAYS.map((date) => ({ date, passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }))),
  notes: [{ id: 'n1', from: '2025-06-01', to: '2025-06-03', label: 'freeze', author: 'Ada' }],
})

describe('TimeSeriesChart registers its ECharts export option (TRND-11)', () => {
  it('passes a builder that redraws the on-screen option on the given tokens, not animated', () => {
    expect(longModel.points.length).toBeGreaterThan(SVG_POINT_LIMIT)
    calls.length = 0
    render(<TimeSeriesChart model={longModel} description="Pass rate and execution volume over time" />)
    const last = calls[calls.length - 1]
    expect(last[0]).toBe('timeSeries')
    const build = last[3] as (tokens: ChartTokens) => object
    expect(typeof build).toBe('function')
    // Marker values (not colours): the export must draw with the tokens it is GIVEN, not the screen's.
    const light = { ...readChartTokens(), theme: 'lab', card: 'export-card-token', text: 'export-text-token' }
    const exported = build(light) as { animation: boolean; tooltip: { backgroundColor: string; textStyle: { color: string } } }
    expect(exported.animation).toBe(false)
    expect(exported.tooltip.backgroundColor).toBe('export-card-token')
    expect(exported.tooltip.textStyle.color).toBe('export-text-token')
    // The same option the screen draws, but on the export's tokens.
    const expected = buildTimeSeriesOption({ model: longModel, tokens: light, description: 'Pass rate and execution volume over time', animate: false })
    expect(JSON.stringify(exported)).toBe(JSON.stringify(expected))
  })

  it('the SVG renderer registers nothing', () => {
    calls.length = 0
    const short = buildTimeSeriesModel({
      points: timeSeriesFromTrends([{ date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }]),
    })
    render(<TimeSeriesChart model={short} />)
    expect(calls).toHaveLength(0)
  })
})
