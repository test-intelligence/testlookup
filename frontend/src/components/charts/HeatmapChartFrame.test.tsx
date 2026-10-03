/**
 * `HeatmapChartFrame` (K5, Wave 2.6; VIZ-501, Wave 3) — a `/analytics/heatmap`
 * matrix in its frame.
 *
 * jsdom has no canvas, so the engine is mocked at the registry (as in
 * `HeatmapChart.test.tsx`): the test reads the option the frame hands ECharts,
 * and the frame's own DOM (footer, table view, states).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnyChartSeries, EnvelopeMeta, MatrixChart } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_MESSAGES } from './chartMessages'
import type { ChartResponse, ChartState } from './chartState'
import HeatmapChartFrame, { HEATMAP_ORDER_NOTE, HEATMAP_SHAPE_ERROR, type HeatmapChartFrameProps } from './HeatmapChartFrame'
import { HEATMAP_ROW_LABEL_CHARS, heatmapOrderNote, RUN_AXIS_TITLE } from './heatmapFromMatrix'
import {
  HEATMAP_FRAME_HOSTILE_NAME,
  HEATMAP_FRAME_LONG_NAME,
  heatmapFrameDense,
  heatmapFrameEdges,
  heatmapFrameHostile,
  heatmapFrameMeta,
  heatmapFrameStatus,
  heatmapFrameWorstFirst,
} from './__fixtures__/heatmapFrame'

const engine = vi.hoisted(() => {
  const listeners = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    on: vi.fn((name: string, listener: (params: unknown) => void) => listeners.set(name, listener)),
    off: vi.fn(),
  }
  return { instance, listeners, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

interface Option {
  yAxis: { data: string[]; inverse?: boolean }
  xAxis: { data: string[]; name?: string }
  series: { id: string; data: unknown[] }[]
  visualMap: { id: string; min?: number; max?: number; text?: string[] }[]
}

type State = ChartState<ChartResponse<AnyChartSeries>>

const ready = (data: ChartResponse<AnyChartSeries>): State => ({
  status: 'ready',
  data,
  meta: data.meta,
  revalidating: false,
})

function renderFrame(state: State, props: Partial<HeatmapChartFrameProps> = {}) {
  return render(
    <ChartAnnouncerProvider>
      <HeatmapChartFrame title="Pass rate by suite and day" headingLevel={3} state={state} {...props} />
    </ChartAnnouncerProvider>,
  )
}

async function drawnOption(): Promise<Option> {
  await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
  const calls = engine.instance.setOption.mock.calls
  return calls[calls.length - 1][0] as unknown as Option
}

const footerText = (container: HTMLElement) => container.querySelector('[data-heatmap-rows]')?.textContent

describe('HeatmapChartFrame (VIZ-501)', () => {
  beforeEach(() => {
    engine.listeners.clear()
    engine.load.mockReset()
    engine.load.mockResolvedValue({ init: engine.init })
    engine.init.mockClear()
    engine.instance.setOption.mockClear()
  })

  it('draws the rows worst first, and only the server’s rows (no "Other" row)', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.yAxis.data[0]).toBe('legacy-import')
    expect(option.yAxis.data).not.toContain('Other')
    expect(option.yAxis.data).toHaveLength(7)
    expect(option.xAxis.data).toHaveLength(14)
  })

  it('DRAWS the worst row at the top, as its footer says (fix round 2, B0 finding 6)', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.yAxis.data[0]).toBe('legacy-import')
    expect(option.yAxis.inverse).toBe(true)
  })

  it('re-sorts on request, keys and all: by name, the table and the canvas agree', async () => {
    const { container } = renderFrame(ready(heatmapFrameWorstFirst), { sort: 'name' })
    const option = await drawnOption()
    expect(option.yAxis.data.slice(0, 3)).toEqual(['admin', 'auth', 'billing'])
    expect(footerText(container)).toMatch(/^Rows: by name\./)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const firstRow = within(screen.getByRole('table')).getAllByRole('row')[1]
    expect(within(firstRow).getByRole('rowheader').textContent).toBe('admin')
  })

  it('prints the kit’s short day on a day axis, and keeps the full day in the tooltip and the table', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.xAxis.data[0]).toBe('Sep 1')
    expect(option.xAxis.data[13]).toBe('Sep 14')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('columnheader', { name: '2026-09-01' })).toBeInTheDocument()
  })

  it('columns that are not runs print their labels as they are (no day formatting, no title)', async () => {
    renderFrame(ready(heatmapFrameStatus))
    const option = await drawnOption()
    expect(option.xAxis.data[0]).toBe('Build 1200')
    expect(option.xAxis.name).toBeUndefined()
  })

  it('a RUN axis prints the build alone under a "Build" title; repeats are told apart on every path (F-04, F-19)', async () => {
    const series = heatmapFrameStatus.series as MatrixChart
    // The endpoint's own labels: the bare build number, and build 1203 twice.
    const bare = { ...series, x_labels: series.x_labels.map((label, x) => (x === 5 ? '1203' : label.replace('Build ', ''))) }
    renderFrame(ready({ meta: heatmapFrameStatus.meta, series: bare }), { nouns: { rows: ['test', 'tests'], columns: ['run', 'runs'] } })
    const option = await drawnOption()
    expect(option.xAxis.data).toEqual(['1200', '1201', '1202', '1203 (1)', '1204', '1203 (2)', '1206', '1207'])
    expect(option.xAxis.name).toBe(RUN_AXIS_TITLE)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('columnheader', { name: 'Build 1203 (1)' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Build 1203 (2)' })).toBeInTheDocument()
  })

  it('the arrow keys follow the drawn order: ArrowDown from the top row goes to the next row down', async () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    await drawnOption()
    const surface = container.querySelector('[data-chart-keyboard="heatmap"]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const first = Number(surface.getAttribute('data-active-index'))
    fireEvent.keyDown(surface, { key: 'ArrowDown' })
    const second = Number(surface.getAttribute('data-active-index'))
    // Row-major, 7 columns per row (the hostile fixture is 7 days).
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
    expect(Math.max(...values)).toBeGreaterThan(0.9)
  })

  it('draws a no-data cell in the hatched series, never as the lowest value', async () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    const option = await drawnOption()
    expect(option.series.find((s) => s.id === 'no-data')?.data).toHaveLength(2)
  })

  it('states the server’s row cut in its own words, not the envelope’s "top N"', () => {
    const { container } = renderFrame({
      status: 'truncated',
      data: heatmapFrameWorstFirst,
      meta: heatmapFrameMeta,
      shown: 7,
      total: 12,
      revalidating: false,
    })
    expect(footerText(container)).toBe(`${HEATMAP_ORDER_NOTE} Top 7 of 12 suites by failures.`)
    expect(container.querySelector('[data-chart-truncation]')).toBeNull()
  })

  it('states a column cut by what the columns are', () => {
    const meta = { ...(heatmapFrameStatus.meta as EnvelopeMeta), truncated: true, truncated_total: 120, truncated_axes: { x: { dimension: 'run', kept: 8, total: 120 } } }
    const { container } = renderFrame(ready({ meta, series: heatmapFrameStatus.series }), { nouns: { rows: ['test', 'tests'], columns: ['run', 'runs'] } })
    expect(footerText(container)).toBe('Rows: most failures first. The last 8 of 120 runs.')
  })

  it('says today’s column is partial, and the tooltip and table name it so', () => {
    const { container } = renderFrame(ready(heatmapFrameEdges))
    expect(footerText(container)).toContain('Today’s column is partial')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('columnheader', { name: '2026-09-14 (today, partial)' })).toBeInTheDocument()
  })

  it('the table view reads the same matrix: rates as percentages, a null cell as "No data", with its counts', () => {
    renderFrame(ready(heatmapFrameWorstFirst))
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table')
    const admin = within(table).getByRole('rowheader', { name: 'admin' }).closest('tr') as HTMLTableRowElement
    expect(within(admin).getAllByText('No data')).toHaveLength(1)
    const firstRow = within(table).getAllByRole('row')[1]
    expect(within(firstRow).getByRole('rowheader').textContent).toBe('legacy-import')
    // A pass rate reads as a percentage no larger than 100%, with the counts behind it.
    const cells = within(table).getAllByRole('cell')
    expect(cells.some((cell) => / passed/.test(cell.textContent ?? ''))).toBe(true)
    for (const cell of cells) {
      const match = /^([\d.,]+)%/.exec(cell.textContent ?? '')
      if (match) expect(Number(match[1].replace(/,/g, ''))).toBeLessThanOrEqual(100)
    }
  })

  it('a skipped-only cell is "Nothing evaluated" in the table, never 0%', () => {
    renderFrame(ready(heatmapFrameEdges))
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const proto = screen.getByRole('rowheader', { name: '__proto__' }).closest('tr') as HTMLTableRowElement
    expect(within(proto).getByText('Nothing evaluated (4 skipped)')).toBeInTheDocument()
    const dormant = screen.getByRole('rowheader', { name: 'dormant' }).closest('tr') as HTMLTableRowElement
    expect(within(dormant).getAllByText('No data')).toHaveLength(7)
  })

  it('renders hostile and Object-member names as text on every path it reaches', async () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    const option = await drawnOption()
    // The axis prints the middle-truncated name; the tooltip and table keep the whole one.
    expect(option.yAxis.data[0]).toHaveLength(HEATMAP_ROW_LABEL_CHARS)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('rowheader', { name: HEATMAP_FRAME_HOSTILE_NAME })).toBeInTheDocument()
    expect(container.querySelector('img, b')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('prints constructor, __proto__ and a 250-character name literally', async () => {
    renderFrame(ready(heatmapFrameEdges))
    const option = await drawnOption()
    // The canvas copy carries a zero-width word joiner (zrender's text cache is a plain object:
    // `canvasText.ts`); the table, the tooltip and the readout keep the name as given.
    expect(option.yAxis.data).toContain('\u2060constructor')
    expect(option.yAxis.data).toContain('\u2060__proto__')
    expect(option.yAxis.data).not.toContain('__proto__')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('rowheader', { name: HEATMAP_FRAME_LONG_NAME })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'constructor' })).toBeInTheDocument()
  })

  it('the default order note is the rate, worst-first rule', () => {
    expect(HEATMAP_ORDER_NOTE).toBe(heatmapOrderNote('rate', 'worst'))
  })

  it('says only the order when nothing was cut', () => {
    const { container } = renderFrame(ready(heatmapFrameHostile))
    expect(footerText(container)).toBe(HEATMAP_ORDER_NOTE)
  })

  it('fit to data: the ramp spans the measured range, and the footer says so', async () => {
    const { container } = renderFrame(ready(heatmapFrameHostile), { fit: true })
    const option = await drawnOption()
    const ramp = option.visualMap.find((v) => v.id === 'ramp')
    expect(ramp?.min).toBeGreaterThan(0.5)
    expect(ramp?.max).toBeLessThan(1)
    expect(footerText(container)).toMatch(/Colour scale fitted to the data: [\d.]+% to [\d.]+%\./)
  })

  it('fit off (the default): the ramp is 0..100%', async () => {
    renderFrame(ready(heatmapFrameHostile))
    const ramp = (await drawnOption()).visualMap.find((v) => v.id === 'ramp')
    expect([ramp?.min, ramp?.max]).toEqual([0, 1])
  })

  it('the 90-day dense matrix (7 x 90) draws', async () => {
    renderFrame(ready(heatmapFrameDense))
    const option = await drawnOption()
    expect(option.yAxis.data).toHaveLength(7)
    expect(option.xAxis.data).toHaveLength(90)
  })

  it('a payload that is not a matrix is an error, not a half-drawn chart', () => {
    renderFrame(
      ready({
        meta: null,
        series: { kind: 'series', dimensions: ['day'], x_type: 'time', series: [] },
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
    const { container } = renderFrame(ready(heatmapFrameHostile), {
      footer: <span data-testid="grain">Counted per test execution</span>,
    })
    expect(screen.getByTestId('grain')).toBeInTheDocument()
    expect(container.querySelector('[data-heatmap-rows]')).not.toBeNull()
  })

  it('a caller footer alone (no data yet) is kept', () => {
    renderFrame({ status: 'loading' }, { footer: <span data-testid="grain">g</span> })
    expect(screen.getByTestId('grain')).toBeInTheDocument()
  })

  it('names the chart for assistive tech by its kind’s nouns', async () => {
    const { container } = renderFrame(ready(heatmapFrameStatus), { nouns: { rows: ['test', 'tests'], columns: ['run', 'runs'] } })
    await drawnOption()
    expect(container.querySelector('[data-chart-keyboard="heatmap"]')).toHaveAttribute(
      'aria-label',
      'Pass rate by suite and day: results for 4 tests over 8 runs, most failures first.',
    )
  })

  it('mark activation: a click names the DRAWN row’s key and the column’s, after any re-sort', async () => {
    const onMarkActivate = vi.fn()
    renderFrame(ready(heatmapFrameWorstFirst), {
      sort: 'name',
      markDimensions: { row: 'suite', column: 'day' },
      onMarkActivate,
      markIntents: () => ['rows'],
    })
    await drawnOption()
    await waitFor(() => expect(engine.listeners.get('click')).toBeDefined())
    // Drawn row 0 by name is admin; column 2 is 2026-09-03.
    engine.listeners.get('click')?.({ value: [2, 0, 0.9], event: { event: {} } })
    expect(onMarkActivate).toHaveBeenCalledWith(
      expect.objectContaining({ dimension: 'suite', value: 'admin', context: [{ dimension: 'day', value: '2026-09-03' }] }),
      'rows',
    )
  })

  it('no activation without both the dimensions and a handler', async () => {
    engine.instance.on.mockClear()
    renderFrame(ready(heatmapFrameWorstFirst), { markDimensions: { row: 'suite', column: 'day' } })
    await drawnOption()
    expect(engine.instance.on).not.toHaveBeenCalled()
  })

  it('accepts a status matrix: no fit, its statuses drawn', async () => {
    renderFrame(ready(heatmapFrameStatus), { fit: true })
    const option = await drawnOption()
    expect(option.visualMap.find((v) => v.id === 'ramp')).toBeUndefined()
  })
})

describe('heatmap frame fixtures', () => {
  it('are endpoint responses: percent points with a unit, keys, counts, in the server order', () => {
    for (const fixture of [heatmapFrameWorstFirst, heatmapFrameDense, heatmapFrameHostile, heatmapFrameEdges]) {
      const series: MatrixChart = fixture.series
      expect(series.unit).toBe('percent')
      expect(series.x_keys).toHaveLength(series.x_labels.length)
      expect(series.y_keys).toHaveLength(series.y_labels.length)
      const values = series.cells.map((cell) => cell.value).filter((v): v is number => typeof v === 'number')
      expect(Math.max(...values)).toBeGreaterThan(1)
      for (const cell of series.cells) {
        const sum = cell.counts ? Object.values(cell.counts).reduce((a, b) => a + b, 0) : -1
        expect(sum).toBe(cell.n)
      }
    }
  })
})
