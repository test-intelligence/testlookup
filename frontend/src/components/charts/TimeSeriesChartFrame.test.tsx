/**
 * VIZ-403 — the chart inside its frame. What matters here is that the frame's
 * table view is not a lesser view of the chart: the same release markers the
 * plot draws are listed there, and a gap is a "—" rather than a 0.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { analyzeTrend, anomalyRuleText, formatTrendPercent } from '@/lib/trendStats'
import { useProjectStore } from '@/store/projectStore'
import { useSuiteStore } from '@/store/suiteStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import TimeSeriesChartFrame from './TimeSeriesChartFrame'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider } from './ChartAnnouncer'
import { OUTSIDE_ZOOM_MARK } from './ReleaseMarkerTable'
import { addUtcDays } from './seriesAlignment'
import { buildTimeSeriesModel, timeSeriesFromTrends, type TimeSeriesPoint } from './timeSeriesModel'
import { CHART_MESSAGES } from './chartMessages'
import { RESET_ZOOM_LABEL } from './zoom/ChartRangeBrush'
import { PROMOTE_NOT_LATEST_REASON } from './zoom/zoomModel'

vi.mock('recharts', () => {
  const pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    // The recharts hooks Wave 2.4's tooltip content reads (`ChartTooltip`'s `usePlotArea`, the bars' `useXAxisScale`): listed so a mock that ever renders that content does not throw.
    usePlotArea: () => undefined,
    useXAxisScale: () => undefined,
    ResponsiveContainer: pass,
    ComposedChart: pass,
    CartesianGrid: () => <div />,
    Legend: () => <div />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    Tooltip: () => <div />,
    Line: () => <div data-testid="rate-line" />,
    Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    Cell: () => <div />,
    ReferenceLine: (props: { x?: string }) => <div data-testid="release-marker" data-x={props.x} />,
    ReferenceDot: (props: { x?: string }) => <div data-testid="anomaly-dot" data-x={props.x} />,
  }
})

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

const meta = { measured: true, reason: null, partial_day: null, includes_in_progress: 0 } as unknown as EnvelopeMeta

const model = buildTimeSeriesModel({
  points: timeSeriesFromTrends([
    { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
    { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
  ]),
  releases: [{ id: 'r1', name: '1.4.0', date: '2026-03-02T00:00:00Z' }],
})

describe('TimeSeriesChartFrame', () => {
  it('lists the same release markers in the table view that the plot draws', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={model}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    // Drawn on the plot …
    expect(screen.getByTestId('release-marker')).toHaveAttribute('data-x', '2026-03-02')
    // … and listed in the table view.
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const releaseTable = screen.getByRole('table', { name: /release markers/i })
    expect(releaseTable).toHaveTextContent('2026-03-02')
    expect(releaseTable).toHaveTextContent('1.4.0')
  })

  it('shows the gap as a dash in the table, never as a zero', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={model}
        state={{ status: 'ready', data: {}, meta, revalidating: false }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const dataTable = screen.getByRole('table', { name: /data table/i })
    const gapRow = within(dataTable).getByRole('rowheader', { name: '2026-03-02' })
    expect(gapRow.parentElement?.textContent).toContain('—')
    expect(dataTable).toHaveTextContent('90')
  })

  it('renders the frame’s not-measured state as "—" with the reason, never 0', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={null}
        state={{ status: 'not-measured', reason: 'no evaluated executions in this window', meta: null }}
      />,
    )
    expect(screen.getByText(CHART_MESSAGES.notMeasured)).toBeInTheDocument()
    expect(screen.getByText('no evaluated executions in this window')).toBeInTheDocument()
    expect(screen.queryByTestId('rate-line')).toBeNull()
  })

  it('never mounts the renderer for a state the frame owns', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={model}
        state={{ status: 'filtered-empty', meta: null }}
      />,
    )
    expect(screen.queryByTestId('rate-line')).toBeNull()
    expect(screen.getByText(CHART_MESSAGES.filteredEmpty)).toBeInTheDocument()
  })
})

// ── VIZ-407: local zoom ──────────────────────────────────────────────────────

const D = (i: number) => addUtcDays('2026-03-01', i)
const trendPoint = (i: number, rate: number): TimeSeriesPoint => ({
  x: D(i),
  rate,
  rateReason: null,
  executions: 100,
  n: 100,
  partial: false,
})
/** 35 days around 90-92 %, one collapse on day 28 (a Sunday, like days 0/7/14/21), releases on days 2 and 30. */
const days35 = (offset = 0) =>
  buildTimeSeriesModel({
    points: Array.from({ length: 35 }, (_, i) => ({ ...trendPoint(i, i === 28 ? 60 : 90 + (i % 3)), x: D(i + offset) })),
    releases: [
      { id: 'r-early', name: '2.0.0', date: D(2 + offset) },
      { id: 'r-late', name: '2.1.0', date: D(30 + offset) },
    ],
  })
const model35 = days35()
const READY = { status: 'ready' as const, data: {}, meta, revalidating: false }

const startHandle = () => screen.getByRole('slider', { name: 'Start of zoom range' })
const endHandle = () => screen.getByRole('slider', { name: 'End of zoom range' })
/** Zoom to the last `n` days from the keyboard: start handle to the end, then back n-1 days. */
function zoomToLast(n: number) {
  fireEvent.keyDown(startHandle(), { key: 'End' })
  for (let i = 1; i < n; i++) fireEvent.keyDown(startHandle(), { key: 'ArrowLeft' })
}
const openTable = () => fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
const dataRows = () =>
  within(screen.getByRole('table', { name: /data table/i }))
    .getAllByRole('rowheader')
    .map((cell) => cell.textContent)

function resetScope() {
  useTimeWindowStore.setState({ days: 30 })
  useProjectStore.setState({ activeProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
}

describe('TimeSeriesChartFrame — VIZ-407 zoom', () => {
  beforeEach(resetScope)
  afterEach(() => vi.useRealTimers())

  it('draws no brush unless the frame is zoomable', () => {
    render(<TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} />)
    expect(screen.queryByRole('slider')).toBeNull()
  })

  it('zooms the plot, the table and the summary to the range — and says so', () => {
    const { container } = render(
      <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} zoom />,
    )
    zoomToLast(10)
    // The plot: only the release inside the zoom is drawn.
    expect(screen.getAllByTestId('release-marker').map((m) => m.getAttribute('data-x'))).toEqual([D(30)])
    // The table: the zoomed days only…
    openTable()
    expect(dataRows()).toEqual(Array.from({ length: 10 }, (_, i) => D(25 + i)))
    // …and the release outside the zoom is still listed, marked as outside the view.
    const releases = screen.getByRole('table', { name: /release markers/i })
    const early = within(releases).getByRole('rowheader', { name: D(2) }).parentElement as HTMLElement
    expect(early).toHaveTextContent(`2.0.0 (${OUTSIDE_ZOOM_MARK})`)
    const late = within(releases).getByRole('rowheader', { name: D(30) }).parentElement as HTMLElement
    expect(late).not.toHaveTextContent(OUTSIDE_ZOOM_MARK)
    // The summary and the footer say it is zoomed.
    expect(container.querySelector('[data-chart-summary]')?.textContent).toContain('zoomed to Mar 26 – Apr 4, 2026')
    const note = container.querySelector('[data-chart-zoom-note]')
    expect(note).toHaveTextContent('Zoomed to Mar 26 – Apr 4, 2026: 10 of 35 days, not the page window.')
    expect(note).toHaveTextContent('1 release marker is outside the zoom and listed in the table.')
    expect(container.querySelector('[data-chart-zoom-table-note]')).toHaveTextContent('Zoomed to Mar 26 – Apr 4, 2026')
  })

  it('keeps the trend analysis the WHOLE window’s: a zoomed day’s anomaly and average are the unzoomed ones', () => {
    const full = analyzeTrend(model35.points)
    if (!full.available) throw new Error('fixture must be analysable')
    const anomaly = full.anomalies.find((a) => a.x === D(28))
    const average = full.movingAverage.find((p) => p.x === D(26))
    if (!anomaly || !average) throw new Error('fixture must flag day 28 and average day 26')

    render(<TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} trendAnalysis zoom />)
    zoomToLast(10)
    // Drawn on the zoomed plot…
    expect(screen.getAllByTestId('anomaly-dot').map((dot) => dot.getAttribute('data-x'))).toEqual([D(28)])
    openTable()
    const trendTable = screen.getByRole('table', { name: 'Trend analysis by day' })
    const row = (day: string) => within(trendTable).getByRole('rowheader', { name: day }).parentElement as HTMLElement
    // …with the rule and the numbers the unzoomed analysis gave.
    // textContent, not toHaveTextContent: that one folds the rule's no-break spaces into plain ones.
    expect(row(D(28)).textContent).toContain(anomalyRuleText(anomaly))
    expect(row(D(26))).toHaveTextContent(formatTrendPercent(average.value))
    expect(within(trendTable).getAllByRole('rowheader')).toHaveLength(10)
    // The table says its statistics are the whole window’s.
    expect(document.querySelector('[data-trend-table-zoom]')).toHaveTextContent('Trend statistics are computed over all 35 days')
  })

  it('the flagged-days statistic states the days in view AND the window’s count its explanation is about', () => {
    const { container } = render(
      <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} trendAnalysis zoom />,
    )
    const stat = () => container.querySelector('[data-trend-stat="anomalies"] button') as HTMLElement
    // Unzoomed: as before.
    expect(stat()).toHaveTextContent(/^1 day flagged as unusual$/)
    expect(stat()).toHaveAccessibleDescription(/1 flagged/)
    zoomToLast(10) // includes day 28
    expect(stat()).toHaveTextContent('1 day flagged as unusual in view (1 in the window)')
    fireEvent.keyDown(endHandle(), { key: 'Home' }) // day 25 alone: nothing flagged in view
    expect(stat()).toHaveTextContent('No day flagged as unusual in view (1 in the window)')
    // The explanation is the window's, and now agrees with the bracketed count.
    expect(stat()).toHaveAccessibleDescription(/1 flagged/)
  })

  it('announces the zoom and the reset through the page’s one announcer', () => {
    vi.useFakeTimers()
    const { container } = render(
      <ChartAnnouncerProvider>
        <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} zoom />
      </ChartAnnouncerProvider>,
    )
    const polite = () => container.querySelector('[data-chart-announcer="polite"]')?.textContent?.trim()
    zoomToLast(10)
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS)
    })
    expect(polite()).toBe('Pass rate trend chart: zoomed to Mar 26 – Apr 4, 2026')
    fireEvent.click(screen.getByRole('button', { name: RESET_ZOOM_LABEL }))
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS)
    })
    expect(polite()).toBe('Pass rate trend chart: zoom reset, all 35 days shown')
    // Still only the provider's two regions: the brush adds none.
    expect(container.querySelectorAll('[aria-live], [role="status"]')).toHaveLength(2)
  })

  it('announces NEW DATA as an update, not as the zoom made before it', () => {
    vi.useFakeTimers()
    const view = (m: typeof model35) => (
      <ChartAnnouncerProvider>
        <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={m} state={READY} zoom />
      </ChartAnnouncerProvider>
    )
    const { container, rerender } = render(view(model35))
    zoomToLast(10)
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS)
    })
    // The same days, new numbers.
    const refreshed = buildTimeSeriesModel({
      points: model35.points.map((p) => ({ ...p, rate: p.rate === null ? null : p.rate - 1 })),
    })
    rerender(view(refreshed))
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS)
    })
    expect(container.querySelector('[data-chart-announcer="polite"]')?.textContent?.trim()).toBe(
      'Pass rate trend chart: updated',
    )
  })

  it('is cleared by a global scope change — and does not come back when the scope does', () => {
    render(<TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} zoom />)
    zoomToLast(10)
    expect(screen.getByRole('button', { name: RESET_ZOOM_LABEL })).toBeInTheDocument()
    act(() => useTimeWindowStore.getState().setDays(7))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
    expect(startHandle()).toHaveAttribute('aria-valuenow', '0')
    act(() => useTimeWindowStore.getState().setDays(30))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()

    zoomToLast(10)
    act(() => useProjectStore.setState({ activeProjectId: 'project-b' }))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()

    zoomToLast(10)
    act(() => useSuiteStore.setState({ activeSuiteNames: ['payments'] }))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
  })

  it('follows its days as the data moves on, and is cleared once they are gone', () => {
    const view = (m: typeof model35) => (
      <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={m} state={READY} zoom />
    )
    const { rerender } = render(view(model35))
    zoomToLast(10) // Mar 26 – Apr 4 = days 25..34
    // Shifted by 5 days: the zoomed days are still there, at new positions.
    rerender(view(days35(5)))
    expect(startHandle()).toHaveAttribute('aria-valuenow', '20')
    expect(endHandle()).toHaveAttribute('aria-valuenow', '29')
    // Shifted by 30: they are gone, and so is the zoom.
    rerender(view(days35(30)))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
    expect(endHandle()).toHaveAttribute('aria-valuenow', '34')
    // Back to the first days: the dropped zoom does not return.
    rerender(view(model35))
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
  })

  // Baseline review B: the execution bars give each day a slot, so the strip is a BAND scale.
  it('hands the brush a band scale and the pass rate as its sparkline', () => {
    const { container } = render(
      <TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={model35} state={READY} zoom />,
    )
    expect(container.querySelector('[data-chart-brush-track]')).toHaveAttribute('data-chart-brush-scale', 'band')
    expect(container.querySelector('[data-chart-brush-spark]')).not.toBeNull()
  })

  it('can open zoomed', () => {
    const { container } = render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={model35}
        state={READY}
        zoom={{ initial: { from: D(3), to: D(9) } }}
      />,
    )
    expect(startHandle()).toHaveAttribute('aria-valuenow', '3')
    expect(container.querySelector('[data-chart-zoom-note]')).toHaveTextContent('Zoomed to Mar 4–10, 2026')
  })
})

describe('TimeSeriesChartFrame — VIZ-407 Apply as time filter', () => {
  beforeEach(resetScope)
  const WINDOW_OPTIONS = [1, 7, 14, 30, 90]
  const mountApply = () =>
    render(
      <ChartAnnouncerProvider>
        <TimeSeriesChartFrame
          title="Pass rate trend"
          headingLevel={3}
          model={model35}
          state={READY}
          zoom={{ applyAsWindow: { windowOptions: WINDOW_OPTIONS } }}
        />
      </ChartAnnouncerProvider>,
    )

  it('sets the global window to exactly the brushed days through the filter bar’s setter, and the zoom ends', () => {
    mountApply()
    zoomToLast(7)
    fireEvent.click(screen.getByRole('button', { name: 'Apply as time filter: last 7 days' }))
    expect(useTimeWindowStore.getState().days).toBe(7)
    // The new window replaces the zoom.
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
    expect(document.activeElement).toBe(document.querySelector('[data-chart-body]'))
  })

  it('is disabled, with the visible reason, for a range that does not end on the latest day', () => {
    mountApply()
    zoomToLast(8)
    fireEvent.keyDown(endHandle(), { key: 'ArrowLeft' })
    const apply = screen.getByRole('button', { name: 'Apply as time filter' })
    expect(apply).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(PROMOTE_NOT_LATEST_REASON)).toBeInTheDocument()
    fireEvent.click(apply)
    expect(useTimeWindowStore.getState().days).toBe(30)
  })

  it('is disabled for a length the page would snap to another — never applied as a different window', () => {
    mountApply()
    zoomToLast(12)
    const apply = screen.getByRole('button', { name: 'Apply as time filter' })
    expect(apply).toHaveAccessibleDescription('The page window can be 1, 7, 14, 30 or 90 days; this range is 12 days')
    fireEvent.click(apply)
    expect(useTimeWindowStore.getState().days).toBe(30)
  })
})
