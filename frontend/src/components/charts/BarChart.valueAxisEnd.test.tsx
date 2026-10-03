/**
 * Wave 3 F-20: the value axis's LAST tick ("1,000") is drawn centred on the
 * plot's right edge, and the stacked plot keeps only 12 px right of it on a
 * compact card: at 375 px the tick was clipped ("1,00") on Windows and on
 * Linux/DejaVu. `fitValueAxisEnd` (the drill ladder passes it) keeps half the
 * widest MEASURED tick right of the plot.
 *
 * OFF, BYTE-IDENTICAL: no other caller passes it, and every flag-off page draws
 * a BarChart. The snapshot below was written from the tree BEFORE the option
 * existed (X4, 2026-10-02), rendering each plot through real Recharts with a
 * DejaVu-like measure (only the container is given a size: jsdom lays nothing
 * out). A change to it is a change to every flag-off bar chart's pixels.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import BarChart from './BarChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartResponse, ChartState } from './chartState'
import { estimateTextWidth } from './StackedColumnChart'

const box = vi.hoisted(() => ({ width: 340 }))

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children, height }: { children: ReactNode; height?: number }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: box.width, height: height ?? 280 })
        : null,
  }
})

vi.mock('./chartLayout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./chartLayout')>()
  return { ...actual, useContainerWidth: () => [() => {}, box.width] }
})

/** DejaVu Sans is ~12 % wider than the estimate (the B0 finding 6 measure). */
const measure = (text: string, size: number) => estimateTextWidth(text, size) * 1.12

vi.mock('./textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./textMeasure')>()
  return { ...actual, useTextMeasure: () => [() => {}, measure] }
})

const SUITES = ['Payments', 'Checkout', 'Search', 'Profile']

/** Executions by suite x status: totals up to 960, so the axis ends on 1,000. */
const stacked = (): ChartState<ChartResponse> => ({
  status: 'ready',
  meta: null,
  revalidating: false,
  data: {
    meta: null,
    series: {
      kind: 'series',
      dimensions: ['suite', 'status'],
      x_type: 'category',
      series: [
        { key: 'passed', label: 'Passed', points: SUITES.map((x, i) => ({ x, y: 900 - i * 200, n: 900 - i * 200 })) },
        { key: 'failed', label: 'Failed', points: SUITES.map((x, i) => ({ x, y: 60 - i * 10, n: 60 - i * 10 })) },
      ],
    } satisfies SeriesChart,
  },
})

/** A ranked level (a status's suites, the leaf's tests): values up to 980. */
const ranked = (scale = 1): ChartState<ChartResponse> => ({
  status: 'ready',
  meta: null,
  revalidating: false,
  data: {
    meta: null,
    series: {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: [{ key: 'value', label: 'Failed', points: SUITES.map((x, i) => ({ x, y: (980 - i * 150) * scale, n: (980 - i * 150) * scale })) }],
    } satisfies SeriesChart,
  },
})

function plot(variant: 'stacked' | 'ranked', width: number, fitValueAxisEnd?: boolean, scale = 1) {
  box.width = width
  const { container, unmount } = render(
    <ChartAnnouncerProvider>
      <BarChart
        title="Results by suite"
        variant={variant}
        state={variant === 'stacked' ? stacked() : ranked(scale)}
        animate={false}
        {...(fitValueAxisEnd === undefined ? {} : { fitValueAxisEnd })}
      />
    </ChartAnnouncerProvider>,
  )
  const element = container.querySelector('[data-bar-chart]') as HTMLElement
  const html = element.outerHTML
  const ticks = [...element.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((text) => ({
    text: text.textContent ?? '',
    x: Number(text.getAttribute('x')),
  }))
  unmount()
  return { html, ticks, element }
}

/** How far the last tick's text runs past the svg's right edge, px (<= 0: drawn whole). */
function overhang(ticks: { text: string; x: number }[], width: number): number {
  const last = ticks[ticks.length - 1]
  return last.x + measure(last.text, 11) / 2 - width
}

describe('BarChart · fitValueAxisEnd (F-20)', () => {
  it.each([
    ['stacked, compact (the ladder at 375)', 'stacked', 340],
    ['stacked, full', 'stacked', 900],
    ['ranked, compact', 'ranked', 340],
    ['ranked, full', 'ranked', 900],
  ] as const)('absent: %s draws exactly what it drew before the option existed', (_name, variant, width) => {
    expect(plot(variant, width).html).toMatchSnapshot()
  })

  it('absent or false: the same bytes (React and Recharts ids aside: they count renders)', () => {
    const ids = (html: string) => html.replace(/_r_[0-9a-z]+_/g, 'ID').replace(/recharts\d+-clip/g, 'CLIP')
    expect(ids(plot('stacked', 340, false).html)).toBe(ids(plot('stacked', 340).html))
    expect(ids(plot('ranked', 340, false).html)).toBe(ids(plot('ranked', 340).html))
  })

  it('the defect, without it: the compact stacked plot cuts its last tick ("1,000") at the svg edge', () => {
    const { ticks } = plot('stacked', 340)
    expect(ticks[ticks.length - 1].text).toBe('1,000')
    expect(overhang(ticks, 340)).toBeGreaterThan(0)
  })

  it.each([
    ['stacked', 340],
    ['stacked', 900],
    ['ranked', 340],
    ['ranked', 900],
  ] as const)('on: the %s plot at %i px draws its last tick whole, and no tick collides with the next', (variant, width) => {
    const { ticks } = plot(variant, width, true)
    expect(ticks.length).toBeGreaterThan(2)
    expect(overhang(ticks, width)).toBeLessThanOrEqual(0)
    for (let i = 1; i < ticks.length; i += 1) {
      const gap = ticks[i].x - ticks[i - 1].x - (measure(ticks[i].text, 11) + measure(ticks[i - 1].text, 11)) / 2
      expect(gap, `${ticks[i - 1].text} | ${ticks[i].text}`).toBeGreaterThanOrEqual(4)
    }
  })

  it('on: a ranked plot whose ticks outgrow its fixed 40 px ("1000000000000") gets room for them too', () => {
    const big = 1e9
    expect(overhang(plot('ranked', 340, false, big).ticks, 340)).toBeGreaterThan(0)
    const { ticks } = plot('ranked', 340, true, big)
    expect(ticks[ticks.length - 1].text).toBe('1000000000000')
    expect(overhang(ticks, 340)).toBeLessThanOrEqual(0)
  })

  it('on: the plot says so (the drill host is checked through it)', () => {
    expect(plot('stacked', 340, true).element.getAttribute('data-bar-fit-end')).toBe('true')
    expect(plot('stacked', 340).element.hasAttribute('data-bar-fit-end')).toBe(false)
  })
})
