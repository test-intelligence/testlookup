/**
 * VIZ-105 for the Recharts kit charts that have no cursor of Recharts' own —
 * the status donut (`DonutChart`) and the ring (`RingGauge`): neither leaves
 * Recharts' `accessibilityLayer` on (it is an UNNAMED `role="application"`
 * surface, which takes the screen reader's virtual cursor away), and
 * `prefers-reduced-motion` turns every series animation off regardless of the
 * `animate` prop.
 *
 * Until W25c this file counted the three legacy charts (`TrendChart`,
 * `DefectDonut`, `PassRateGauge`) that turned the layer ON; they are deleted,
 * no kit chart turns it on, and the chart gallery's e2e asserts zero
 * `role="application"` on every item.
 */
import { act, render, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import DonutChart from './DonutChart'
import RingGauge from './RingGauge'
import type { ChartResponse, ChartState } from './chartState'
import { prefersReducedMotion, REDUCED_MOTION_QUERY, useChartAnimation, usePrefersReducedMotion } from './motion'
import { CURSOR_HINT } from './ChartCursor'

vi.mock('recharts', () => {
  const chart =
    (name: string) =>
    ({ children, accessibilityLayer }: { children?: ReactNode; accessibilityLayer?: boolean }) => (
      <svg data-chart={name} data-a11y={String(accessibilityLayer)}>
        <g>{children}</g>
      </svg>
    )
  const mark = ({ isAnimationActive }: { isAnimationActive?: boolean }) => <g data-mark="" data-animate={String(isAnimationActive)} />
  return {
    // The recharts hooks Wave 2.4's tooltip content reads (`ChartTooltip`'s `usePlotArea`, the bars' `useXAxisScale`): listed so a mock that ever renders that content does not throw.
    usePlotArea: () => undefined,
    useXAxisScale: () => undefined,
    ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    // What `DonutChart` imports (as `DonutChart.test.tsx` mocks it) …
    PieChart: chart('pie'),
    Pie: mark,
    Cell: () => null,
    Label: () => null,
    Tooltip: () => null,
    Legend: () => null,
    // … and what `RingGauge` imports.
    RadialBarChart: chart('radial'),
    RadialBar: mark,
    PolarAngleAxis: () => null,
  }
})

/** The status breakdown, as `DonutChart.test.tsx` builds it. */
function statusState(counts: Record<string, number>): ChartState<ChartResponse> {
  const series: SeriesChart = {
    kind: 'series',
    dimensions: ['status'],
    x_type: 'category',
    series: [
      {
        key: 'executions',
        label: 'Executions',
        points: Object.entries(counts).map(([status, value]) => ({ x: status, y: value, n: value })),
      },
    ],
  }
  return { status: 'ready', data: { meta: null, series: series as ChartSeries }, meta: null, revalidating: false }
}

const STATUSES = statusState({ passed: 880, failed: 60, broken: 20, skipped: 40 })

type Listener = () => void
function mockMatchMedia(reduce: boolean) {
  const listeners = new Set<Listener>()
  const state = { reduce }
  window.matchMedia = ((query: string) => ({
    get matches() {
      return query === REDUCED_MOTION_QUERY && state.reduce
    },
    media: query,
    addEventListener: (_: string, l: Listener) => listeners.add(l),
    removeEventListener: (_: string, l: Listener) => listeners.delete(l),
  })) as unknown as typeof window.matchMedia
  return {
    set(next: boolean) {
      state.reduce = next
      listeners.forEach((l) => l())
    },
  }
}

const realMatchMedia = window.matchMedia

describe('Recharts kit charts — no unnamed application, and reduced motion', () => {
  afterEach(() => {
    window.matchMedia = realMatchMedia
  })

  it('passes accessibilityLayer={false} on the donut and the ring — EXPLICITLY, as Recharts 3 defaults it on', () => {
    const { container } = render(
      <>
        <DonutChart title="Status breakdown" state={STATUSES} animate={false} />
        <RingGauge value={90} animate={false} />
      </>,
    )
    const charts = [...container.querySelectorAll('[data-chart]')]
    expect(charts.map((c) => c.getAttribute('data-chart'))).toEqual(['pie', 'radial'])
    // `false`, not `undefined`: omitting the prop would leave `role="application"` on the surface.
    for (const chart of charts) expect(chart).toHaveAttribute('data-a11y', 'false')
    expect(container.querySelector('[role="application"]')).toBeNull()
  })

  /**
   * `accessibilityLayer` puts `role="application"` on the surface — which
   * takes the screen reader's virtual cursor away — and Recharts gives that
   * surface no accessible name, so the reader lands on an unnamed
   * application. A custom tooltip `content` has already deleted Recharts' own
   * `role="status"` live region, so nothing is announced there either.
   * `useChartCursor` replaces both with a NAMED, focusable group and the
   * page's one announcer.
   */
  it('gives the donut a named focusable surface instead of an unnamed application', () => {
    const { container } = render(<DonutChart title="Status breakdown" state={STATUSES} animate={false} />)
    const surface = container.querySelector('[data-donut]') as HTMLElement
    expect(surface).not.toBeNull()
    expect(surface).toHaveAttribute('tabindex', '0')
    expect(surface).toHaveAttribute('role', 'group')
    expect(surface.getAttribute('aria-label')).toContain('Status breakdown')
    expect(surface.getAttribute('aria-label')).toContain(CURSOR_HINT)
  })

  it('disables every animation under prefers-reduced-motion, even with animate={true}', () => {
    mockMatchMedia(true)
    const { container } = render(
      <>
        <DonutChart title="Status breakdown" state={STATUSES} animate />
        <RingGauge value={90} animate />
      </>,
    )
    const marks = [...container.querySelectorAll('[data-mark]')]
    // One Pie and one RadialBar: both charts drew their series.
    expect(marks).toHaveLength(2)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'false')
  })

  it('keeps the caller\'s choice when motion is allowed', () => {
    mockMatchMedia(false)
    const { container } = render(<DonutChart title="Status breakdown" state={STATUSES} animate />)
    expect(container.querySelector('[data-mark]')).toHaveAttribute('data-animate', 'true')
  })
})

describe('motion', () => {
  afterEach(() => {
    window.matchMedia = realMatchMedia
  })

  it('follows a live change of the media query', () => {
    const media = mockMatchMedia(false)
    const { result } = renderHook(() => ({ reduced: usePrefersReducedMotion(), animate: useChartAnimation(true) }))
    expect(result.current).toEqual({ reduced: false, animate: true })
    act(() => media.set(true))
    expect(result.current).toEqual({ reduced: true, animate: false })
  })

  it('treats a browser without matchMedia as "motion allowed"', () => {
    // @ts-expect-error — simulating an environment without the API.
    window.matchMedia = undefined
    expect(prefersReducedMotion()).toBe(false)
    const { result } = renderHook(() => useChartAnimation(undefined))
    expect(result.current).toBeUndefined()
  })

  it('falls back to the deprecated listener API', () => {
    const addListener = vi.fn()
    const removeListener = vi.fn()
    window.matchMedia = (() => ({ matches: true, addListener, removeListener })) as unknown as typeof window.matchMedia
    const { unmount, result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(true)
    expect(addListener).toHaveBeenCalled()
    unmount()
    expect(removeListener).toHaveBeenCalled()
  })
})
