/**
 * Wave 2.4 review A5 — in full screen a Recharts drawing is scaled up as a
 * whole, laid out for its page-size text, instead of having its text enlarged
 * from CSS inside a layout made for 11 px (which cut names off the svg).
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChartResponsive from './ChartResponsive'
import { ChartFrameContext } from './chartFrameContext'
import { PRESENTATION_SCALE } from './framePlotHeight'
import { chartScaleOf, unscaled } from './tipPlacement'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children, height, width }: { children?: ReactNode; height?: number; width?: string }) => (
    <div data-container-height={height} data-container-width={width}>
      {children}
    </div>
  ),
}))

afterEach(() => vi.restoreAllMocks())

function drawn(fullscreen: boolean | null, height: number) {
  const chart = (
    <ChartResponsive height={height}>
      <svg data-testid="drawing" />
    </ChartResponsive>
  )
  return render(
    fullscreen === null ? chart : <ChartFrameContext.Provider value={{ fullscreen, bodyHeight: 900, portalContainer: null }}>{chart}</ChartFrameContext.Provider>,
  ).container
}

describe('ChartResponsive', () => {
  it('outside full screen IS the ResponsiveContainer: not one element more', () => {
    for (const container of [drawn(null, 260), drawn(false, 260)]) {
      const first = container.firstElementChild as HTMLElement
      expect(first.getAttribute('data-container-height')).toBe('260')
      expect(first.getAttribute('data-container-width')).toBe('100%')
      expect(container.querySelector('[data-chart-presentation]')).toBeNull()
    }
  })

  it('in full screen, lays the drawing out at its height and page text size, and shows it scaled up in the flow', () => {
    expect(PRESENTATION_SCALE).toBeCloseTo(15 / 11)
    const container = drawn(true, 660)
    const outer = container.querySelector('[data-chart-presentation]') as HTMLElement
    // The flow gets the SCALED size, so nothing under the plot is overlapped.
    expect(outer.style.height).toBe('900px')
    const inner = outer.firstElementChild as HTMLElement
    // Laid out 11/15 as wide and at the layout height …
    expect(parseFloat(inner.style.width)).toBeCloseTo((100 * 11) / 15, 6)
    expect(inner.style.height).toBe('660px')
    expect(inner.querySelector('[data-container-height]')?.getAttribute('data-container-height')).toBe('660')
    // … then scaled from its top-left corner: 11 px text reads at 15 px.
    expect(inner.style.transform).toBe(`scale(${PRESENTATION_SCALE})`)
    expect(inner.style.transformOrigin).toBe('0 0')
  })
})

describe('chartScaleOf / unscaled — measured on screen, placed in chart coordinates', () => {
  it('is the ratio of the on-screen width to the layout width, 1 when it cannot be measured', () => {
    const chart = document.createElement('div')
    vi.spyOn(chart, 'offsetWidth', 'get').mockReturnValue(440)
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 600, height: 300 } as DOMRect)
    expect(chartScaleOf(chart)).toBeCloseTo(600 / 440)
    const flat = document.createElement('div')
    expect(chartScaleOf(flat)).toBe(1)
    expect(chartScaleOf(null)).toBe(1)
  })

  it('divides a rectangle measured on screen back down to the drawing\'s own coordinates', () => {
    expect(unscaled({ left: 150, top: 30, width: 300, height: 60 }, 1.5)).toEqual({ left: 100, top: 20, width: 200, height: 40 })
    const same = { left: 1, top: 2, width: 3, height: 4 }
    expect(unscaled(same, 1)).toBe(same)
    expect(unscaled(same, 0)).toBe(same)
  })
})
