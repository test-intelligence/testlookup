/**
 * Wave 2.6 fix round (PA "Remaining shift") — a time-series frame does not
 * grow when its data arrives.
 *
 * Under the plot the drawn chart ALWAYS prints its axis caption ("Days are UTC
 * buckets; …": one 16 px line, 4 px below the plot). The loading body held
 * only the plot's height, so on Summary the frame grew as it drew and moved
 * the tables below it. Until it draws, the frame's body now reserves that
 * line too. Drawn, the chart's content (plot + caption) is that tall already,
 * so the drawn frame's markup is left exactly as it was (committed DOM pins).
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import TimeSeriesChartFrame, { TIME_SERIES_CAPTION_RESERVE } from './TimeSeriesChartFrame'
import { trendWithReleasesFixture } from './__fixtures__/wave2Fixtures'

const plotHeights: number[] = []

vi.mock('recharts', () => {
  const pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    usePlotArea: () => ({ x: 40, y: 16, width: 400, height: 200 }),
    useXAxisScale: () => undefined,
    ResponsiveContainer: ({ children, height }: { children?: ReactNode; height?: number }) => {
      plotHeights.push(Number(height))
      return <div>{children}</div>
    },
    ComposedChart: pass,
    CartesianGrid: () => null,
    Legend: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Line: () => null,
    Bar: pass,
    Cell: () => null,
    ReferenceLine: () => null,
    ReferenceDot: () => null,
  }
})

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

const LOADING: ChartState<unknown> = { status: 'loading' }
const EMPTY: ChartState<unknown> = { status: 'filtered-empty', meta: null }
const READY: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }

function bodyOf(state: ChartState<unknown>, height = 240) {
  const { container, unmount } = render(
    <ChartAnnouncerProvider>
      <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} state={state} model={trendWithReleasesFixture} height={height} animate={false} />
    </ChartAnnouncerProvider>,
  )
  const body = container.querySelector('[data-chart-body]') as HTMLElement
  const out = { minHeight: body.style.minHeight, skeleton: (body.querySelector('[data-skeleton]') as HTMLElement | null)?.style.height, caption: body.querySelector('[data-chart-axis-caption]') }
  unmount()
  return out
}

describe('the loading body reserves the caption line the drawn chart always prints', () => {
  it('is one 16 px line and the 4 px gap above it', () => {
    expect(TIME_SERIES_CAPTION_RESERVE).toBe(16 + 4)
  })

  it('loading: the body and its skeleton hold the plot AND the caption line', () => {
    const loading = bodyOf(LOADING)
    expect(loading.minHeight).toBe(`${240 + TIME_SERIES_CAPTION_RESERVE}px`)
    expect(loading.skeleton).toBe(`${240 + TIME_SERIES_CAPTION_RESERVE}px`)
  })

  it('an empty state holds the same height (it is replaced by the drawn chart in place, too)', () => {
    expect(bodyOf(EMPTY).minHeight).toBe(`${240 + TIME_SERIES_CAPTION_RESERVE}px`)
  })

  it('drawn: a plot of exactly the height asked for with the caption under it — the height the loading body held', () => {
    plotHeights.length = 0
    const drawn = bodyOf(READY)
    // Unchanged markup: the content (plot + caption line) fills what loading reserved.
    expect(drawn.minHeight).toBe('240px')
    expect(plotHeights.slice(-1)[0]).toBe(240)
    expect(drawn.caption).not.toBeNull()
    // The caption is the 16 px line the reserve is for (text-xs), in a figure that spaces its rows 4 px apart.
    expect(drawn.caption?.className).toMatch(/\btext-xs\b/)
    expect(drawn.caption?.parentElement?.className).toMatch(/\bgap-1\b/)
  })
})
