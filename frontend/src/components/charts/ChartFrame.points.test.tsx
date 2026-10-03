/**
 * Wave 3 (VIZ-506): `ChartFrame`, `ChartTable`, the export menu and the CSV
 * take ANY C3 kind, so the test scatter's `points` series is framed like any
 * other chart: summary, "View as table", and a CSV of raw numbers. The four
 * older kinds keep their own tests (ChartFrame.test.tsx, chartExport.test.ts).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, PointsChart } from '@/lib/viz/contracts'
import { buildChartCsv } from '@/lib/viz/chartExport'
import ChartFrame from './ChartFrame'
import ChartTable from './ChartTable'
import type { ChartState } from './chartStateCore'

const HOSTILE = '<img src=x onerror="window.__xss=1">'

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-30', days: 30, timezone: 'UTC' },
  },
  totals: { matched_runs: 30, total_runs: 30, matched_executions: 900, total_executions: 900 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-30T09:00:00Z',
  as_of: '2026-09-30T09:00:00Z',
}

const POINTS: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [
    { id: 'fp-1', label: 'test_checkout', x: 1840.5, y: 12.5, size: 1200, n: 1190 },
    { id: 'fp-2', label: HOSTILE, x: 1, y: 0, size: 5, n: 5 },
  ],
  medians: { x: 920.75, y: 6.25 },
  excluded: { below_min_executions: 14, no_duration: 0, no_evaluated: 0 },
}

const ready: ChartState = { status: 'ready', data: { meta: META, series: POINTS }, meta: META, revalidating: false }

describe('a points series in the frame', () => {
  it('is summarised and tabulated from the same series, names as text', () => {
    render(
      <ChartFrame title="Duration vs failure rate" headingLevel={3} state={ready} series={POINTS} chartType="Scatter chart">
        <div data-testid="plot" />
      </ChartFrame>,
    )
    expect(screen.getByTestId('plot')).toBeInTheDocument()
    const summary = document.getElementById(
      (screen.getByRole('group', { name: 'Duration vs failure rate' }).getAttribute('aria-describedby') ?? '').split(' ').pop() ?? '',
    )
    expect(summary?.textContent).toContain('Scatter chart of 2 points.')
    expect(summary?.textContent).toContain('Not shown: 14 below the minimum executions.')
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table', { name: /data table/i })
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      'Name',
      'p95 duration (ms)',
      'Failure rate (%)',
      'Executions',
      'Evaluated',
    ])
    expect(within(table).getByRole('rowheader', { name: HOSTILE })).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('never hands a points series to function children (they are typed for the four older kinds)', () => {
    const children = vi.fn(() => <div data-testid="plot" />)
    render(
      <ChartFrame title="T" headingLevel={3} state={ready} series={POINTS}>
        {children}
      </ChartFrame>,
    )
    expect(children).not.toHaveBeenCalled()
    expect(screen.queryByTestId('plot')).toBeNull()
  })

  it('ChartTable reads a points series as it does every kind', () => {
    render(<ChartTable caption="Scatter — data table" series={POINTS} autoFocus={false} />)
    const row = screen.getByRole('rowheader', { name: 'test_checkout' }).parentElement as HTMLElement
    expect(Array.from(row.querySelectorAll('td'), (td) => td.textContent)).toEqual(['1,841 ms', '12.5%', '1,200', '1,190'])
  })
})

describe('buildChartCsv with a points series', () => {
  it('keeps the table’s columns and writes RAW numbers (a spreadsheet re-reads them as numbers)', () => {
    const csv = buildChartCsv('Duration vs failure rate', POINTS, null)
    const lines = csv.trimEnd().split('\r\n')
    const header = lines.findIndex((line) => line.startsWith('Name,'))
    expect(lines[header]).toBe('Name,p95 duration (ms),Failure rate (%),Executions,Evaluated')
    expect(lines[header + 1]).toBe('test_checkout,1840.5,12.5,1200,1190')
    // The hostile name is a quoted CSV cell, never a formula or markup that runs.
    expect(lines[header + 2]).toMatch(/,1,0,5,5$/)
    expect(lines).toHaveLength(header + 3)
  })
})
