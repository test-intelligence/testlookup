/**
 * VIZ-403 — the renderer. Every edge case the story lists is asserted on what
 * actually reaches Recharts, not on a screenshot: a gap must be a `null` in the
 * data AND `connectNulls={false}`, because either one alone still draws a line
 * straight through a day nobody ran anything.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChart, { TimeSeriesTooltip } from './TimeSeriesChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import {
  AXIS_NOT_ZERO_LABEL,
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  SVG_POINT_LIMIT,
  UTC_AXIS_CAPTION,
  buildTimeSeriesModel,
  timeSeriesFromTrends,
  type TimeSeriesPoint,
} from './timeSeriesModel'

interface Captured {
  chartData: unknown[]
  chart: Record<string, unknown> | null
  line: Record<string, unknown> | null
  bars: Record<string, unknown>[]
  cells: Record<string, unknown>[]
  axes: Record<string, unknown>[]
  referenceLines: Record<string, unknown>[]
  tooltips: Record<string, unknown>[]
}

const captured: Captured = {
  chartData: [],
  chart: null,
  line: null,
  bars: [],
  cells: [],
  axes: [],
  referenceLines: [],
  tooltips: [],
}

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children, data, ...props }: { children: ReactNode; data: unknown[] } & Record<string, unknown>) => {
    captured.chartData = data
    captured.chart = props
    return <div data-testid="composed-chart">{children}</div>
  },
  CartesianGrid: () => <div />,
  // The legend is RENDERED, not stubbed away: its entries are part of what a
  // reader who cannot tell the hues apart has to match the marks with.
  Legend: ({ content }: { content?: () => ReactNode }) => <div>{content ? content() : null}</div>,
  XAxis: () => <div />,
  YAxis: (props: Record<string, unknown>) => {
    captured.axes.push(props)
    return <div data-testid="y-axis" data-axis-id={String(props.yAxisId)} data-label={String(props.label)} />
  },
  Tooltip: (props: Record<string, unknown>) => {
    captured.tooltips.push(props)
    return <div />
  },
  Line: (props: Record<string, unknown>) => {
    captured.line = props
    return <div data-testid="rate-line" data-connect-nulls={String(props.connectNulls)} data-dot={String(!!props.dot)} />
  },
  Bar: ({ children, ...props }: { children?: ReactNode } & Record<string, unknown>) => {
    captured.bars.push(props)
    return <div data-testid="executions-bar">{children}</div>
  },
  Cell: (props: Record<string, unknown>) => {
    captured.cells.push(props)
    return <div data-testid="bar-cell" data-partial={String(props['data-partial'])} />
  },
  ReferenceLine: (props: Record<string, unknown>) => {
    captured.referenceLines.push(props)
    return <div data-testid="release-marker" data-x={String(props.x)} />
  },
}))

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

function reset() {
  captured.chartData = []
  captured.chart = null
  captured.line = null
  captured.bars = []
  captured.cells = []
  captured.axes = []
  captured.referenceLines = []
  captured.tooltips = []
}

const trends = [
  { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
  { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
  { date: '2026-03-03', passed: 8, failed: 2, skipped: 0, broken: 0, total: 10, pass_rate: 80 },
]

const model = (over: Partial<Parameters<typeof buildTimeSeriesModel>[0]> = {}) =>
  buildTimeSeriesModel({ points: timeSeriesFromTrends(trends), ...over })

describe('TimeSeriesChart — gaps', () => {
  it('draws a no-run day as a gap, not a zero', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    expect((captured.chartData as { rate: number | null }[]).map((row) => row.rate)).toEqual([90, null, 80])
    expect(screen.getByTestId('rate-line')).toHaveAttribute('data-connect-nulls', 'false')
  })

  it('keeps the genuine zero on the EXECUTIONS series — only the rate is unknown', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    expect((captured.chartData as { executions: number | null }[]).map((row) => row.executions)).toEqual([10, 0, 10])
  })
})

describe('TimeSeriesChart — a single data point', () => {
  it('renders a dot and marks itself as a single point, so no line is implied', () => {
    reset()
    render(<TimeSeriesChart model={buildTimeSeriesModel({ points: timeSeriesFromTrends([trends[0]]) })} />)
    expect(screen.getByTestId('rate-line')).toHaveAttribute('data-dot', 'true')
    expect(screen.getByRole('figure')).toHaveAttribute('data-single-point', 'true')
  })

  it('shows dots for an isolated point between two gaps, which would otherwise be invisible', () => {
    reset()
    render(
      <TimeSeriesChart
        model={buildTimeSeriesModel({
          points: timeSeriesFromTrends([
            { date: '2026-03-01', passed: 0, failed: 0, skipped: 4, broken: 0, total: 4, pass_rate: 0 },
            trends[0],
            { date: '2026-03-03', passed: 0, failed: 0, skipped: 4, broken: 0, total: 4, pass_rate: 0 },
          ]),
        })}
      />,
    )
    expect(screen.getByTestId('rate-line')).toHaveAttribute('data-dot', 'true')
  })
})

describe('TimeSeriesChart — the rate axis', () => {
  it('gives the second axis its own title', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    const labels = captured.axes.map((axis) => axis.label as { value?: string } | undefined)
    expect(labels.some((label) => label?.value === RATE_AXIS_TITLE)).toBe(true)
    expect(labels.some((label) => label?.value === EXECUTIONS_AXIS_TITLE)).toBe(true)
  })

  it('shows no zero-baseline indicator on a 0–100 axis', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    expect(screen.queryByText(AXIS_NOT_ZERO_LABEL)).toBeNull()
  })

  it('SAYS the axis does not start at 0 as soon as it is zoomed', () => {
    reset()
    render(<TimeSeriesChart model={model({ points: timeSeriesFromTrends(trends), zoomRateAxis: true })} />)
    expect(screen.getByText(AXIS_NOT_ZERO_LABEL)).toBeInTheDocument()
    const rateAxis = captured.axes.find((axis) => axis.yAxisId === 'rate')
    expect((rateAxis?.domain as number[])[0]).toBeGreaterThan(0)
  })
})

describe('TimeSeriesChart — release markers', () => {
  it('draws a vertical marker on the release bucket, named', () => {
    reset()
    render(
      <TimeSeriesChart
        model={model({
          points: timeSeriesFromTrends(trends),
          releases: [{ id: 'r1', name: '1.4.0', date: '2026-03-02T00:00:00Z' }],
        })}
      />,
    )
    const markers = screen.getAllByTestId('release-marker')
    expect(markers).toHaveLength(1)
    expect(markers[0]).toHaveAttribute('data-x', '2026-03-02')
    expect((captured.referenceLines[0].label as { value?: string }).value).toBe('1.4.0')
  })

  it('reports releases that fell outside the window rather than dropping them silently', () => {
    reset()
    render(
      <TimeSeriesChart
        model={model({
          points: timeSeriesFromTrends(trends),
          releases: [{ id: 'r0', name: '1.0.0', date: '2025-12-01T00:00:00Z' }],
        })}
      />,
    )
    expect(screen.queryAllByTestId('release-marker')).toHaveLength(0)
    expect(screen.getByText(/1 release.*outside/i)).toBeInTheDocument()
  })
})

describe('TimeSeriesChart — the partial UTC day', () => {
  it('draws the current UTC day partial and names it', () => {
    reset()
    render(
      <TimeSeriesChart
        model={model({
          points: timeSeriesFromTrends(trends),
          meta: { partial_day: '2026-03-03', includes_in_progress: 2 } as never,
        })}
      />,
    )
    expect(screen.getByRole('figure')).toHaveAttribute('data-partial-day', '2026-03-03')
    const partialCells = captured.cells.filter((cell) => cell['data-partial'] === true)
    expect(partialCells).toHaveLength(1)
    // Hatched AND dashed: a colour-only difference is not a difference.
    expect(partialCells[0].strokeDasharray).toBeTruthy()
    expect(String(partialCells[0].fill)).toContain('url(#')
  })

  it('draws every other day solid', () => {
    reset()
    render(
      <TimeSeriesChart
        model={model({
          points: timeSeriesFromTrends(trends),
          meta: { partial_day: '2026-03-03', includes_in_progress: 2 } as never,
        })}
      />,
    )
    expect(captured.cells.filter((cell) => cell['data-partial'] !== true)).toHaveLength(2)
  })
})

describe('TimeSeriesChart — UTC buckets vs the viewer', () => {
  it('captions the axis as UTC', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    expect(screen.getByText(UTC_AXIS_CAPTION)).toBeInTheDocument()
  })

  it('explains an empty UTC "today" to a viewer at UTC+13', () => {
    reset()
    const points: TimeSeriesPoint[] = [
      { x: '2026-03-04', rate: 91, rateReason: null, executions: 10, n: 10, partial: false },
      { x: '2026-03-05', rate: null, rateReason: 'no evaluated executions', executions: 0, n: 0, partial: false },
    ]
    render(
      <TimeSeriesChart
        model={buildTimeSeriesModel({ points })}
        now={new Date('2026-03-05T12:00:00Z')}
        timeZone="Pacific/Auckland"
      />,
    )
    expect(screen.getByText(/local date is already 2026-03-06/)).toBeInTheDocument()
  })
})

describe('TimeSeriesChart — renderer choice', () => {
  it('stays in SVG at the limit', () => {
    reset()
    const points: TimeSeriesPoint[] = Array.from({ length: SVG_POINT_LIMIT }, (_, i) => ({
      x: `2026-${String((i % 12) + 1).padStart(2, '0')}-01`,
      rate: 90,
      rateReason: null,
      executions: 1,
      n: 1,
      partial: false,
    }))
    render(<TimeSeriesChart model={buildTimeSeriesModel({ points })} />)
    expect(screen.getByRole('figure')).toHaveAttribute('data-renderer', 'svg')
    expect(screen.getByTestId('composed-chart')).toBeInTheDocument()
  })

  it('hands a longer series to the ECharts renderer', () => {
    reset()
    const points: TimeSeriesPoint[] = Array.from({ length: SVG_POINT_LIMIT + 1 }, (_, i) => ({
      x: `p${i}`,
      rate: 90,
      rateReason: null,
      executions: 1,
      n: 1,
      partial: false,
    }))
    render(<TimeSeriesChart model={buildTimeSeriesModel({ points })} />)
    expect(screen.getByRole('figure')).toHaveAttribute('data-renderer', 'echarts')
    expect(screen.queryByTestId('composed-chart')).toBeNull()
    expect(screen.getByTestId('time-series-canvas')).toBeInTheDocument()
  })
})

describe('TimeSeriesTooltip', () => {
  const built = buildTimeSeriesModel({
    points: timeSeriesFromTrends(trends),
    meta: { partial_day: '2026-03-03', includes_in_progress: 1 } as never,
    releases: [{ id: 'r1', name: '1.4.0', date: '2026-03-02T00:00:00Z' }],
  })

  it('states the UTC day and adds the local equivalent', () => {
    render(
      <TimeSeriesTooltip
        active
        label="2026-03-01"
        model={built}
        timeZone="Pacific/Auckland"
        locale="en-US"
      />,
    )
    expect(screen.getByText(/2026-03-01 \(UTC\)/)).toBeInTheDocument()
    expect(screen.getByText(/Mar 1/)).toBeInTheDocument()
  })

  it('shows a gap as "—" with the reason, never 0%', () => {
    render(<TimeSeriesTooltip active label="2026-03-02" model={built} timeZone="UTC" locale="en-US" />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).toBeNull()
    expect(screen.getByText(/no evaluated executions/i)).toBeInTheDocument()
  })

  it('names the in-progress runs on the partial day', () => {
    render(
      <TimeSeriesTooltip
        active
        label="2026-03-03"
        model={built}
        timeZone="UTC"
        locale="en-US"
        inProgressRuns={[{ x: '2026-03-03', names: ['nightly run 412'] }]}
      />,
    )
    expect(screen.getByText(/nightly run 412/)).toBeInTheDocument()
  })

  it('names the release on a marked day', () => {
    render(<TimeSeriesTooltip active label="2026-03-02" model={built} timeZone="UTC" locale="en-US" />)
    expect(screen.getByText('1.4.0')).toBeInTheDocument()
  })

  it('renders nothing when it is not active', () => {
    const { container } = render(<TimeSeriesTooltip label="2026-03-01" model={built} />)
    expect(container).toBeEmptyDOMElement()
  })
})

// ── fix round B ──────────────────────────────────────────────────────────────

const partialModel = () =>
  model({
    points: timeSeriesFromTrends(trends),
    meta: { partial_day: '2026-03-03', includes_in_progress: 2 } as never,
  })

const withAnnouncer = (node: ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)
const announced = () => document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''

describe('fix round B · 2 the keyboard cursor', () => {
  it('names a focusable surface instead of handing Recharts an unnamed application', () => {
    reset()
    render(<TimeSeriesChart model={model()} title="Pass rate, last 3 days" />)
    // Recharts 3 defaults `accessibilityLayer` to TRUE, which puts an UNNAMED
    // `role="application"` on the surface and takes the virtual cursor away.
    expect(captured.chart?.accessibilityLayer).toBe(false)
    const surface = document.querySelector('[data-chart-cursor]') as HTMLElement
    expect(surface).not.toBeNull()
    expect(surface.getAttribute('role')).toBe('group')
    expect(surface.getAttribute('tabindex')).toBe('0')
    expect(surface.getAttribute('aria-label')).toMatch(/Pass rate, last 3 days/)
    expect(surface.getAttribute('aria-label')).toMatch(/arrow keys/i)
    expect(document.querySelectorAll('[role="application"]')).toHaveLength(0)
  })

  it('reads a bucket through the page announcer and dismisses on Escape', () => {
    reset()
    withAnnouncer(<TimeSeriesChart model={model()} title="Pass rate" timeZone="UTC" locale="en-US" />)
    const surface = document.querySelector('[data-chart-cursor]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(surface.getAttribute('data-chart-cursor')).toBe('active')
    expect(announced()).toMatch(/2026-03-01/)
    // The same words a sighted keyboard user can see.
    expect(document.querySelector('[data-chart-readout]')?.textContent).toMatch(/2026-03-01/)

    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(surface.getAttribute('data-chart-cursor')).toBe('idle')
    expect(document.querySelector('[data-chart-readout]')).toBeNull()
    // …and the hover tooltip is dismissed with it (SC 1.4.13).
    expect(captured.tooltips[captured.tooltips.length - 1]?.active).toBe(false)
  })
})

describe('fix round B · 4 the executions bars are a data-carrying object', () => {
  it('draws an ordinary day at full opacity, not at 0.45', () => {
    reset()
    render(<TimeSeriesChart model={partialModel()} />)
    const solid = captured.cells.filter((cell) => cell['data-partial'] !== true)
    expect(solid).toHaveLength(2)
    for (const cell of solid) {
      expect(cell.fillOpacity ?? 1, 'an ordinary execution bar is a measurement, not a wash').toBe(1)
      expect(String(cell.fill)).not.toContain('url(#')
    }
  })

  it('keeps the still-filling day distinguishable by PATTERN, never by being the brightest bar', () => {
    reset()
    render(<TimeSeriesChart model={partialModel()} />)
    const partial = captured.cells.filter((cell) => cell['data-partial'] === true)
    expect(partial).toHaveLength(1)
    // A hatch tile is mostly card colour, so the partial bar carries LESS ink
    // than a solid one — which is what "still filling" should look like.
    expect(String(partial[0].fill)).toContain('url(#')
    expect(partial[0].strokeDasharray).toBeTruthy()
  })
})

describe('fix round B · 6 the partial-day note needs a DRAWN partial bucket', () => {
  it('says nothing when meta names a day the series does not carry', () => {
    reset()
    // `duration_p50`/`p95` are zero_fill=False, so a day with nothing to report
    // is simply absent — including the day the envelope calls partial.
    const absent = model({
      points: timeSeriesFromTrends(trends),
      meta: { partial_day: '2026-03-09', includes_in_progress: 4 } as never,
    })
    render(<TimeSeriesChart model={absent} />)
    expect(absent.points.some((point) => point.partial)).toBe(false)
    expect(document.querySelector('[data-chart-partial-note]')).toBeNull()
    expect(screen.getByRole('figure')).not.toHaveAttribute('data-partial-day')
  })

  it('still says it when the partial bucket IS drawn', () => {
    reset()
    render(<TimeSeriesChart model={partialModel()} />)
    expect(document.querySelector('[data-chart-partial-note]')).not.toBeNull()
    expect(screen.getByRole('figure')).toHaveAttribute('data-partial-day', '2026-03-03')
  })
})

describe('fix round B · 8 the partial-day hatch is in the legend', () => {
  it('names the hatch when a partial bucket is drawn', () => {
    reset()
    render(<TimeSeriesChart model={partialModel()} />)
    const legend = document.querySelector('[data-chart-legend]') as HTMLElement
    expect(legend.textContent).toMatch(/still filling/i)
    const entry = Array.from(legend.querySelectorAll('li')).find((li) => /still filling/i.test(li.textContent ?? ''))
    // The swatch is drawn with the SAME pattern as the bar it names.
    expect(String(entry?.querySelector('[data-legend-swatch]')?.getAttribute('fill'))).toContain('url(#')
  })

  it('leaves it out when no bucket is partial', () => {
    reset()
    render(<TimeSeriesChart model={model()} />)
    expect(document.querySelector('[data-chart-legend]')?.textContent).not.toMatch(/still filling/i)
  })
})

describe('fix round B · 7 a release nobody could place is still counted', () => {
  it('counts an unreadable release date in the "not shown" note', () => {
    reset()
    render(
      <TimeSeriesChart
        model={model({
          points: timeSeriesFromTrends(trends),
          releases: [{ id: 'r0', name: 'broken', date: 'not-a-date' }],
        })}
      />,
    )
    expect(screen.queryAllByTestId('release-marker')).toHaveLength(0)
    expect(document.querySelector('[data-chart-markers-outside]')?.textContent).toMatch(/1 release/i)
  })
})
