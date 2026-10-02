/**
 * Wave 2.6 fix round — two readings of the trend analysis that misled.
 *
 *   R2-15  "No day flagged as unusual" carried the anomaly marker's red
 *          down-triangle: the alarm shape and colour on the all-clear state.
 *          With nothing flagged the chip's icon is neutral; with a day
 *          flagged it is still the marker's own triangle (the key to the
 *          triangles on the plot).
 *   R2-19  In lab the dotted trend line ran on top of the dashed Target 90 %
 *          line and the two could not be told apart: both thin broken lines in
 *          muted tones. The trend line is now drawn at TWICE the target's
 *          weight (and its halo grows with it), so where they coincide the
 *          trend line is the heavy one.
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { analyzeTrend } from '@/lib/trendStats'
import TimeSeriesChart from './TimeSeriesChart'
import { TrendStatsStrip } from './TimeSeriesChartOverlays'
import { ANOMALY_FILL, TREND_OVERLAY_STYLE } from './TimeSeriesChartOverlayStyle'
import { trendAnalysisFixture } from './__fixtures__/wave2Fixtures'

const captured = vi.hoisted(() => ({ lines: [] as Record<string, unknown>[], refs: [] as Record<string, unknown>[] }))

vi.mock('recharts', () => ({
  usePlotArea: () => ({ x: 40, y: 16, width: 400, height: 200 }),
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  Legend: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  Line: (props: Record<string, unknown>) => {
    captured.lines.push(props)
    return null
  },
  Bar: () => null,
  Cell: () => null,
  ReferenceLine: (props: Record<string, unknown>) => {
    captured.refs.push(props)
    return null
  },
  ReferenceDot: () => null,
}))

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

const analysis = analyzeTrend(trendAnalysisFixture.points)
if (!analysis.available) throw new Error('the fixture is expected to support the trend analysis')

describe('R2-15: the flagged-days chip', () => {
  it('with nothing flagged, shows a neutral icon — not the red anomaly triangle', () => {
    const { container } = render(<TrendStatsStrip analysis={{ ...analysis, anomalies: [] }} />)
    const chip = container.querySelector('[data-trend-stat="anomalies"]') as HTMLElement
    expect(chip.textContent).toMatch(/No day flagged as unusual/)
    expect(chip.querySelector('[data-trend-anomaly-swatch]')).toBeNull()
    const icon = chip.querySelector('[data-trend-anomaly-none]')
    expect(icon).not.toBeNull()
    // Neutral: drawn in the text colour it sits in, never the failed status hue.
    for (const el of icon?.querySelectorAll('*') ?? []) {
      for (const attr of ['fill', 'stroke']) expect(el.getAttribute(attr) ?? 'none').not.toBe(ANOMALY_FILL)
    }
  })

  it('with nothing flagged IN VIEW (some in the window), the icon is neutral too: no triangle is on the plot', () => {
    const zoomed = { ...analysis, anomalies: [], zoomed: { anomaliesInWindow: 2 } } as unknown as Parameters<typeof TrendStatsStrip>[0]['analysis']
    const { container } = render(<TrendStatsStrip analysis={zoomed} />)
    const chip = container.querySelector('[data-trend-stat="anomalies"]') as HTMLElement
    expect(chip.querySelector('[data-trend-anomaly-swatch]')).toBeNull()
    expect(chip.querySelector('[data-trend-anomaly-none]')).not.toBeNull()
  })

  it('with a day flagged, keeps the marker\'s own triangle as the key to the plot', () => {
    const { container } = render(<TrendStatsStrip analysis={analysis} />)
    const chip = container.querySelector('[data-trend-stat="anomalies"]') as HTMLElement
    expect(analysis.anomalies.length).toBeGreaterThan(0)
    expect(chip.querySelector('[data-trend-anomaly-swatch]')?.getAttribute('fill')).toBe(ANOMALY_FILL)
    expect(chip.querySelector('[data-trend-anomaly-none]')).toBeNull()
  })
})

describe('R2-19: the trend line is told apart from the target line by weight', () => {
  const draw = () => {
    captured.lines = []
    captured.refs = []
    render(
      <TimeSeriesChart
        model={trendAnalysisFixture}
        trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }}
        rateTarget={{ value: 90, label: 'Target 90%' }}
      />,
    )
    const trend = captured.lines.filter((line) => line.dataKey === 'trendLine')
    const target = captured.refs.find((ref) => ref.className === 'rate-target-line')
    return {
      halo: trend.find((line) => line.className === 'trend-overlay-halo'),
      line: trend.find((line) => line.className === 'trend-overlay-trend-line'),
      target,
    }
  }

  it('draws the trend line at least twice as heavy as the target line', () => {
    const { line, target } = draw()
    expect(target).toBeDefined()
    expect(Number(line?.strokeWidth)).toBeGreaterThanOrEqual(2 * Number(target?.strokeWidth))
    // Still a broken line in its own pattern: never a colour-only or weight-only difference.
    expect(line?.strokeDasharray).toBe(TREND_OVERLAY_STYLE.trendLine.dash)
    expect(line?.strokeDasharray).not.toBe(target?.strokeDasharray)
  })

  it('keeps 2 px of card each side of the heavier line (the halo grows with it)', () => {
    const { line, halo } = draw()
    expect(Number(halo?.strokeWidth)).toBe(Number(line?.strokeWidth) + 4)
  })
})
