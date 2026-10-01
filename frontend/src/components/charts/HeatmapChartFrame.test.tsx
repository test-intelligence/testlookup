/**
 * K5: `HeatmapChartFrame` — the Trends suite x day heatmap in its frame.
 *
 * jsdom has no canvas, so the engine is mocked at the registry (as in
 * `HeatmapChart.test.tsx`): the test reads the option the frame hands ECharts,
 * and the frame's own DOM (footer, table view, states).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { validateChartSeries } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_MESSAGES } from './chartMessages'
import type { ChartResponse, ChartState } from './chartState'
import HeatmapChartFrame, { HEATMAP_ORDER_NOTE, HEATMAP_SHAPE_ERROR } from './HeatmapChartFrame'
import { heatmapDescription, heatmapRowsNote } from './heatmapFromChartData'
import {
  HEATMAP_FRAME_HOSTILE_NAME,
  heatmapFrameDense,
  heatmapFrameHostile,
  heatmapFrameMeta,
  heatmapFrameWorstFirst,
} from './__fixtures__/heatmapFrame'

const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

interface Option {
  yAxis: { data: string[] }
  xAxis: { data: string[] }
  series: { id: string; data: unknown[] }[]
}

const ready = (data: ChartResponse): ChartState<ChartResponse> => ({
  status: 'ready',
  data,
  meta: data.meta,
  revalidating: false,
})

function renderFrame(state: ChartState<ChartResponse>) {
  return render(
    <ChartAnnouncerProvider>
      <HeatmapChartFrame title="Pass rate by suite and day" headingLevel={3} state={state} />
    </ChartAnnouncerProvider>,
  )
}

async function drawnOption(): Promise<Option> {
  await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
  const calls = engine.instance.setOption.mock.calls
  return calls[calls.length - 1][0] as unknown as Option
}

describe('HeatmapChartFrame (K5)', () => {
  beforeEach(() => {
    engine.load.mockReset()
    engine.load.mockResolvedValue({ init: engine.init })
    engine.init.mockClear()
    engine.instance.setOption.mockClear()
  })

  it('draws the rows worst first with Other last, from the shared chart-data state', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.yAxis.data[0]).toBe('legacy-import')
    expect(option.yAxis.data[option.yAxis.data.length - 1]).toBe('Other')
    expect(option.yAxis.data).toHaveLength(8)
    expect(option.xAxis.data).toHaveLength(14)
  })

  it('DRAWS the worst row at the top, as its footer says (fix round 2, B0 finding 6)', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = (await drawnOption()) as Option & { yAxis: { inverse?: boolean } }
    // ECharts draws category 0 at the BOTTOM unless the axis is inverted.
    expect(option.yAxis.data[0]).toBe('legacy-import')
    expect(option.yAxis.inverse).toBe(true)
  })

  it('prints the kit’s short day on the column axis, and keeps the full day in the tooltip and the table', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.xAxis.data[0]).toBe('Sep 1')
    expect(option.xAxis.data[13]).toBe('Sep 14')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('columnheader', { name: '2026-09-01' })).toBeInTheDocument()
  })

  it('the arrow keys follow the drawn order: ArrowDown from the top row goes to the next row down', async () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    await drawnOption()
    const surface = container.querySelector('[data-chart-keyboard="heatmap"]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    // The first stop is the top-left cell: row 0, the worst.
    const first = Number(surface.getAttribute('data-active-index'))
    fireEvent.keyDown(surface, { key: 'ArrowDown' })
    const second = Number(surface.getAttribute('data-active-index'))
    // The matrix is row-major, 7 columns per row (the hostile fixture is 7 days).
    expect(Math.floor(first / 7)).toBe(0)
    expect(Math.floor(second / 7)).toBe(1)
  })

  it('hands the canvas ratios (0..1), never percentage points', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    const cells = option.series.find((s) => s.id === 'cells')?.data as [number, number, number | string][]
    const values = cells.map((cell) => cell[2]).filter((v): v is number => typeof v === 'number')
    expect(values.length).toBeGreaterThan(0)
    expect(Math.max(...values)).toBeLessThanOrEqual(1)
  })

  it('draws a no-data cell in the hatched series, never as the lowest value', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    const noData = option.series.find((s) => s.id === 'no-data')
    expect(noData?.data).toHaveLength(2)
  })

  it('states the row truncation in its own words, and not the envelope’s "top 8"', () => {
    const { container } = renderFrame({
      status: 'truncated',
      data: heatmapFrameWorstFirst,
      meta: heatmapFrameMeta,
      shown: 8,
      total: 12,
      revalidating: false,
    })
    const rows = container.querySelector('[data-heatmap-rows]')
    expect(rows?.textContent).toBe(`${HEATMAP_ORDER_NOTE} Top 7 of 12 suites by executions; the rest are combined in "Other".`)
    expect(container.querySelector('[data-chart-truncation]')).toBeNull()
  })

  it('the table view reads the same matrix: rates as percentages, a null cell as "No data"', () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table')
    const admin = within(table).getByRole('rowheader', { name: 'admin' }).closest('tr') as HTMLTableRowElement
    expect(within(admin).getAllByText('No data')).toHaveLength(1)
    const firstRow = within(table).getAllByRole('row')[1]
    expect(within(firstRow).getByRole('rowheader').textContent).toBe('legacy-import')
    // A pass rate reads as a percentage no larger than 100%.
    for (const cell of within(table).getAllByRole('cell')) {
      const match = /([\d.,]+)%/.exec(cell.textContent ?? '')
      if (match) expect(Number(match[1].replace(/,/g, ''))).toBeLessThanOrEqual(100)
    }
  })

  it('renders a hostile suite name as text on every path it reaches', async () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    const option = await drawnOption()
    expect(option.yAxis.data).toContain(HEATMAP_FRAME_HOSTILE_NAME)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('rowheader', { name: HEATMAP_FRAME_HOSTILE_NAME })).toBeInTheDocument()
    expect(container.querySelector('img, b')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('says only the order when nothing was truncated', () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    expect(container.querySelector('[data-heatmap-rows]')?.textContent).toBe(HEATMAP_ORDER_NOTE)
  })

  it('the 90-day dense matrix (8 x 90) draws and validates', async () => {
    renderFrame(ready(heatmapFrameDense))
    const option = await drawnOption()
    expect(option.yAxis.data).toHaveLength(8)
    expect(option.xAxis.data).toHaveLength(90)
  })

  it('a payload that is not a day x series response is an error, not a half-drawn chart', () => {
    renderFrame(
      ready({
        meta: null,
        series: { kind: 'matrix', value_type: 'rate', x_labels: ['a'], y_labels: ['b'], cells: [{ x: 0, y: 0, value: 0.5, n: 1 }] },
      }),
    )
    expect(screen.getByText(HEATMAP_SHAPE_ERROR)).toBeInTheDocument()
    expect(engine.load).not.toHaveBeenCalled()
  })

  it('loading draws the skeleton and loads no engine', () => {
    renderFrame({ status: 'loading' })
    expect(engine.load).not.toHaveBeenCalled()
  })

  it('keeps a caller footer beside its own row note', () => {
    const { container } = render(
      <ChartAnnouncerProvider>
        <HeatmapChartFrame
          title="t"
          headingLevel={3}
          state={ready(heatmapFrameHostile)}
          footer={<span data-testid="grain">Counted per test execution</span>}
        />
      </ChartAnnouncerProvider>,
    )
    expect(screen.getByTestId('grain')).toBeInTheDocument()
    expect(container.querySelector('[data-heatmap-rows]')).not.toBeNull()
  })
})

describe('heatmapRowsNote', () => {
  it('names the whole when the server says how many there were', () => {
    expect(heatmapRowsNote({ shown: 7, total: 12, other: true }, 'suites')).toContain('Top 7 of 12 suites')
  })
  it('an Other row with no count still says what it is', () => {
    expect(heatmapRowsNote({ shown: 7, total: 7, other: true }, 'suites')).toBe(
      'The remaining suites are combined in "Other".',
    )
  })
  it('nothing to say without truncation', () => {
    expect(heatmapRowsNote({ shown: 3, total: 3, other: false }, 'suites')).toBe('')
  })
})

describe('heatmapDescription', () => {
  const matrix = (columns: number) => ({
    kind: 'matrix' as const,
    value_type: 'rate' as const,
    x_labels: Array.from({ length: columns }, (_, i) => `d${i}`),
    y_labels: [],
    cells: [],
  })
  it('names the rows, Other and the days', () => {
    expect(heatmapDescription('Suites', matrix(14), { shown: 7, total: 12, other: true }, 'suites')).toBe(
      'Suites: pass rate for 7 suites and Other over 14 days, lowest first.',
    )
  })
  it('one day is a day', () => {
    expect(heatmapDescription('S', matrix(1), { shown: 2, total: 2, other: false }, 'suites')).toBe(
      'S: pass rate for 2 suites over 1 day, lowest first.',
    )
  })
})

describe('heatmap frame fixtures', () => {
  it('are chart-data responses in percentage points, in the server order', () => {
    for (const fixture of [heatmapFrameWorstFirst, heatmapFrameDense, heatmapFrameHostile]) {
      const checked = validateChartSeries(fixture.series)
      expect(checked.ok ? [] : checked.errors).toEqual([])
      if (fixture.series.kind !== 'series') throw new Error('fixture must be a series chart')
      const ys = fixture.series.series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => y !== null)
      expect(Math.max(...ys)).toBeGreaterThan(1)
    }
  })
})
