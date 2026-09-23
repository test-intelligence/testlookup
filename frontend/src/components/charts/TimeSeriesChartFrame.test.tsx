/**
 * VIZ-403 — the chart inside its frame. What matters here is that the frame's
 * table view is not a lesser view of the chart: the same release markers the
 * plot draws are listed there, and a gap is a "—" rather than a 0.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import TimeSeriesChartFrame from './TimeSeriesChartFrame'
import { buildTimeSeriesModel, timeSeriesFromTrends } from './timeSeriesModel'
import { CHART_MESSAGES } from './chartMessages'

vi.mock('recharts', () => {
  const pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
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
