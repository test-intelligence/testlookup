/**
 * Wave 2.6 fix round (R2-10) — the release markers' labels on the plot.
 *
 * Real Recharts (only the container is given a size: jsdom lays nothing out)
 * and a stand-in text measure, so the collision pass runs as it does in a
 * browser: two releases on adjacent days of a narrow plot draw their names one
 * row apart, never edge to edge; where nothing collides, the labels are
 * exactly what they always were.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChart from './TimeSeriesChart'
import { trendWithReleasesFixture } from './__fixtures__/wave2Fixtures'
import { MARKER_LABEL_ROW_HEIGHT } from './releaseMarkerLabels'
import type { TimeSeriesModel } from './timeSeriesModel'

const size = vi.hoisted(() => ({ width: 640 }))
const captured = vi.hoisted(() => ({ refs: [] as Record<string, unknown>[], charts: [] as Record<string, unknown>[] }))

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const ReferenceLine = (props: Record<string, unknown>) => {
    captured.refs.push(props)
    return <actual.ReferenceLine {...props} />
  }
  const ComposedChart = (props: Record<string, unknown>) => {
    captured.charts.push(props)
    return <actual.ComposedChart {...props} />
  }
  return {
    ...actual,
    ReferenceLine,
    ComposedChart,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: size.width, height: 260 })
        : null,
  }
})

/** 6 px a character at 10 px: "2026.09" is 42 px wide. */
vi.mock('./textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./textMeasure')>()
  return { ...actual, useTextMeasure: () => [() => {}, (text: string, fontSize: number) => text.length * fontSize * 0.6] }
})

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

const withMarkers = (markers: TimeSeriesModel['markers']): TimeSeriesModel => ({ ...trendWithReleasesFixture, markers })
// Two days apart: on a 320 px chart ~38 px, under the two half-labels and their gap.
const PAIR = withMarkers([
  { x: '2026-03-06', names: ['2026.09'], ids: ['a'] },
  { x: '2026-03-08', names: ['2026.10'], ids: ['b'] },
])

function draw(model: TimeSeriesModel, width: number) {
  size.width = width
  captured.refs = []
  captured.charts = []
  const view = render(<TimeSeriesChart model={model} title="Pass rate trend" animate={false} />)
  // The last render's marker lines (the target line is horizontal and absent here).
  const markers = model.markers.map((marker) => [...captured.refs].reverse().find((ref) => ref.x === marker.x) as Record<string, unknown>)
  const chart = captured.charts[captured.charts.length - 1]
  return { view, markers, margin: chart.margin as { top: number } }
}

const labelOf = (ref: Record<string, unknown>) => ref.label as { value: string; offset?: number } | undefined

describe('R2-10: release labels never run together', () => {
  it('on a narrow plot, raises the second of two adjacent labels one row, and makes room above the plot', () => {
    // 10 days over ~190 px: one day is ~19 px; two days are under "2026.09"'s 42 + the gap.
    const { markers, margin } = draw(PAIR, 320)
    expect(labelOf(markers[0])?.value).toBe('2026.09')
    expect(labelOf(markers[0])?.offset).toBeUndefined()
    expect(labelOf(markers[1])?.value).toBe('2026.10')
    expect(labelOf(markers[1])?.offset).toBe(5 + MARKER_LABEL_ROW_HEIGHT)
    expect(margin.top).toBe(16 + MARKER_LABEL_ROW_HEIGHT)
  })

  it('on a wide plot, draws the labels exactly as before: one row, the usual margin', () => {
    const wide = withMarkers([
      { x: '2026-03-02', names: ['2026.09'], ids: ['a'] },
      { x: '2026-03-09', names: ['2026.10'], ids: ['b'] },
    ])
    const { markers, margin } = draw(wide, 1200)
    for (const marker of markers) expect(labelOf(marker)).toEqual({ value: String(labelOf(marker)?.value), position: 'top', fill: 'var(--chart-axis)', fontSize: 10 })
    expect(margin.top).toBe(16)
  })

  it('drops a label no row can hold, and says the release is named in the tooltip and the table', () => {
    // The third's label takes the first row again — over the second's line, so the second is dropped.
    const three = withMarkers([
      { x: '2026-03-06', names: ['2026.09'], ids: ['a'] },
      { x: '2026-03-08', names: ['2026.10'], ids: ['b'] },
      { x: '2026-03-09', names: ['2026.11'], ids: ['c'] },
    ])
    const { markers, view } = draw(three, 320)
    const drawn = markers.filter((marker) => labelOf(marker) !== undefined)
    expect(drawn.length).toBeLessThan(3)
    // The line is still drawn: only its label is not.
    expect(markers.every((marker) => marker !== undefined)).toBe(true)
    expect(view.container.querySelector('[data-chart-marker-labels-dropped]')?.textContent).toMatch(
      /^1 release label does not fit over the plot; the day's tooltip and the release table name it\.$/,
    )
  })
})
