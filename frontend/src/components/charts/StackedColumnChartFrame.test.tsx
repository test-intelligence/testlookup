/**
 * VIZ-104 K1 — the stacked columns inside their frame. The frame owns every
 * non-data state (no plot is mounted for them), a model with nothing to draw
 * is handed back to the frame as a state rather than drawn as an empty plot,
 * and the table view is the drawn model: a gap is "—", never 0, and each
 * bucket's total is its last column.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import StackedColumnChartFrame, {
  NOTHING_MEASURED_REASON,
  STACKED_COLUMN_CHART_TYPE,
  stackedFrameState,
} from './StackedColumnChartFrame'
import { CHART_MESSAGES } from './chartMessages'
import { readyState, type ChartState } from './chartState'
import { buildStackedColumnModel, TOTAL_LABEL } from './stackedColumnModel'
import {
  HOSTILE_BUCKET_LABEL,
  STATUS_SERIES,
  hostileLabelsFixture,
  seriesMonthlyFixture,
  statusDailyFixture,
} from './__fixtures__/stackedColumn'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 280 })
        : null,
  }
})

const PLOT = '[data-stacked-column-plot]'

const frame = (state: ChartState<unknown>, model = statusDailyFixture, title = 'Execution trend') =>
  render(
    <StackedColumnChartFrame title={title} headingLevel={3} state={state} model={model} animate={false} bucketNoun="day" />,
  )

describe('StackedColumnChartFrame · states the frame owns', () => {
  const owned: [string, ChartState<unknown>, string][] = [
    ['loading', { status: 'loading' }, CHART_MESSAGES.loading],
    ['never-had-data', { status: 'never-had-data' }, CHART_MESSAGES.neverHadData],
    ['filtered-empty', { status: 'filtered-empty', meta: null }, CHART_MESSAGES.filteredEmpty],
    ['not-measured', { status: 'not-measured', reason: 'no runs in scope', meta: null }, CHART_MESSAGES.notMeasured],
    [
      'error',
      { status: 'error', error: { kind: 'server', message: 'The server failed', requestId: 'req-1', status: 500 } },
      CHART_MESSAGES.error,
    ],
    ['forbidden', { status: 'forbidden', requestId: null }, CHART_MESSAGES.forbidden],
  ]

  for (const [name, state, text] of owned) {
    it(`mounts no chart for ${name}`, () => {
      const { container } = frame(state)
      expect(container.querySelector(PLOT)).toBeNull()
      expect(container.querySelector('svg.recharts-surface')).toBeNull()
      if (name !== 'loading') expect(screen.getAllByText(text).length).toBeGreaterThan(0)
    })
  }

  it('mounts no chart when the page has no model yet, even in a drawn state', () => {
    const { container } = render(
      <StackedColumnChartFrame title="Run history" headingLevel={3} state={readyState(null)} model={null} />,
    )
    expect(container.querySelector(PLOT)).toBeNull()
  })
})

describe('StackedColumnChartFrame · a drawn state', () => {
  it('draws the plot under a real heading, named by the frame title', () => {
    const { container } = frame(readyState(null))
    expect(screen.getByRole('heading', { level: 3, name: 'Execution trend' })).toBeInTheDocument()
    expect(container.querySelector(PLOT)).not.toBeNull()
    expect(container.querySelector('[data-chart-cursor]')?.getAttribute('aria-label')).toMatch(/^Execution trend/)
  })

  it('summarises the columns as a stacked column chart, with both axes named', () => {
    const { container } = frame(readyState(null))
    const summary = container.querySelector('[data-chart-summary]')?.textContent ?? ''
    expect(summary).toMatch(new RegExp(`^${STACKED_COLUMN_CHART_TYPE}`))
    expect(summary).toMatch(/X axis: Day \(UTC\) \(14 values\)/)
    expect(summary).toMatch(/Y axis: Executions/)
  })

  it('tabulates a gap as "—", a measured zero as 0, and each bucket’s total last', () => {
    frame(readyState(null))
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: /data table/i })
    const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Day (UTC)', 'Passed', 'Failed', 'Broken', 'Skipped', TOTAL_LABEL])
    const cells = (label: string) =>
      Array.from((within(table).getByRole('rowheader', { name: label }).parentElement as HTMLElement).querySelectorAll('td'), (td) => td.textContent)
    expect(cells('Mar 4')).toEqual(['—', '—', '—', '—', '—'])
    expect(cells('Mar 7')).toEqual(['0', '0', '0', '0', '0'])
    expect(cells('Mar 9')).toEqual(['199', '7', '2', '—', '208'])
  })

  it('prints the table in the model’s own format', () => {
    render(<StackedColumnChartFrame title="Hours saved per month" headingLevel={3} state={readyState(null)} model={seriesMonthlyFixture} />)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: /data table/i })
    const row = within(table).getByRole('rowheader', { name: 'Oct 2025' }).parentElement as HTMLElement
    expect(Array.from(row.querySelectorAll('td'), (td) => td.textContent)).toEqual(['12.5 h', '4.25 h', '2 h', '18.75 h'])
  })

  it('lists a hostile bucket name in the table as text', () => {
    const { container } = frame(readyState(null), hostileLabelsFixture, 'Suites')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(within(screen.getByRole('table', { name: /data table/i })).getByRole('rowheader', { name: HOSTILE_BUCKET_LABEL })).toBeInTheDocument()
    expect(container.querySelector('img, script')).toBeNull()
  })
})

describe('stackedFrameState · a model with nothing to draw is a state, not an empty plot', () => {
  const build = (buckets: Parameters<typeof buildStackedColumnModel>[0]['buckets']) =>
    buildStackedColumnModel({ buckets, series: STATUS_SERIES, valueTitle: 'Executions', bucketTitle: 'Day' })

  it('turns buckets of which NOTHING was measured into not-measured, with a reason', () => {
    const model = build([{ key: 'a', label: 'A', values: {} }])
    expect(stackedFrameState(readyState(null), model)).toEqual({ status: 'not-measured', reason: NOTHING_MEASURED_REASON, meta: null })
    frame(readyState(null), model)
    expect(screen.getByText(CHART_MESSAGES.notMeasured)).toBeInTheDocument()
    expect(screen.getByText(NOTHING_MEASURED_REASON)).toBeInTheDocument()
  })

  it('turns no bucket, and a window of zeros, into filtered-empty', () => {
    expect(stackedFrameState(readyState(null), build([])).status).toBe('filtered-empty')
    expect(stackedFrameState(readyState(null), build([{ key: 'a', label: 'A', values: { passed: 0 } }])).status).toBe('filtered-empty')
  })

  it('leaves a drawable model, and every state the page already owns, alone', () => {
    const ready = readyState(null)
    expect(stackedFrameState(ready, statusDailyFixture)).toBe(ready)
    const loading: ChartState<unknown> = { status: 'loading' }
    expect(stackedFrameState(loading, build([]))).toBe(loading)
    expect(stackedFrameState(ready, null)).toBe(ready)
  })
})
