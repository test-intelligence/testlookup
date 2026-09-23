/**
 * VIZ-404 — the renderer and its frame. Every edge case is asserted on what
 * reaches Recharts, what reaches the page's ONE announcer and what the table
 * view shows — not on a screenshot.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { cloneElement, type ReactElement, type ReactNode } from 'react'
import { tooltipText } from './tooltip'
import { NBSP } from '@/lib/trendStats'
import { readTooltip } from './tooltipTestUtils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import { CURSOR_HINT } from './ChartCursor'
import { LABELS_DROPPED_NOTE, LEGEND_HINT, MultiSeriesTip, SHOW_ALL_LABEL, multiSeriesTipContent } from './MultiSeriesChart'
import MultiSeriesChartFrame from './MultiSeriesChartFrame'
import { addUtcDays } from './seriesAlignment'
import { sliceMultiSeriesModel } from './zoom/zoomModel'
import {
  ALIGNED_X_TITLE,
  HIDDEN_SUFFIX,
  buildMultiSeriesModel,
  tipContentAt,
  type MultiSeriesInputSeries,
  type MultiSeriesModel,
} from './multiSeriesModel'

interface Captured {
  chart: Record<string, unknown> | null
  lines: Record<string, unknown>[]
  tips: Record<string, unknown>[]
}
const captured: Captured = { chart: null, lines: [], tips: [] }
/** The plot area and y scale the direct labels read from the chart context. */
const geometry = vi.hoisted(() => ({ plot: { x: 60, y: 16, width: 400, height: 200 } as { x: number; y: number; width: number; height: number } | undefined }))

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
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
  useChartWidth: () => 600,
  useXAxisScale: () => undefined,
  useYAxisScale: () => (value: unknown) => {
    const plot = geometry.plot ?? { y: 0, height: 0 }
    return plot.y + (1 - (value as number) / 100) * plot.height
  },
}))

const READY: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const day = (i: number) => addUtcDays('2026-03-01', i)

function suite(key: string, values: (number | null)[], n = 100): MultiSeriesInputSeries {
  return {
    key,
    label: key,
    points: values.map((y, i) =>
      y === null ? { x: day(i), y: null, n: 0, measured: false, reason: `${key} ran nothing that day` } : { x: day(i), y, n },
    ),
  }
}

// payments and cart end 0.4 apart: their labels must be nudged.
const THREE = [suite('payments', [97, 96, 94.4]), suite('cart', [90, null, 94.0]), suite('search', [84, 98, 87.3])]
const model3 = buildMultiSeriesModel({ series: THREE, metric: RATE, seriesNoun: 'suites' })

function mount(model: MultiSeriesModel, props: { initialHidden?: string[]; title?: string } = {}) {
  return render(
    <ChartAnnouncerProvider>
      <MultiSeriesChartFrame
        title={props.title ?? 'Pass rate by suite'}
        state={READY}
        model={model}
        headingLevel={2}
        initialHidden={props.initialHidden}
        animate={false}
      />
    </ChartAnnouncerProvider>,
  )
}

/** The Line props Recharts received in the LAST render, one per drawn series. */
const lastLines = () => {
  const count = captured.lines.length
  const names = new Set<string>()
  const out: Record<string, unknown>[] = []
  for (let i = count - 1; i >= 0; i--) {
    const name = String(captured.lines[i].name)
    if (names.has(name)) break
    names.add(name)
    out.unshift(captured.lines[i])
  }
  return out
}

const directLabels = (container: HTMLElement) =>
  [...container.querySelectorAll('[data-direct-label]')].map((node) => ({
    key: node.getAttribute('data-direct-label'),
    y: Number(node.querySelector('text')?.getAttribute('y')),
  }))

beforeEach(() => {
  captured.chart = null
  captured.lines = []
  captured.tips = []
  geometry.plot = { x: 60, y: 16, width: 400, height: 200 }
})
afterEach(() => vi.useRealTimers())

describe('MultiSeriesChart — the lines', () => {
  it('draws one line per series, told apart by dash AND colour, with gaps as gaps', () => {
    mount(model3)
    const lines = lastLines()
    expect(lines.map((l) => l.name)).toEqual(['payments', 'cart', 'search'])
    expect(new Set(lines.map((l) => String(l.strokeDasharray))).size).toBe(3)
    expect(new Set(lines.map((l) => l.stroke)).size).toBe(3)
    for (const line of lines) expect(line.connectNulls).toBe(false)
    // cart's unmeasured day reaches Recharts as null — never 0.
    const data = captured.chart?.data as Record<string, unknown>[]
    const cartKey = String(lines[1].dataKey)
    expect(data.map((row) => row[cartKey])).toEqual([90, null, 94])
    expect(captured.chart?.accessibilityLayer).toBe(false)
  })

  it('puts the plot in a named, focusable group — not an unnamed application', () => {
    const { container } = mount(model3)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    expect(surface).toHaveAttribute('role', 'group')
    expect(surface).toHaveAttribute('tabindex', '0')
    expect(surface.getAttribute('aria-label')).toContain('Pass rate by suite')
    expect(surface.getAttribute('aria-label')).toContain(CURSOR_HINT)
    expect(container.querySelector('[role="application"]')).toBeNull()
  })
})

describe('direct labels', () => {
  it('labels every line end, nudged so that none overlaps', () => {
    const { container } = mount(model3)
    const labels = directLabels(container)
    expect(labels.map((l) => l.key).sort()).toEqual(['cart', 'payments', 'search'])
    const ys = labels.map((l) => l.y).sort((a, b) => a - b)
    for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(16)
    expect(screen.queryByText(LABELS_DROPPED_NOTE)).toBeNull()
  })

  it('drops to the legend alone, and says so, when they cannot fit', () => {
    geometry.plot = { x: 60, y: 16, width: 400, height: 40 } // room for two 16 px labels, not three
    const { container } = mount(model3)
    expect(directLabels(container)).toEqual([])
    expect(screen.getByText(LABELS_DROPPED_NOTE)).toBeInTheDocument()
  })
})

describe('the shared tooltip', () => {
  const detailOf = (content: ReturnType<typeof multiSeriesTipContent>, key: string) =>
    content.rows.find((row) => row.data?.['data-tip-series'] === key)?.detail

  it('a ZOOMED model’s first day states its change vs the day before the view (Wave 2.4 F4)', () => {
    const format = (v: number) => `${v.toFixed(1)}%`
    const zoomed = sliceMultiSeriesModel(model3, { start: 1, end: 2 })
    const content = multiSeriesTipContent(zoomed, 0, tipContentAt(zoomed, 0, { hidden: new Set(), format }), format)
    expect(content.title).toBe(day(1))
    // The same words the unzoomed chart says for that day.
    expect(detailOf(content, 'search')).toBe(`100 samples, +14${NBSP}pts vs previous day`)
    expect(detailOf(content, 'payments')).toBe(`100 samples, −1${NBSP}pts vs previous day`)
    // A zoom starting on the first day of the data has no previous day to state.
    const atStart = sliceMultiSeriesModel(model3, { start: 0, end: 1 })
    expect(detailOf(multiSeriesTipContent(atStart, 0, tipContentAt(atStart, 0, { hidden: new Set(), format }), format), 'search')).toBe('100 samples')
  })

  it('states a RATE’s change in percentage points and a COUNT’s as the count (Wave 2.4 F5)', () => {
    const rate = multiSeriesTipContent(model3, 1, tipContentAt(model3, 1, { hidden: new Set(), format: (v) => `${v}%` }), (v) => `${v}%`)
    expect(detailOf(rate, 'search')).toBe(`100 samples, +14${NBSP}pts vs previous day`)
    const counts = buildMultiSeriesModel({ series: THREE, metric: { kind: 'count', title: 'Failures' }, seriesNoun: 'suites' })
    const count = multiSeriesTipContent(counts, 1, tipContentAt(counts, 1, { hidden: new Set(), format: (v) => `${v}` }), (v) => `${v}`)
    expect(detailOf(count, 'search')).toBe('100 samples, +14 vs previous day')
  })

  it('lists every shown series for the day, sorted descending, the unmeasured one last as "—" with its reason', () => {
    render(<MultiSeriesTip active label={day(1)} model={model3} hidden={new Set()} format={(v) => `${v.toFixed(1)}%`} />)
    const rows = [...document.querySelectorAll('[data-tip-series]')]
    expect(rows.map((row) => row.getAttribute('data-tip-series'))).toEqual(['search', 'payments', 'cart'])
    expect(rows.map((row) => row.querySelector('[data-tip-value]')?.textContent)).toEqual(['98.0%', '96.0%', '—'])
    expect(document.querySelector('[data-tip-reason-visible="cart"]')).toHaveTextContent('cart ran nothing that day')
  })

  it('is handed to Recharts as a React element (escaped), and made dismissible by the cursor', () => {
    mount(model3)
    const tip = captured.tips[captured.tips.length - 1]
    expect(typeof tip.content).toBe('object')
    expect(tip.wrapperStyle).toEqual({ pointerEvents: 'auto' })
  })

  it('the keyboard reaches the same sorted content through the page announcer, and sees it in a readout', () => {
    const { container } = mount(model3)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' }) // day 0
    fireEvent.keyDown(surface, { key: 'ArrowRight' }) // day 1
    const assertive = container.querySelector('[data-chart-announcer="assertive"]')
    // VIZ-601: each series with its own n and its change vs the previous day —
    // a rate's change in percentage POINTS, as the single-series chart says it
    // (Wave 2.4 F5) — then, as in the tooltip, after the values, why "—" is "—".
    expect(assertive?.textContent).toBe(
      `Pass rate by suite: ${day(1)}. search: 98.0% (100 samples, +14${NBSP}pts vs previous day). ` +
        `payments: 96.0% (100 samples, −1${NBSP}pts vs previous day). cart: —. cart: cart ran nothing that day`,
    )
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    expect([...readout.querySelectorAll('[data-tip-series]')].map((row) => row.getAttribute('data-tip-series'))).toEqual([
      'search',
      'payments',
      'cart',
    ])
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(container.querySelector('[data-chart-readout]')).toBeNull()
  })

  it('pointer and keyboard read the SAME content for every day (VIZ-601)', () => {
    const { container } = mount(model3)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    const content = captured.tips[captured.tips.length - 1].content as ReactElement<Record<string, unknown>>
    model3.xs.forEach((x, index) => {
      fireEvent.keyDown(surface, { key: index === 0 ? 'Home' : 'ArrowRight' })
      const hover = render(cloneElement(content, { active: true, label: x }))
      const pointed = readTooltip(hover.container.querySelector('[data-chart-tooltip]') as HTMLElement)
      hover.unmount()
      const heard = container.querySelector('[data-chart-announcer="assertive"]')?.textContent
      expect(heard).toBe(`Pass rate by suite: ${tooltipText(pointed)}`)
      expect(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement)).toEqual(pointed)
    })
  })

  it('says a change is unknown, never "no change", when the series did not measure the previous day', () => {
    render(<MultiSeriesTip active label={day(2)} model={model3} hidden={new Set()} format={(v) => `${v.toFixed(1)}%`} />)
    const cart = document.querySelector('[data-tip-series="cart"] .chart-tooltip-detail')
    expect(cart?.textContent).toBe('(100 samples, previous day not measured)')
  })

  it('keeps a hostile series name literal in the tooltip, the readout and the legend', () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    const model = buildMultiSeriesModel({ series: [suite(hostile, [90, 91]), suite('b', [80, 81])], metric: RATE, seriesNoun: 'suites' })
    const { container } = mount(model)
    const surface = container.querySelector('[data-multi-series-plot]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'Home' })
    expect(container.querySelector('[data-chart-readout]')?.textContent).toContain(hostile)
    expect(container.querySelector('[data-multi-series-legend]')?.textContent).toContain(hostile)
    render(<MultiSeriesTip active label={day(0)} model={model} hidden={new Set()} format={(v) => `${v}`} />)
    expect(document.querySelector(`[data-chart-tooltip] [data-tip-series] .chart-tooltip-label`)?.textContent).toBe(hostile)
    expect(document.querySelector('img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })
})

describe('the legend: hide, isolate — announced, and reflected in the table', () => {
  it('a click hides a series: its line and its direct label go, the table marks it, and the announcer says so', () => {
    vi.useFakeTimers()
    const { container } = mount(model3)
    const cart = container.querySelector('[data-legend-series="cart"]') as HTMLButtonElement
    expect(cart).toHaveAttribute('aria-pressed', 'true')
    expect(cart).toHaveAccessibleDescription(LEGEND_HINT)

    fireEvent.click(cart)
    expect(cart).toHaveAttribute('aria-pressed', 'false')
    expect(lastLines().map((l) => l.name)).toEqual(['payments', 'search'])
    expect(directLabels(container).map((l) => l.key).sort()).toEqual(['payments', 'search'])

    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(container.querySelector('[data-chart-announcer="polite"]')?.textContent).toBe(
      'Pass rate by suite chart: cart hidden, 2 of 3 series shown',
    )

    // The table keeps cart, marked hidden, with its values.
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toContain(`cart${HIDDEN_SUFFIX}`)
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('90')
  })

  it('Shift+Enter from the keyboard shows one series alone; "Show all series" brings the rest back', () => {
    vi.useFakeTimers()
    const { container } = mount(model3)
    const search = container.querySelector('[data-legend-series="search"]') as HTMLButtonElement
    search.focus()
    fireEvent.keyDown(search, { key: 'Enter', shiftKey: true })
    expect(lastLines().map((l) => l.name)).toEqual(['search'])
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(container.querySelector('[data-chart-announcer="polite"]')?.textContent).toBe(
      'Pass rate by suite chart: only search shown, 1 of 3 series shown',
    )
    fireEvent.click(screen.getByRole('button', { name: SHOW_ALL_LABEL }))
    expect(lastLines().map((l) => l.name)).toEqual(['payments', 'cart', 'search'])
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(container.querySelector('[data-chart-announcer="polite"]')?.textContent).toBe(
      'Pass rate by suite chart: all 3 series shown',
    )
  })

  it('a series hidden from the start is hidden, marked and struck through — and its first draw is not announced', () => {
    vi.useFakeTimers()
    const { container } = mount(model3, { initialHidden: ['cart'] })
    expect(lastLines().map((l) => l.name)).toEqual(['payments', 'search'])
    const cart = container.querySelector('[data-legend-series="cart"]') as HTMLButtonElement
    expect(cart).toHaveAttribute('aria-pressed', 'false')
    expect(cart.querySelector('.line-through')).toHaveTextContent('cart')
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(container.querySelector('[data-chart-announcer="polite"]')?.textContent).toBe('')
  })
})

describe('the notices', () => {
  it('shows the not-comparable banner with its reason, and still draws the comparison', () => {
    const model = buildMultiSeriesModel({
      series: THREE,
      metric: RATE,
      comparability: { comparable: false, reason: 'R2 ran 3 suites R1 did not' },
    })
    const { container } = mount(model)
    expect(container.querySelector('[data-chart-comparable-banner]')).toHaveTextContent(
      'Not directly comparable: R2 ran 3 suites R1 did not. The comparison is still shown.',
    )
    expect(lastLines()).toHaveLength(3)
  })

  it('names how many series were folded into "Other"', () => {
    const twelve = Array.from({ length: 12 }, (_, i) => suite(`s${i}`, [10 + i], 100 - i))
    const { container } = mount(buildMultiSeriesModel({ series: twelve, metric: { kind: 'count', title: 'Executions' }, seriesNoun: 'suites' }))
    expect(container.querySelector('[data-chart-fold-notice]')).toHaveTextContent(
      '12 suites: the 7 with the most executions are drawn, and the other 5 are folded into "Other".',
    )
    expect(lastLines()).toHaveLength(8)
  })

  it('counts the gaps rather than drawing zeros', () => {
    const { container } = mount(model3)
    expect(container.querySelector('[data-chart-gap-note]')).toHaveTextContent('1 value is not measured and drawn as a gap, never as 0.')
  })

  it('an aligned comparison titles its axis and tables the relative day AND the absolute dates', () => {
    const r1 = { key: 'r1', label: 'R1', points: [0, 1].map((i) => ({ x: addUtcDays('2026-03-02', i), y: 90 + i, n: 9 })) }
    const r2 = { key: 'r2', label: 'R2', points: [0, 1].map((i) => ({ x: addUtcDays('2026-04-10', i), y: 80 + i, n: 9 })) }
    const model = buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start' })
    mount(model)
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader')[0]).toHaveTextContent(ALIGNED_X_TITLE)
    expect(within(table).getAllByRole('rowheader').map((th) => th.textContent)).toEqual([
      'Day 0 (R1 2026-03-02; R2 2026-04-10)',
      'Day 1 (R1 2026-03-03; R2 2026-04-11)',
    ])
  })
})
