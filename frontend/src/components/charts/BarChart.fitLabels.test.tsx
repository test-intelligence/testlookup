/**
 * Wave 2.6 fix round 2 (B0 finding 6): horizontal-bar category labels fit
 * their axis by MEASUREMENT.
 *
 * On Linux (DejaVu Sans) a 13-character compact label was wider than the
 * 92 px compact axis: its head ran off the svg ("aymen…imeout" for
 * "Payment gateway timeout"), and a label wider than the axis was wrapped by
 * Recharts onto two lines that overlapped the next bar's. jsdom has no layout,
 * so the card width and a DejaVu-like measure (the estimate x 1.12) are
 * supplied, and Recharts lays the axis out for real.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import BarChart, { CATEGORY_TICK_ROOM } from './BarChart'
import { MIN_FITTED_LABEL, fitCategoryLabel, middleTruncate } from './BarChart.model'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartResponse, ChartState } from './chartState'
import { estimateTextWidth } from './StackedColumnChart'

const box = vi.hoisted(() => ({ width: 380, measured: true }))

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

const measure = (text: string, size: number) => estimateTextWidth(text, size) * 1.12

vi.mock('./textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./textMeasure')>()
  return { ...actual, useTextMeasure: () => [() => {}, box.measured ? measure : null] }
})

const NAMES = [
  'Payment gateway timeout',
  'Tax provider rounding mismatch',
  '<img src=x onerror="window.__xss=1">',
  'Session store not cleared',
  'card declined should show a reason (Payments)',
  'ok',
]

const ranked = (names: readonly string[]): ChartState<ChartResponse> => ({
  status: 'ready',
  meta: null,
  revalidating: false,
  data: {
    meta: null,
    series: {
      kind: 'series',
      dimensions: ['failure_cluster'],
      x_type: 'category',
      series: [{ key: 'failures', label: 'Failures', points: names.map((x, i) => ({ x, y: 20 - i, n: 20 - i })) }],
    } satisfies SeriesChart,
  },
})

const stacked = (names: readonly string[]): ChartState<ChartResponse> => ({
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
        { key: 'passed', label: 'Passed', points: names.map((x) => ({ x, y: 10, n: 10 })) },
        { key: 'failed', label: 'Failed', points: names.map((x) => ({ x, y: 2, n: 2 })) },
      ],
    } satisfies SeriesChart,
  },
})

function categoryTicks(container: HTMLElement): string[] {
  return [...container.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')].map(
    (tick) => tick.textContent ?? '',
  )
}

function axisWidth(container: HTMLElement): number {
  // The width the chart gave its category axis (sized from the chart below 460 px since R2-1).
  return Number(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-axis-width'))
}

describe('BarChart · category labels fit by measurement (B0 finding 6)', () => {
  it.each([
    ['ranked, compact (the gate at 480)', 380, ranked],
    ['ranked, full axis (Summary "Failures by test")', 900, ranked],
    ['stacked by suite, compact', 380, stacked],
    ['stacked by suite, full axis', 900, stacked],
  ] as const)('%s: every label fits the room the axis gives it', (_name, width, state) => {
    box.width = width
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant={state === ranked ? 'ranked' : 'stacked'} state={state(NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    const ticks = categoryTicks(container)
    expect(ticks).toHaveLength(NAMES.length)
    const room = axisWidth(container) - CATEGORY_TICK_ROOM
    for (const tick of ticks) expect(measure(tick, 11)).toBeLessThanOrEqual(room)
    // Geometry, independent of that constant: Recharts anchors each label's END
    // at `x`; its start (x - measured width) must not pass the svg's left edge.
    for (const text of container.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')) {
      const x = Number(text.getAttribute('x'))
      expect(text.getAttribute('text-anchor')).toBe('end')
      expect(x - measure(text.textContent ?? '', 11)).toBeGreaterThanOrEqual(0)
      // One line: Recharts' `Text` word-wraps only when it is handed a width
      // (measured at the wrong font size, so it wrapped labels that fit).
      expect(text.getAttribute('width')).toBeNull()
      expect(text.querySelectorAll('tspan')).toHaveLength(1)
    }
    // Nothing is cut that already fitted.
    expect(ticks).toContain('ok')
  })

  it('before layout (no measure) the axis draws exactly the character cuts it always drew', () => {
    box.width = 380
    box.measured = false
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant="ranked" state={ranked(NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    expect(categoryTicks(container)).toEqual(NAMES.map((name) => middleTruncate(middleTruncate(name), 13)))
  })

  it('a hostile label is cut as text, never markup', () => {
    box.width = 380
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant="ranked" state={ranked(NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    expect(container.querySelector('img')).toBeNull()
  })
})

/**
 * The category axis's width as DRAWN: Recharts ends a left tick label at the
 * axis's right edge minus `tickSize` (6) and `tickMargin` (2), and the axis
 * starts at the plot's 4 px left margin.
 */
function drawnAxisWidth(container: HTMLElement): number {
  const tick = container.querySelector('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')
  return Number(tick?.getAttribute('x')) + 6 + 2 - 4
}

/** R2-1's names: a 480 px card at 1280 cut every one of them to ~13 characters. */
const R2_NAMES = [
  'Payment gateway timeout',
  'login times out (Auth)',
  'login times out (Checkout)',
  'card declined should show a reason (Payments)',
  'Webhook signature mismatch',
]

describe('BarChart · a compact label column sized from the chart, not fixed at 92 px (R2-1)', () => {
  // The plot's right margin on a compact chart: the ranked bars' value labels, the status bars' none.
  const RIGHT = { ranked: 40, stacked: 12 } as const

  it.each([
    ['ranked, a 480 px card at 1280 (Overview top failing, Gate clusters)', 446, ranked, 'ranked'],
    ['ranked, Summary "Failures by test" at 375', 309, ranked, 'ranked'],
    ['ranked, a 300 px chart', 300, ranked, 'ranked'],
    // Under ~298 px the plot's 120 px floor, not the 45 % share, is what limits the column.
    ['ranked, a 260 px chart (a phone, both sides of the card padded)', 260, ranked, 'ranked'],
    ['stacked by suite, compact', 380, stacked, 'stacked'],
  ] as const)('%s: names that fit are whole, the plot keeps its room', (_name, width, state, variant) => {
    box.width = width
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant={variant} state={state(R2_NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    expect(container.querySelector('[data-bar-compact="true"]')).not.toBeNull()
    const axis = drawnAxisWidth(container)
    const room = axis - CATEGORY_TICK_ROOM
    // Never narrower than the old compact column, never wider than the names need…
    expect(axis).toBeGreaterThanOrEqual(92)
    const longest = Math.max(...R2_NAMES.map((name) => measure(name, 11)))
    expect(axis).toBeLessThanOrEqual(Math.max(92, Math.ceil(longest) + CATEGORY_TICK_ROOM))
    // …and the plot beside it keeps at least 120 px and the labels at most 45 % of the chart.
    expect(width - 4 - RIGHT[variant] - axis).toBeGreaterThanOrEqual(120)
    expect(axis).toBeLessThanOrEqual(Math.max(92, width * 0.45))
    expect(container.querySelector('[data-bar-chart]')).toHaveAttribute('data-bar-axis-width', String(axis))
    // Every name that fits the column is drawn whole; the rest are cut to fit it.
    const ticks = categoryTicks(container)
    R2_NAMES.forEach((name, i) => {
      if (measure(name, 11) <= room) expect(ticks[i]).toBe(name)
      else expect(measure(ticks[i], 11)).toBeLessThanOrEqual(room)
    })
  })

  it('at a 480 px card the R2 names are whole (they were "Payme…meout", "login …(Auth)")', () => {
    box.width = 446
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant="ranked" state={ranked(R2_NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    const ticks = categoryTicks(container)
    expect(ticks).toContain('Payment gateway timeout')
    expect(ticks).toContain('login times out (Auth)')
    // The longest is still cut, but to the column, not to 13 characters.
    expect([...ticks[3]].length).toBeGreaterThan(13)
  })

  it('short names keep the 92 px column', () => {
    box.width = 446
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant="ranked" state={ranked(['ok', 'no'])} animate={false} />
      </ChartAnnouncerProvider>,
    )
    expect(drawnAxisWidth(container)).toBe(92)
  })

  it('a full-width chart keeps its 210 px column (no committed desk screenshot moves)', () => {
    box.width = 900
    box.measured = true
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="t" variant="ranked" state={ranked(R2_NAMES)} animate={false} />
      </ChartAnnouncerProvider>,
    )
    expect(drawnAxisWidth(container)).toBe(210)
  })
})

describe('fitCategoryLabel', () => {
  const width = (text: string) => [...text].length * 10

  it('keeps the first cut when it fits', () => {
    expect(fitCategoryLabel('abcdefghij', 'abcde…ghij', 100, width)).toBe('abcde…ghij')
  })

  it('cuts the FULL name in the middle until it fits', () => {
    const fitted = fitCategoryLabel('abcdefghijklmnop', 'abcdefghijklmnop', 70, width)
    expect(width(fitted)).toBeLessThanOrEqual(70)
    expect(fitted).toBe(middleTruncate('abcdefghijklmnop', 7))
  })

  it('stops at the shortest cut when nothing fits', () => {
    expect(fitCategoryLabel('abcdefghij', 'abcdefghij', 5, width)).toBe(middleTruncate('abcdefghij', MIN_FITTED_LABEL))
  })
})
