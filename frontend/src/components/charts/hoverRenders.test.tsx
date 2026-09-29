/**
 * VIZ-601 "Performance": hovering must not re-render the CHART.
 *
 * A real Recharts `TimeSeriesChart` (sized, so Recharts lays it out in jsdom)
 * is hovered across every day, 60 pointer moves in all. The tooltip really
 * opens — asserted, so the moves are known to have reached Recharts — yet the
 * plot component (the one that draws the Recharts tree) renders no more times than before the
 * pointer arrived: the tooltip's state lives in Recharts and in `PinnedTip`,
 * and `useChartCursor`'s pointer handler sets no state unless a dismissal is
 * lifted.
 */
import { act, fireEvent, render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TimeSeriesChart from './TimeSeriesChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { buildTimeSeriesModel, timeSeriesFromTrends } from './timeSeriesModel'

const WIDTH = 640
const HEIGHT = 260

const body = vi.hoisted(() => ({ renders: 0 }))

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: WIDTH, height: HEIGHT })
        : null,
  }
})

// The PLOT body (the component that draws the whole Recharts tree) calls
// `useChartCursor` exactly once per render: count it there, not in an outer
// wrapper that a re-rendering child would never show.
vi.mock('./ChartCursor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./ChartCursor')>()
  return {
    ...actual,
    useChartCursor: (options: Parameters<typeof actual.useChartCursor>[0]) => {
      body.renders += 1
      return actual.useChartCursor(options)
    },
  }
})

const DAYS = Array.from({ length: 12 }, (_, i) => ({
  date: `2026-03-${String(i + 1).padStart(2, '0')}`,
  passed: 8 + (i % 3),
  failed: 2,
  skipped: 0,
  broken: 0,
  total: 10 + (i % 3),
  pass_rate: 80,
}))

afterEach(() => vi.restoreAllMocks())

describe('hovering a chart', () => {
  it('opens the tooltip across every day without re-rendering the chart body', async () => {
    // jsdom has no layout: give the chart WRAPPER its size, so Recharts can map
    // the pointer to a day (every other box stays 0×0 — a legend measured at
    // the chart's full height would leave the plot no room at all).
    const sized = (el: HTMLElement) => el.classList.contains('recharts-wrapper')
    const box = { x: 0, y: 0, left: 0, top: 0, right: WIDTH, bottom: HEIGHT, width: WIDTH, height: HEIGHT, toJSON: () => ({}) }
    const none = { ...box, right: 0, bottom: 0, width: 0, height: 0 }
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return (sized(this) ? box : none) as DOMRect
    })
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return sized(this) ? WIDTH : 0
    })
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return sized(this) ? HEIGHT : 0
    })

    const model = buildTimeSeriesModel({ points: timeSeriesFromTrends(DAYS) })
    const { container } = render(
      <ChartAnnouncerProvider>
        <TimeSeriesChart model={model} title="Pass rate" timeZone="UTC" locale="en-US" animate={false} />
      </ChartAnnouncerProvider>,
    )
    const wrapper = container.querySelector('.recharts-wrapper') as HTMLElement
    const surface = container.querySelector('[data-time-series-plot]') as HTMLElement
    expect(wrapper).not.toBeNull()
    const settled = body.renders

    // Recharts 3 coalesces pointer moves to one per animation frame (its
    // `throttleDelay: 'raf'`); let a frame's worth of time pass per move.
    for (let i = 0; i < 60; i++) {
      const x = 60 + (i * (WIDTH - 120)) / 59
      await act(async () => {
        fireEvent.pointerMove(surface, { clientX: x, clientY: 120 })
        fireEvent.mouseMove(wrapper, { clientX: x, clientY: 120 })
        await new Promise((resolve) => setTimeout(resolve, 20))
      })
    }

    // The moves reached Recharts: the pinned tooltip is open on a day…
    const tip = container.querySelector('[data-chart-tooltip]')
    expect(tip).not.toBeNull()
    expect(tip?.querySelector('.chart-tooltip-title')?.textContent).toMatch(/^2026-03-\d\d \(UTC\)$/)
    // …placed beside its day, from the real DOM measurement path, inside the chart.
    expect(['left', 'right']).toContain(tip?.getAttribute('data-tip-side'))
    expect(tip?.getAttribute('data-tip-fits')).toBe('true')
    const left = parseFloat((tip as HTMLElement).style.left)
    expect(left).toBeGreaterThanOrEqual(0)
    expect(left).toBeLessThanOrEqual(WIDTH)
    // …and the chart itself did not render again for any of them.
    expect(body.renders - settled).toBe(0)
  })
})
