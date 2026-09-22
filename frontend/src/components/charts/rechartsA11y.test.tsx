/**
 * VIZ-105 for the three Recharts components: `accessibilityLayer` is on (keyboard
 * navigation + ARIA from Recharts 3), and `prefers-reduced-motion` turns every
 * series animation off regardless of the `animate` prop.
 */
import { act, render, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TrendChart from './TrendChart'
import DefectDonut from './DefectDonut'
import PassRateGauge from './PassRateGauge'
import { prefersReducedMotion, REDUCED_MOTION_QUERY, useChartAnimation, usePrefersReducedMotion } from './motion'

vi.mock('recharts', () => {
  const chart =
    (name: string) =>
    ({ children, accessibilityLayer }: { children?: ReactNode; accessibilityLayer?: boolean }) => (
      <div data-chart={name} data-a11y={String(accessibilityLayer)}>
        {children}
      </div>
    )
  const mark = ({ isAnimationActive }: { isAnimationActive?: boolean }) => <div data-mark data-animate={String(isAnimationActive)} />
  const box = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: box,
    LineChart: chart('line'),
    AreaChart: chart('area'),
    BarChart: chart('bar'),
    PieChart: chart('pie'),
    RadialBarChart: chart('radial'),
    CartesianGrid: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Legend: () => null,
    Cell: () => null,
    Line: mark,
    Area: mark,
    Bar: mark,
    Pie: mark,
    RadialBar: mark,
  }
})

const POINT = { date: '2026-09-01', passed: 1, failed: 1, skipped: 1, broken: 1, total: 4, pass_rate: 25 }

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

describe('Recharts components — accessibility layer and reduced motion', () => {
  afterEach(() => {
    window.matchMedia = realMatchMedia
  })

  it('turns accessibilityLayer on for every Recharts chart', () => {
    const { container } = render(
      <>
        <TrendChart data={[POINT]} type="line" />
        <TrendChart data={[POINT]} type="area" />
        <TrendChart data={[POINT]} type="bar" />
        <DefectDonut data={[1, 2, 3, 4]} />
        <PassRateGauge value={90} />
      </>,
    )
    const charts = [...container.querySelectorAll('[data-chart]')]
    expect(charts.map((c) => c.getAttribute('data-chart'))).toEqual(['line', 'area', 'bar', 'pie', 'radial'])
    for (const chart of charts) expect(chart).toHaveAttribute('data-a11y', 'true')
  })

  it('disables every animation under prefers-reduced-motion, even with animate={true}', () => {
    mockMatchMedia(true)
    const { container } = render(
      <>
        <TrendChart data={[POINT]} type="line" animate />
        <TrendChart data={[POINT]} type="bar" />
        <DefectDonut data={[1, 2, 3, 4]} animate />
        <PassRateGauge value={90} animate />
      </>,
    )
    const marks = [...container.querySelectorAll('[data-mark]')]
    expect(marks.length).toBeGreaterThan(0)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'false')
  })

  it('keeps the caller\'s choice when motion is allowed', () => {
    mockMatchMedia(false)
    const { container } = render(<DefectDonut data={[1, 2, 3, 4]} animate />)
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
