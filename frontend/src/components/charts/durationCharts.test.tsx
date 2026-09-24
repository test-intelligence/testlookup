/**
 * VIZ-406 — the duration renderers. The histogram's edges are LABELLED (a log
 * axis a reader cannot read the edges of is a shape, not a measurement), the
 * overflow bucket is visibly distinct, the excluded count is stated in the
 * chart's own text, and the p50/p95 band never silently reorders itself.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { cloneElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import DurationHistogram, { HISTOGRAM_BUCKETS_CAPTION, OVERFLOW_NOTE } from './DurationHistogram'
import DurationTrend, { INVERTED_NOTE, durationAxis, durationTrendTipContent } from './DurationTrend'
import { sliceDurationBand } from './zoom/zoomModel'
import { tooltipText } from './tooltip'
import { readTooltip } from './tooltipTestUtils'
import SlowestTests from './SlowestTests'
import DurationChartFrame from './DurationChartFrame'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_MESSAGES } from './chartMessages'
import { buildDurationHistogram, durationBandPoints, rankSlowestTests } from './durationBuckets'

interface Captured {
  chartData: unknown[]
  chart: Record<string, unknown> | null
  cells: Record<string, unknown>[]
  marks: Record<string, unknown>[]
  axes: Record<string, unknown>[]
  tooltips: Record<string, unknown>[]
}
const captured: Captured = { chartData: [], chart: null, cells: [], marks: [], axes: [], tooltips: [] }

vi.mock('recharts', () => {
  const chart = ({ children, data, ...props }: { children?: ReactNode; data?: unknown[] } & Record<string, unknown>) => {
    if (data) captured.chartData = data
    captured.chart = props
    return <div data-testid="chart">{children}</div>
  }
  const mark = (type: string) => (props: Record<string, unknown> & { children?: ReactNode }) => {
    captured.marks.push({ ...props, __type: type })
    return <div data-testid={`mark-${type}`} data-key={String(props.dataKey)}>{props.children as ReactNode}</div>
  }
  const axis = (which: string) => (props: Record<string, unknown>) => {
    captured.axes.push({ ...props, __axis: which })
    return <div data-testid={`axis-${which}`} />
  }
  return {
    // The plot area the pinned tooltips read their column from (VIZ-601).
    usePlotArea: () => ({ x: 40, y: 8, width: 400, height: 200 }),
    ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    BarChart: chart,
    ComposedChart: chart,
    CartesianGrid: () => <div />,
    // Rendered, not stubbed away: a legend entry is half of "never colour-only".
    Legend: ({ content }: { content?: () => ReactNode }) => <div>{content ? content() : null}</div>,
    XAxis: axis('x'),
    YAxis: axis('y'),
    Tooltip: (props: Record<string, unknown>) => {
      captured.tooltips.push(props)
      return <div />
    },
    Bar: mark('bar'),
    Line: mark('line'),
    Area: mark('area'),
    Cell: (props: Record<string, unknown>) => {
      captured.cells.push(props)
      return <div data-testid="cell" data-overflow={String(props['data-overflow'])} />
    },
  }
})

function reset() {
  captured.chartData = []
  captured.chart = null
  captured.cells = []
  captured.marks = []
  captured.axes = []
  captured.tooltips = []
}

const series = (points: SeriesChart['series'][number]['points']): SeriesChart => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'duration', label: 'duration', points }],
})

const meta = { measured: true, reason: null } as unknown as EnvelopeMeta

// ── Histogram ────────────────────────────────────────────────────────────────

describe('DurationHistogram', () => {
  const durations = [0.4, 3, 12, 12, 40, 99, 900, 3_600_000, null, 0, 0]

  it('labels every bucket edge, so the log axis can be read as durations', () => {
    reset()
    render(<DurationHistogram model={buildDurationHistogram(durations)} />)
    const labels = (captured.chartData as { label: string }[]).map((row) => row.label)
    expect(labels.length).toBeGreaterThan(1)
    for (const label of labels) expect(label).toMatch(/ms|s|m |h /)
    expect(labels[0]).toContain('–')
  })

  it('marks the overflow bucket so one outlier is visible, not silently merged', () => {
    reset()
    render(<DurationHistogram model={buildDurationHistogram(durations)} />)
    // A STRING, not a boolean: `overflow` is an SVG presentation attribute, so
    // the flag reaches the DOM as `data-overflow="true"` rather than as a
    // boolean React would refuse to write.
    const overflowCells = captured.cells.filter((cell) => cell['data-overflow'] === 'true')
    expect(overflowCells).toHaveLength(1)
    const labels = (captured.chartData as { label: string }[]).map((row) => row.label)
    expect(labels[labels.length - 1].startsWith('≥')).toBe(true)
  })

  it('states the executions it could not place', () => {
    reset()
    render(<DurationHistogram model={buildDurationHistogram(durations)} />)
    // one null + two zeros
    expect(screen.getByText('3 executions without duration, including 2 recorded as 0ms')).toBeInTheDocument()
  })

  it('says nothing about exclusions when there were none', () => {
    reset()
    render(<DurationHistogram model={buildDurationHistogram([1, 2, 3])} />)
    expect(screen.queryByText(/without duration/)).toBeNull()
  })

  it('draws no bars at all when nothing carries a duration, and says why', () => {
    reset()
    render(<DurationHistogram model={buildDurationHistogram([null, null, 0])} />)
    expect(screen.queryByTestId('mark-bar')).toBeNull()
    expect(screen.getByText(/no execution.*carries a duration/i)).toBeInTheDocument()
    expect(screen.getByText(/3 executions without duration/)).toBeInTheDocument()
  })
})

// ── p50 / p95 band ───────────────────────────────────────────────────────────

describe('DurationTrend', () => {
  const healthy = durationBandPoints({
    p50: series([
      { x: '2026-03-01', y: 100, n: 10 },
      { x: '2026-03-02', y: 120, n: 10 },
    ]),
    p95: series([
      { x: '2026-03-01', y: 400, n: 10 },
      { x: '2026-03-02', y: 500, n: 10 },
    ]),
  })

  it('draws two lines with a shaded band between them', () => {
    reset()
    render(<DurationTrend model={healthy} />)
    const keys = captured.marks.map((mark) => mark.dataKey)
    expect(keys).toContain('p50')
    expect(keys).toContain('p95')
    expect(captured.marks.some((mark) => mark.__type === 'area' && mark.dataKey === 'band')).toBe(true)
    expect((captured.chartData as { band: number[] }[])[0].band).toEqual([100, 400])
  })

  it('leaves a gap in the band and both lines when a percentile is unmeasured', () => {
    reset()
    render(
      <DurationTrend
        model={durationBandPoints({
          p50: series([{ x: '2026-03-01', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' }]),
          p95: series([{ x: '2026-03-01', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' }]),
        })}
      />,
    )
    const row = (captured.chartData as { p50: number | null; band: number[] | null }[])[0]
    expect(row.p50).toBeNull()
    expect(row.band).toBeNull()
  })

  it('draws an inverted band as reported AND says the percentiles disagree', () => {
    reset()
    render(
      <DurationTrend
        model={durationBandPoints({
          p50: series([{ x: '2026-03-01', y: 400, n: 10 }]),
          p95: series([{ x: '2026-03-01', y: 100, n: 10 }]),
        })}
      />,
    )
    const row = (captured.chartData as { p50: number; p95: number; band: number[] }[])[0]
    expect(row.p50).toBe(400)
    expect(row.p95).toBe(100)
    expect(row.band).toEqual([100, 400])
    expect(screen.getByText(/p95 was below p50 on 1 day/)).toBeInTheDocument()
  })

  it('says nothing when the percentiles are consistent', () => {
    reset()
    render(<DurationTrend model={healthy} />)
    expect(screen.queryByText(/p95 was below p50/)).toBeNull()
  })
})

// ── Slowest tests ────────────────────────────────────────────────────────────

describe('SlowestTests', () => {
  const rows = [
    { name: 'checkout pays with a saved card', p95: 3_600_000, runs: 12 },
    { name: 'cart adds an item', p95: 900, runs: 40 },
    { name: 'search finds nothing', p95: null, runs: 3 },
  ]

  it('ranks the slowest first, with each test’s run count', () => {
    render(<SlowestTests model={rankSlowestTests(rows)} />)
    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).getByText(/checkout pays/)).toBeInTheDocument()
    expect(within(items[0]).getByText('1h 0m')).toBeInTheDocument()
    expect(within(items[0]).getByText(/12 runs/)).toBeInTheDocument()
  })

  it('shows an unmeasured p95 as "—", never as the fastest test', () => {
    render(<SlowestTests model={rankSlowestTests(rows)} />)
    const items = screen.getAllByRole('listitem')
    expect(within(items[items.length - 1]).getByText(/search finds nothing/)).toBeInTheDocument()
    expect(within(items[items.length - 1]).getByText('—')).toBeInTheDocument()
  })

  it('states that it is showing the top 20 of more', () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ name: `t${i}`, p95: i + 1, runs: 1 }))
    render(<SlowestTests model={rankSlowestTests(many)} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(20)
    expect(screen.getByText(/20 of 50/)).toBeInTheDocument()
  })

  it('renders its own ranked bars rather than depending on a BarChart that may not exist', () => {
    render(<SlowestTests model={rankSlowestTests(rows)} />)
    expect(screen.getAllByTestId('ranked-bar').length).toBe(3)
  })
})

// ── Inside the frame ─────────────────────────────────────────────────────────

describe('DurationChartFrame', () => {
  it('gives the frame a table built from the same buckets the histogram draws', () => {
    reset()
    render(
      <DurationChartFrame
        kind="histogram"
        title="Duration distribution"
        headingLevel={3}
        histogram={buildDurationHistogram([0.4, 3, 12, 3_600_000])}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: /data table/i })
    expect(table).toHaveTextContent('0.2ms – 0.5ms')
  })

  it('shows a not-measured percentile trend as "—" with the reason, never 0 ms', () => {
    reset()
    render(
      <DurationChartFrame
        kind="trend"
        title="p50 / p95"
        headingLevel={3}
        band={durationBandPoints({})}
        state={{ status: 'not-measured', reason: 'percentiles need at least one timed execution', meta: null }}
      />,
    )
    expect(screen.getByText(CHART_MESSAGES.notMeasured)).toBeInTheDocument()
    expect(screen.getByText('percentiles need at least one timed execution')).toBeInTheDocument()
    expect(screen.queryByTestId('mark-line')).toBeNull()
  })

  it('puts the slowest tests in the frame’s table with their run counts', () => {
    reset()
    render(
      <DurationChartFrame
        kind="slowest"
        title="Slowest tests"
        headingLevel={3}
        slowest={rankSlowestTests([{ name: 'cart adds an item', p95: 900, runs: 40 }])}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: /data table/i })
    expect(table).toHaveTextContent('cart adds an item')
    expect(table).toHaveTextContent('900')
  })
})

// ── fix round B ──────────────────────────────────────────────────────────────

const withAnnouncer = (node: ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)
const announced = () => document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''

describe('fix round B · 1 the slowest-tests frame formats each series in its OWN unit', () => {
  const slowest = rankSlowestTests([{ name: 'checkout.spec', p95: 42_000, runs: 10 }])

  const renderFrame = () =>
    render(
      <DurationChartFrame
        kind="slowest"
        title="Slowest tests"
        headingLevel={3}
        slowest={slowest}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )

  it('prints the run count as a count, never as a duration', () => {
    reset()
    renderFrame()
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const row = screen.getByRole('row', { name: /checkout\.spec/ })
    // One ValueFormatter for the whole chart turns "10 runs" into "10ms".
    expect(row.textContent).not.toMatch(/10ms/)
    // The cells, not the concatenated row text: "42.0s" and "10" run together.
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent)
    expect(cells).toContain('10')
    // …and p95 is still a duration.
    expect(cells).toContain('42.0s')
  })

  it('formats the generated summary per series too, not only the table', () => {
    reset()
    renderFrame()
    const summary = document.querySelector('[data-chart-summary]')?.textContent ?? ''
    expect(summary).toMatch(/Runs: min 10 \(checkout\.spec\)/)
    expect(summary).not.toMatch(/10ms/)
  })
})

describe('fix round B · 2 the duration charts carry a keyboard cursor', () => {
  it('gives the histogram a named focusable surface, not an unnamed application', () => {
    reset()
    withAnnouncer(
      <DurationHistogram model={buildDurationHistogram([1, 2, 5, 40])} title="Duration distribution" />,
    )
    expect(captured.chart?.accessibilityLayer).toBe(false)
    const surface = document.querySelector('[data-chart-cursor]') as HTMLElement
    expect(surface.getAttribute('role')).toBe('group')
    expect(surface.getAttribute('tabindex')).toBe('0')
    expect(surface.getAttribute('aria-label')).toMatch(/Duration distribution/)
    expect(surface.getAttribute('aria-label')).toMatch(/arrow keys/i)
    expect(document.querySelectorAll('[role="application"]')).toHaveLength(0)

    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(surface.getAttribute('data-chart-cursor')).toBe('active')
    expect(announced()).toMatch(/execution/i)
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(surface.getAttribute('data-chart-cursor')).toBe('idle')
    expect(captured.tooltips[captured.tooltips.length - 1]?.active).toBe(false)
  })

  it('gives the p50/p95 trend one too, reading both percentiles of a day', () => {
    reset()
    withAnnouncer(
      <DurationTrend
        title="Duration trend"
        model={durationBandPoints({
          p50: series([{ x: '2026-03-01', y: 100, n: 10 }]),
          p95: series([{ x: '2026-03-01', y: 400, n: 10 }]),
        })}
      />,
    )
    expect(captured.chart?.accessibilityLayer).toBe(false)
    const surface = document.querySelector('[data-chart-cursor]') as HTMLElement
    expect(surface.getAttribute('aria-label')).toMatch(/Duration trend/)
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const spoken = announced()
    expect(spoken).toMatch(/2026-03-01/)
    // VIZ-601: "Label: value", and n — the sample behind both percentiles.
    expect(spoken).toMatch(/p50: 100ms/)
    expect(spoken).toMatch(/p95: 400ms/)
    expect(spoken).toMatch(/Samples: 10/)
    expect(document.querySelectorAll('[role="application"]')).toHaveLength(0)
  })
})

describe('DurationTrend · yMax keeps a zoomed slice on the whole window’s scale', () => {
  const band = durationBandPoints({
    p50: series([{ x: '2026-03-01', y: 100, n: 10 }]),
    p95: series([{ x: '2026-03-01', y: 400, n: 10 }]),
  })
  const yAxis = () => {
    const axis = captured.axes.filter((a) => a.__axis === 'y').pop()
    return { domain: axis?.domain, ticks: axis?.ticks }
  }

  it('draws the axis from zero to a NICE top over yMax when given, and over the drawn data when not', () => {
    reset()
    render(<DurationTrend model={band} title="Duration trend" yMax={2500} />)
    expect(yAxis()).toEqual({ domain: [0, 3000], ticks: [0, 1000, 2000, 3000] })
    reset()
    render(<DurationTrend model={band} title="Duration trend" />)
    expect(yAxis()).toEqual({ domain: [0, 400], ticks: [0, 100, 200, 300, 400] })
    // yMax of 0 is no maximum: the data's own.
    reset()
    render(<DurationTrend model={band} title="Duration trend" yMax={0} />)
    expect(yAxis()).toEqual({ domain: [0, 400], ticks: [0, 100, 200, 300, 400] })
  })

  it('a zoomed slice given the window’s NON-ROUND maximum has exactly the unzoomed axis (Wave 2.4 F6)', () => {
    // The whole band tops out at 1,873 ms on day 2; the slice (days 3-4) at 600.
    const whole = durationBandPoints({
      p50: series(['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04'].map((x) => ({ x, y: 300, n: 10 }))),
      p95: series(['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04'].map((x, i) => ({ x, y: i === 1 ? 1873 : 600, n: 10 }))),
    })
    reset()
    render(<DurationTrend model={whole} title="Duration trend" />)
    const unzoomed = yAxis()
    // A nice top ABOVE the maximum, never the maximum itself.
    expect(unzoomed).toEqual({ domain: [0, 2000], ticks: [0, 500, 1000, 1500, 2000] })
    reset()
    render(<DurationTrend model={{ ...whole, points: whole.points.slice(2) }} title="Duration trend" yMax={1873} />)
    expect(yAxis()).toEqual(unzoomed)
    expect(durationAxis(undefined)).toBeNull()
    expect(durationAxis(0)).toBeNull()
  })
})

describe('VIZ-601 · the duration tooltips', () => {
  const tipAt = (index: number) => captured.tooltips[index].content as ReactElement<Record<string, unknown>>
  const read = (node: ReactElement) => {
    const { container, unmount } = render(node)
    const content = readTooltip(container.querySelector('[data-chart-tooltip]') as HTMLElement)
    unmount()
    return content
  }

  const band = durationBandPoints({
    p50: series([
      { x: '2026-03-01', y: 100, n: 10 },
      { x: '2026-03-02', y: 340, n: 12 },
      { x: '2026-03-03', y: 300, n: 12 },
    ]),
    p95: series([
      { x: '2026-03-01', y: 400, n: 10 },
      { x: '2026-03-02', y: 900, n: 11 },
      { x: '2026-03-03', y: 250, n: 12 },
    ]),
  })

  it('p50/p95: both values, n (both when the two samples differ), each change vs the previous day', () => {
    reset()
    render(<DurationTrend model={band} title="Duration trend" />)
    const content = read(cloneElement(tipAt(captured.tooltips.length - 1), { active: true, label: '2026-03-02' }))
    expect(content.rows).toEqual([
      { kind: 'value', label: 'p50', value: '340ms' },
      { kind: 'value', label: 'p95', value: '900ms' },
      { kind: 'sample', label: 'Samples', value: '12 (p50), 11 (p95)' },
      { kind: 'change', label: 'p50 change vs previous day', value: '+240ms' },
      { kind: 'change', label: 'p95 change vs previous day', value: '+500ms' },
    ])
    // An inverted day says so, as a note after the numbers.
    const inverted = read(cloneElement(tipAt(captured.tooltips.length - 1), { active: true, label: '2026-03-03' }))
    expect(inverted.rows[inverted.rows.length - 1]).toEqual({ kind: 'note', label: '', value: INVERTED_NOTE })
    expect(inverted.rows).toContainEqual({ kind: 'change', label: 'p95 change vs previous day', value: '−650ms' })
  })

  it('a ZOOMED band’s first day states its change vs the day before the view (Wave 2.4 F4)', () => {
    const zoomed = sliceDurationBand(band, { start: 1, end: 2 })
    // 2026-03-02 is the first day in view: the same change rows as unzoomed.
    const first = durationTrendTipContent(zoomed, 0)
    expect(first.title).toBe('2026-03-02')
    expect(first.rows).toContainEqual({ kind: 'change', key: 'change:p50 change vs previous day', label: 'p50 change vs previous day', value: '+240ms' })
    expect(first.rows).toContainEqual({ kind: 'change', key: 'change:p95 change vs previous day', label: 'p95 change vs previous day', value: '+500ms' })
    // From the first day of the data there is no previous day, zoomed or not.
    expect(durationTrendTipContent(sliceDurationBand(band, { start: 0, end: 1 }), 0).rows.some((row) => row.kind === 'change')).toBe(false)
  })

  it('histogram: the count, n (the executions placed) and its share of them; an open-ended bucket says so', () => {
    reset()
    const model = buildDurationHistogram([1, 2, 5, 40, 50_000_000])
    render(<DurationHistogram model={model} title="Duration distribution" />)
    const last = model.buckets[model.buckets.length - 1]
    const content = read(cloneElement(tipAt(captured.tooltips.length - 1), { active: true, label: last.label }))
    expect(content.title).toBe(last.label)
    expect(content.rows[0]).toEqual({ kind: 'value', label: 'Executions', value: String(last.count) })
    expect(content.rows).toContainEqual({ kind: 'sample', label: 'Samples', value: '5', detail: 'executions placed' })
    // Its share of the executions PLACED (1 of 5), not of everything the window held.
    expect(content.rows).toContainEqual({ kind: 'share', label: 'Share of total', value: `${((last.count / model.counted) * 100).toFixed(1)}%` })
    expect(last.count / model.counted).toBe(0.2)
    expect(last.overflow).toBe(true)
    expect(content.rows).toContainEqual({ kind: 'note', label: '', value: OVERFLOW_NOTE })
    // …and an ordinary bucket does not.
    const first = read(cloneElement(tipAt(captured.tooltips.length - 1), { active: true, label: model.buckets[0].label }))
    expect(first.rows.some((row) => row.kind === 'note')).toBe(false)
  })

  it('pointer and keyboard read the SAME content, bucket by bucket and day by day', () => {
    for (const which of ['histogram', 'trend'] as const) {
      reset()
      const model = buildDurationHistogram([1, 2, 5, 40])
      const { container, unmount } = withAnnouncer(
        which === 'histogram' ? (
          <DurationHistogram model={model} title="Chart" />
        ) : (
          <DurationTrend model={band} title="Chart" />
        ),
      )
      const surface = container.querySelector('[data-chart-cursor]') as HTMLElement
      const labels = which === 'histogram' ? model.buckets.map((bucket) => bucket.label) : band.points.map((point) => point.x)
      const content = tipAt(captured.tooltips.length - 1)
      labels.forEach((label, index) => {
        fireEvent.keyDown(surface, { key: index === 0 ? 'Home' : 'ArrowRight' })
        const pointed = read(cloneElement(content, { active: true, label }))
        expect(announced()).toBe(`Chart: ${tooltipText(pointed)}`)
        expect(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement)).toEqual(pointed)
      })
      unmount()
    }
  })
})

describe('fix round B · 3 the p50-p95 band is not colour-only', () => {
  const band = durationBandPoints({
    p50: series([{ x: '2026-03-01', y: 100, n: 10 }]),
    p95: series([{ x: '2026-03-01', y: 400, n: 10 }]),
  })

  it('fills the band with a pattern and outlines it, so it reads at 3:1', () => {
    reset()
    render(<DurationTrend model={band} title="Duration trend" />)
    const area = captured.marks.find((mark) => mark.__type === 'area' && mark.dataKey === 'band')
    // An 18 %-opacity wash of series-5 measures 1.19:1 against the card.
    expect(String(area?.fill)).toContain('url(#')
    expect(area?.stroke).toBeTruthy()
    expect(area?.stroke).not.toBe('none')
    expect(area?.fillOpacity ?? 1).toBe(1)
  })

  it('draws the band edges and both lines with ONE interpolation, straight, so a line never leaves the band', () => {
    // The band is [min, max] per day. Straight segments keep each line inside
    // it between any two days — even across an inverted day, where the lines
    // cross — because every point of a segment is the same mix of its ends.
    // A curve (monotone) on the lines and straight edges on the band let the
    // lines bulge out of it around the inverted day (the first baselines).
    reset()
    render(<DurationTrend model={band} title="Duration trend" />)
    const curves = captured.marks
      .filter((mark) => mark.__type === 'line' || (mark.__type === 'area' && mark.dataKey === 'band'))
      .map((mark) => ({ key: mark.dataKey, type: mark.type ?? 'linear (Recharts default)' }))
    expect(curves.map((curve) => curve.key).sort()).toEqual(['band', 'p50', 'p95'])
    expect(curves).toEqual(curves.map((curve) => ({ key: curve.key, type: 'linear' })))
  })

  it('names the band in the legend, drawn with the same pattern', () => {
    reset()
    render(<DurationTrend model={band} title="Duration trend" />)
    const legend = document.querySelector('[data-chart-legend]') as HTMLElement
    const entry = Array.from(legend.querySelectorAll('li')).find((li) => /p50.*p95/i.test(li.textContent ?? ''))
    expect(entry, 'the band has no legend entry at all').toBeDefined()
    expect(String(entry?.querySelector('[data-legend-swatch]')?.getAttribute('fill'))).toContain('url(#')
  })
})

describe('fix round B · 5 SlowestTests at 320 px', () => {
  const rows = [
    { name: 'checkout pays with a saved card and a coupon code', p95: 3_600_000, runs: 12 },
    { name: 'checkout pays with a saved card and no coupon code', p95: 900, runs: 40 },
  ]
  const atWidth = (width: number, node: ReactNode) => {
    const real = Element.prototype.getBoundingClientRect
    Element.prototype.getBoundingClientRect = function rect() {
      return { ...real.call(this), width } as DOMRect
    }
    try {
      return render(node)
    } finally {
      Element.prototype.getBoundingClientRect = real
    }
  }

  it('stacks the name onto its own line below 420 px, so 20 rows do not read alike', () => {
    atWidth(320, <SlowestTests model={rankSlowestTests(rows)} />)
    const list = document.querySelector('[data-slowest-list]') as HTMLElement
    expect(list.getAttribute('data-slowest-stacked')).toBe('true')
    const first = document.querySelectorAll('li')[0]
    const name = first.querySelector('[data-slowest-name]') as HTMLElement
    // The full name, not a `title` a mouse-only reader has to hover for.
    expect(name.textContent).toBe(rows[0].name)
    expect(name.className).not.toMatch(/truncate/)
  })

  it('keeps one line at a comfortable width', () => {
    atWidth(640, <SlowestTests model={rankSlowestTests(rows)} />)
    expect(document.querySelector('[data-slowest-list]')?.getAttribute('data-slowest-stacked')).toBe('false')
  })
})

describe('fix round B · 8 the minor a11y gaps', () => {
  it('states the excluded executions ONCE, not in the chart and the frame footer both', () => {
    reset()
    render(
      <DurationChartFrame
        kind="histogram"
        title="Duration distribution"
        headingLevel={3}
        histogram={buildDurationHistogram([1, 2, 5, null, 0])}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    expect(screen.getAllByText(/2 executions without duration/)).toHaveLength(1)
  })

  it('captions the duration trend and titles both of its axes', () => {
    reset()
    render(
      <DurationTrend
        title="Duration trend"
        model={durationBandPoints({
          p50: series([{ x: '2026-03-01', y: 100, n: 10 }]),
          p95: series([{ x: '2026-03-01', y: 400, n: 10 }]),
        })}
      />,
    )
    expect(document.querySelector('figcaption')?.textContent).toMatch(/UTC/)
    const titles = captured.axes.map((axis) => JSON.stringify(axis.label ?? ''))
    expect(titles.join(' ')).toMatch(/Day \(UTC\)/)
    expect(titles.join(' ')).toMatch(/Duration/)
  })
})

// ── What the first Linux baselines showed ────────────────────────────────────

describe('the duration charts say each thing once, and only when it applies', () => {
  const inverted = durationBandPoints({
    p50: series([
      { x: '2026-03-01', y: 400, n: 10 },
      { x: '2026-03-02', y: 120, n: 10 },
    ]),
    p95: series([
      { x: '2026-03-01', y: 100, n: 10 },
      { x: '2026-03-02', y: 500, n: 10 },
    ]),
  })

  it('states the p95-below-p50 notice ONCE, not in the chart and the frame footer both', () => {
    reset()
    render(
      <DurationChartFrame
        kind="trend"
        title="Duration trend"
        headingLevel={3}
        band={inverted}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    expect(inverted.notice).toBe('p95 was below p50 on 1 day; both are drawn as reported.')
    expect(screen.getAllByText(inverted.notice as string)).toHaveLength(1)
  })

  it('explains the buckets only when it draws them', () => {
    reset()
    const { unmount } = render(<DurationHistogram model={buildDurationHistogram([1, 2, 5, 40])} />)
    expect(screen.getAllByText(HISTOGRAM_BUCKETS_CAPTION)).toHaveLength(1)
    unmount()

    reset()
    render(<DurationHistogram model={buildDurationHistogram([null, null, 0])} />)
    expect(screen.getByText(/no execution.*carries a duration/i)).toBeInTheDocument()
    expect(screen.queryByText(HISTOGRAM_BUCKETS_CAPTION)).toBeNull()
    expect(screen.queryByText(/log-spaced/)).toBeNull()
  })
})
