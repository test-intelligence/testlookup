/**
 * Wave 2.6 fix round 2 (B0 finding 5): the LAST bucket label of a flat time
 * axis must fit inside the svg.
 *
 * A flat label is centred on its column. Thinned (every 2nd or 3rd day), a
 * label is wider than its column, so the newest one — always drawn — reached
 * past the plot's right edge by half its width minus half a column. The 8 px
 * right margin covered that in Segoe UI and not in DejaVu Sans, where the CI
 * baselines render: "Sep 18" lost its last glyph at 640 and 768 px.
 *
 * jsdom has no layout, so the card width and the text measure are supplied (a
 * font factor of 1.12 is DejaVu Sans against the estimate), and Recharts lays
 * the axis out for real: the test reads the last tick's centre from the svg
 * and adds half its MEASURED width.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import StackedColumnChart, { columnRightMargin, estimateTextWidth } from './StackedColumnChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { longWindowFixture, statusDailyFixture } from './__fixtures__/stackedColumn'

const box = vi.hoisted(() => ({ width: 360, factor: 1.12 }))

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: box.width, height: 280 })
        : null,
  }
})

vi.mock('./chartLayout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./chartLayout')>()
  return { ...actual, useContainerWidth: () => [() => {}, box.width] }
})

const measure = (text: string, size: number) => estimateTextWidth(text, size) * box.factor

vi.mock('./textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./textMeasure')>()
  return { ...actual, useTextMeasure: () => [() => {}, measure] }
})

function lastLabelRightEdge(container: HTMLElement): { right: number; text: string } {
  const ticks = [...container.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')]
    .filter((tick) => (tick.textContent ?? '') !== '')
  const last = ticks[ticks.length - 1] as SVGTextElement
  const text = last.textContent ?? ''
  const x = Number(last.getAttribute('x'))
  expect(last.getAttribute('text-anchor')).toBe('middle')
  return { right: x + measure(text, 11) / 2, text }
}

describe('StackedColumnChart · the newest label fits (B0 finding 5)', () => {
  it.each([320, 360, 420, 480, 560, 640])('a flat, thinned two-week axis at %i px keeps its last label inside the svg', (width) => {
    box.width = width
    box.factor = 1.12
    const { container } = render(
      <ChartAnnouncerProvider>
        <StackedColumnChart model={statusDailyFixture} animate={false} />
      </ChartAnnouncerProvider>,
    )
    const plot = container.querySelector('[data-stacked-column-plot]') as HTMLElement
    if (plot.getAttribute('data-stacked-axis-angle') !== '0') return // slanted: anchored at its end, it cannot overhang right
    const { right, text } = lastLabelRightEdge(container)
    expect(text).toBe(statusDailyFixture.buckets[statusDailyFixture.buckets.length - 1].label)
    expect(right).toBeLessThanOrEqual(width)
  })

  it('reproduces the reported case: flat and thinned at 360 px in DejaVu widths', () => {
    box.width = 360
    box.factor = 1.12
    const { container } = render(
      <ChartAnnouncerProvider>
        <StackedColumnChart model={statusDailyFixture} animate={false} />
      </ChartAnnouncerProvider>,
    )
    const plot = container.querySelector('[data-stacked-column-plot]') as HTMLElement
    expect(plot.getAttribute('data-stacked-axis-angle')).toBe('0')
    expect(Number(plot.getAttribute('data-stacked-axis-interval'))).toBeGreaterThan(0)
    expect(lastLabelRightEdge(container).right).toBeLessThanOrEqual(360)
    expect(Number(plot.getAttribute('data-stacked-plot-right'))).toBeGreaterThan(8)
  })

  it('a slanted axis keeps the default margin (its labels end at their column)', () => {
    box.width = 640
    box.factor = 1
    const { container } = render(
      <ChartAnnouncerProvider>
        <StackedColumnChart model={longWindowFixture} animate={false} />
      </ChartAnnouncerProvider>,
    )
    const plot = container.querySelector('[data-stacked-column-plot]') as HTMLElement
    expect(plot.getAttribute('data-stacked-axis-angle')).not.toBe('0')
    expect(plot.getAttribute('data-stacked-plot-right')).toBeNull()
  })
})

describe('columnRightMargin', () => {
  it('is the base margin while the last label fits in its half-column plus the margin', () => {
    // 14 columns over 560 px (40 px each): a 40 px label overhangs by 0.
    expect(columnRightMargin({ lastLabel: 40, plotWidth: 560, count: 14, base: 8 })).toBe(8)
  })

  it.each([
    [46, 250, 14],
    [100, 100, 2],
    [60, 90, 3],
    [260, 200, 1],
  ])(
    'label %i px, plot %i px, %i columns: grows by exactly what the last label needs in the NARROWER columns it causes',
    (label, plot, count) => {
      const margin = columnRightMargin({ lastLabel: label, plotWidth: plot, count, base: 8 })
      const band = (plot + 8 - margin) / count
      expect(margin).toBeGreaterThan(8)
      // The label's half beyond its column's centre fits in the margin...
      expect(label / 2 - band / 2).toBeLessThanOrEqual(margin - 1)
      // ...and one px less would not.
      const tighter = (plot + 8 - (margin - 1)) / count
      expect(label / 2 - tighter / 2).toBeGreaterThan(margin - 2)
    },
  )

  it('handles a single column', () => {
    expect(columnRightMargin({ lastLabel: 200, plotWidth: 100, count: 1, base: 8 })).toBeGreaterThan(8)
    expect(columnRightMargin({ lastLabel: 10, plotWidth: 100, count: 0, base: 8 })).toBe(8)
  })
})
