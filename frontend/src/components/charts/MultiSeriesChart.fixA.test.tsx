/**
 * VIZ-404 fix round A — the renderer half of the review findings (M2, M6, M7,
 * M8, m3, m4, partial day). Geometry is asserted for real in the Playwright
 * spec; this pins what reaches the DOM and Recharts.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import { DIRECT_LABEL_GUTTER, LABELS_DROPPED_NOTE, MultiSeriesTip, NO_GUTTER, SHOW_ALL_LABEL } from './MultiSeriesChart'
import MultiSeriesChartFrame from './MultiSeriesChartFrame'
import { addUtcDays } from './seriesAlignment'
import {
  LABEL_ROW,
  LABEL_SWATCH_LENGTH,
  LEADER_DASH,
  buildMultiSeriesModel,
  type MultiSeriesInputSeries,
  type MultiSeriesModel,
} from './multiSeriesModel'
import { CHART_VARS } from './tokens'

interface Captured {
  chart: Record<string, unknown> | null
  lines: Record<string, unknown>[]
  tips: Record<string, unknown>[]
}
const captured: Captured = { chart: null, lines: [], tips: [] }
const geometry = vi.hoisted(() => ({
  plot: { x: 60, y: 16, width: 400, height: 200 } as { x: number; y: number; width: number; height: number },
  width: 600,
  days: 3,
}))

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div data-testid="responsive">{children}</div>,
  LineChart: ({ children, ...props }: { children: ReactNode } & Record<string, unknown>) => {
    captured.chart = props
    return <svg data-testid="line-chart">{children}</svg>
  },
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  ReferenceLine: (props: Record<string, unknown>) => <g data-testid="focus-line" data-x={String(props.x)} />,
  Tooltip: (props: Record<string, unknown>) => {
    captured.tips.push(props)
    return null
  },
  Line: (props: Record<string, unknown>) => {
    captured.lines.push(props)
    return <g data-testid="series-line" data-name={String(props.name)} />
  },
  usePlotArea: () => geometry.plot,
  useChartWidth: () => geometry.width,
  // A point scale over `geometry.days` days, keyed by the day string's position.
  useXAxisScale: () => (value: unknown) => {
    const index = Number(String(value).slice(-2)) - 1
    return geometry.plot.x + (index * geometry.plot.width) / Math.max(1, geometry.days - 1)
  },
  useYAxisScale: () => (value: unknown) => geometry.plot.y + (1 - (value as number) / 100) * geometry.plot.height,
}))

const READY: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const day = (i: number) => addUtcDays('2026-03-01', i)
const suite = (key: string, values: (number | null)[]): MultiSeriesInputSeries => ({
  key,
  label: key,
  points: values.map((y, i) => (y === null ? { x: day(i), y: null, n: 0, measured: false, reason: `${key} ran nothing` } : { x: day(i), y, n: 100 })),
})
const THREE = [suite('payments', [97, 96, 94.4]), suite('cart', [90, null, 94.0]), suite('search', [84, 98, 87.3])]
const model3 = buildMultiSeriesModel({ series: THREE, metric: RATE, seriesNoun: 'suites' })
const TITLE = 'Pass rate by suite'

function mount(model: MultiSeriesModel) {
  return render(
    <ChartAnnouncerProvider>
      <MultiSeriesChartFrame title={TITLE} state={READY} model={model} headingLevel={2} animate={false} />
    </ChartAnnouncerProvider>,
  )
}

const lastLines = () => {
  const names = new Set<string>()
  const out: Record<string, unknown>[] = []
  for (let i = captured.lines.length - 1; i >= 0; i--) {
    const name = String(captured.lines[i].name)
    if (names.has(name)) break
    names.add(name)
    out.unshift(captured.lines[i])
  }
  return out
}

beforeEach(() => {
  captured.chart = null
  captured.lines = []
  captured.tips = []
  geometry.plot = { x: 60, y: 16, width: 400, height: 200 }
  geometry.width = 600
  geometry.days = 3
})
afterEach(() => vi.restoreAllMocks())

describe('M2: the keyboard readout sits OUTSIDE the plot and wraps', () => {
  it('is drawn after the plot in the flow, never absolutely positioned over it', () => {
    const { container } = mount(model3)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    const chart = container.querySelector('[data-testid="line-chart"]') as Element
    expect(readout).not.toBeNull()
    expect(readout.className).not.toMatch(/\babsolute\b/)
    expect(readout.className).not.toMatch(/\btruncate\b/)
    expect(chart.compareDocumentPosition(readout) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('M8: the pointer tooltip is pinned per DAY', () => {
  const tipAt = (props: { label: string; coordinate?: { x: number; y: number } }) =>
    render(
      <div data-parent="">
        <MultiSeriesTip active model={model3} hidden={new Set()} format={(v) => `${v}`} {...props} />
      </div>,
    )

  it('sits a fixed offset right of the day\'s x, at the plot top — the pointer\'s y does not move it', () => {
    const first = tipAt({ label: day(1), coordinate: { x: 250, y: 40 } })
    const box = first.container.querySelector('[data-chart-tooltip]') as HTMLElement
    const at = { left: box.style.left, top: box.style.top }
    first.unmount()
    const second = tipAt({ label: day(1), coordinate: { x: 268, y: 190 } })
    const moved = second.container.querySelector('[data-chart-tooltip]') as HTMLElement
    // Day 1 of 3 is at 60 + 200 = 260; the gap is 12 px (half a step is 100).
    expect(at).toEqual({ left: '272px', top: '16px' })
    expect({ left: moved.style.left, top: moved.style.top }).toEqual(at)
  })

  it('flips to the left of the day when it would run past the chart\'s right edge', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(200)
    const { container } = tipAt({ label: day(2) })
    const box = container.querySelector('[data-chart-tooltip]') as HTMLElement
    // Day 2 is at 460: 460 + 12 + 200 > 600, so it ends 12 px left of the day.
    expect(box.style.left).toBe(`${460 - 12 - 200}px`)
  })

  it('holds its day while the pointer is on it: pointer moves over it never reach the chart', () => {
    const onParentMove = vi.fn()
    const { container } = render(
      <div onMouseMove={onParentMove}>
        <MultiSeriesTip active label={day(1)} model={model3} hidden={new Set()} format={(v) => `${v}`} />
      </div>,
    )
    const box = container.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(box.style.pointerEvents).toBe('auto')
    fireEvent.mouseMove(box)
    expect(onParentMove).not.toHaveBeenCalled()
  })

  it('reaches Recharts with a fixed origin and no slide animation', () => {
    mount(model3)
    const tip = captured.tips[captured.tips.length - 1]
    expect(tip.position).toEqual({ x: 0, y: 0 })
    expect(tip.isAnimationActive).toBe(false)
  })
})

describe('M6: the direct-label gutter never crushes the plot', () => {
  it('drops the labels (and the gutter) when the plot would be narrower than the minimum', () => {
    geometry.plot = { x: 60, y: 16, width: 150, height: 200 }
    const { container } = mount(model3)
    expect(container.querySelectorAll('[data-direct-label]')).toHaveLength(0)
    expect(screen.getByText(LABELS_DROPPED_NOTE)).toBeInTheDocument()
    expect((captured.chart?.margin as { right: number }).right).toBe(NO_GUTTER)
  })

  it('keeps them when the plot has room', () => {
    const { container } = mount(model3)
    expect(container.querySelectorAll('[data-direct-label]')).toHaveLength(3)
    expect((captured.chart?.margin as { right: number }).right).toBe(DIRECT_LABEL_GUTTER)
  })
})

describe('M7: "Show all series" never drops focus to <body>', () => {
  it('moves focus to the first legend toggle as it goes', () => {
    const { container } = mount(model3)
    fireEvent.click(container.querySelector('[data-legend-series="cart"]') as HTMLElement)
    const showAll = screen.getByRole('button', { name: SHOW_ALL_LABEL })
    showAll.focus()
    act(() => {
      fireEvent.click(showAll)
    })
    expect(screen.queryByRole('button', { name: SHOW_ALL_LABEL })).toBeNull()
    expect(document.activeElement).toBe(container.querySelector('[data-legend-series="payments"]'))
  })
})

describe('m3: the not-comparable banner', () => {
  const reason = 'release/2.4 ran 2 suites that main did not'
  const model = buildMultiSeriesModel({ series: THREE, metric: RATE, comparability: { comparable: false, reason } })

  it('is a note with an icon and a strong border', () => {
    const { container } = mount(model)
    const banner = container.querySelector('[data-chart-comparable-banner]') as HTMLElement
    expect(banner).toHaveAttribute('role', 'note')
    expect(banner.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(banner.className).toMatch(/border-\[var\(--color-text-muted\)\]/)
  })

  it('is part of what a reader hears on tabbing straight to the plot, and of the frame summary', () => {
    const { container } = mount(model)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    expect(surface).toHaveAccessibleDescription(expect.stringContaining(reason))
    expect(container.querySelector('[data-chart-summary]')?.textContent).toContain(reason)
  })

  it('a comparable chart describes nothing extra', () => {
    const { container } = mount(model3)
    expect(container.querySelector('[data-multi-series-plot]')).not.toHaveAttribute('aria-describedby')
    expect(container.querySelector('[data-chart-summary]')?.textContent).not.toMatch(/comparable/i)
  })
})

describe('m4: the legend and the leaders', () => {
  it('names the legend group after the chart, and sizes each toggle to 24 px', () => {
    const { container } = mount(model3)
    expect(screen.getByRole('group', { name: `Series shown on ${TITLE}` })).toHaveAttribute('data-multi-series-legend')
    for (const toggle of container.querySelectorAll('[data-legend-series]')) expect(toggle.className).toMatch(/\bmin-h-6\b/)
  })

  it('draws each swatch at least 32 px long, so a whole dash period shows', () => {
    const { container } = mount(model3)
    for (const swatch of container.querySelectorAll('[data-multi-series-legend] svg')) {
      expect(Number(swatch.getAttribute('width'))).toBeGreaterThanOrEqual(32)
    }
  })

  // Fix round B: a leader in its line's colour AND dash read as the line
  // itself crashing on the last day. The leader is now a thin axis-coloured
  // connector, clear of the line, in the gutter; the swatch carries the match.
  it('draws each leader in the axis colour and its own pattern — never its line\'s colour or dash', () => {
    const { container } = mount(model3)
    for (const line of model3.lines) {
      const leader = container.querySelector(`[data-direct-label="${line.key}"] [data-direct-label-leader]`)
      expect(leader, line.key).not.toBeNull()
      expect(leader?.getAttribute('stroke')).toBe(CHART_VARS.axis)
      expect(leader?.getAttribute('stroke')).not.toBe(CHART_VARS.series[line.styleIndex])
      expect(leader?.getAttribute('stroke-dasharray')).toBe(LEADER_DASH)
      expect(leader?.getAttribute('stroke-dasharray') ?? undefined).not.toBe(line.dash)
      expect(Number(leader?.getAttribute('stroke-width'))).toBe(1)
    }
  })

  it('starts each leader clear of the plot edge and keeps it in the gutter, flat before it turns', () => {
    const { container } = mount(model3)
    const edge = geometry.plot.x + geometry.plot.width
    for (const line of model3.lines) {
      const points = (container.querySelector(`[data-direct-label="${line.key}"] [data-direct-label-leader]`)?.getAttribute('points') ?? '')
        .trim()
        .split(/\s+/)
        .map((pair) => pair.split(',').map(Number))
      expect(points, line.key).toHaveLength(3)
      expect(points[0][0]).toBe(edge + LABEL_ROW.leaderStart)
      for (const [x] of points) expect(x).toBeGreaterThanOrEqual(edge + 3)
      // Flat at the height the line ends, then one step to the label's row.
      expect(points[1][1]).toBe(points[0][1])
      const text = container.querySelector(`[data-direct-label="${line.key}"] text`)
      expect(points[2][1]).toBe(Number(text?.getAttribute('y')))
    }
  })

  it('puts a swatch of the line\'s own colour AND dash right before each name', () => {
    const { container } = mount(model3)
    const edge = geometry.plot.x + geometry.plot.width
    for (const line of model3.lines) {
      const group = container.querySelector(`[data-direct-label="${line.key}"]`) as Element
      const swatch = group.querySelector('[data-direct-label-swatch]')
      const text = group.querySelector('text')
      expect(swatch?.getAttribute('stroke'), line.key).toBe(CHART_VARS.series[line.styleIndex])
      expect(swatch?.getAttribute('stroke-dasharray') ?? undefined, line.key).toBe(line.dash)
      expect(Number(swatch?.getAttribute('x2')) - Number(swatch?.getAttribute('x1'))).toBe(LABEL_SWATCH_LENGTH)
      expect(Number(swatch?.getAttribute('x2'))).toBeLessThan(Number(text?.getAttribute('x')))
      expect(Number(swatch?.getAttribute('x1'))).toBe(edge + LABEL_ROW.swatchStart)
      expect(swatch?.getAttribute('y1')).toBe(text?.getAttribute('y'))
    }
  })
})

describe('fix round B: past a release\'s range is its own note, not a gap', () => {
  it('says how many days the short release has, and counts no gap for the rest', () => {
    const r1 = { key: 'r1', label: 'R1', points: [0, 1, 2, 3].map((i) => ({ x: addUtcDays('2026-02-02', i), y: 80 + i, n: 9 })) }
    const r2 = { key: 'r2', label: 'R2', points: [0, 1].map((i) => ({ x: addUtcDays('2026-02-20', i), y: 70 + i, n: 9 })) }
    const { container } = mount(buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start' }))
    expect(container.querySelector('[data-chart-gap-note]')).toBeNull()
    expect(container.querySelector('[data-chart-range-note]')?.textContent).toBe('R2 has 2 days; days 2–3 are past its range.')
  })

  it('a chart with real gaps and no short release says only "not measured"', () => {
    const { container } = mount(model3)
    expect(container.querySelector('[data-chart-gap-note]')?.textContent).toBe('1 value is not measured and drawn as a gap, never as 0.')
    expect(container.querySelector('[data-chart-range-note]')).toBeNull()
  })
})

describe('the still-filling day', () => {
  const meta = { partial_day: day(2), includes_in_progress: 2 } as unknown as EnvelopeMeta
  const model = buildMultiSeriesModel({ series: THREE, metric: RATE, meta })

  it('draws the partial day\'s points hollow, and says the day is still filling', () => {
    const { container } = mount(model)
    const payments = lastLines()[0]
    expect(typeof payments.dot).toBe('function')
    const dot = payments.dot as (props: Record<string, unknown>) => ReactElement | null
    const partial = dot({ index: 2, cx: 10, cy: 20, key: 'p' })
    expect(isValidElement(partial)).toBe(true)
    expect((partial as ReactElement<Record<string, unknown>>).props.fill).toBe(CHART_VARS.card)
    expect((partial as ReactElement<Record<string, unknown>>).props['data-partial-dot']).toBe('')
    const complete = dot({ index: 1, cx: 10, cy: 20, key: 'c' })
    expect(isValidElement(complete) ? (complete as ReactElement<Record<string, unknown>>).props['data-partial-dot'] : undefined).toBeUndefined()
    expect(container.querySelector('[data-chart-partial-note]')?.textContent).toBe(
      `${day(2)} is still filling (2 in progress): its points are drawn hollow, and the line-end labels name the last complete day.`,
    )
  })

  it('without a partial day, nothing is marked', () => {
    const { container } = mount(model3)
    expect(container.querySelector('[data-chart-partial-note]')).toBeNull()
  })
})
