import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SidePanel from '@/components/ui/SidePanel'
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import TestScatter, {
  CLEAR_SELECTION_LABEL,
  DRAG_SELECT_LABEL,
  NO_SALIENT_MESSAGE,
  SELECT_SALIENT_LABEL,
} from './TestScatter'
import { sameRect, scatterMark } from './testScatter.model'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { StaleBuildError } from './engines/lazyChartEngine'

// jsdom has no canvas: the engine is mocked at the registry, and the test
// asserts what the component hands ECharts (options, actions) and what it
// does with the events ECharts would raise (brushEnd, click).
const engine = vi.hoisted(() => {
  const handlers = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    isDisposed: vi.fn(() => false),
    on: vi.fn((name: string, handler: (params: unknown) => void) => handlers.set(name, handler)),
    off: vi.fn((name: string) => handlers.delete(name)),
  }
  return { instance, handlers, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

const HOSTILE = '<img src=x onerror="window.__xss=1">'
const point = (id: string, x: number, y: number, size = 10, label = id): PointsChartPoint => ({ id, label, x, y, size, n: size })

// Medians x 100, y 0: slow = x > 100, flaky = y > 0.
const data: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [
    point('fp-fast-ok', 10, 0),
    point('fp-slow-flaky', 900, 25, 40, HOSTILE),
    point('fp-median', 100, 0),
    point('fp-slow-ok', 400, 0),
    point('fp-fast-flaky', 50, 60),
    point('fp-slow-flaky-2', 3000, 5),
  ],
  medians: { x: 100, y: 0 },
  excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 },
}

const lastOption = () => {
  const calls = engine.instance.setOption.mock.calls
  return calls[calls.length - 1]?.[0]
}
const actions = () => engine.instance.dispatchAction.mock.calls.map(([payload]) => payload as Record<string, unknown>)
const lastAction = (type: string) => [...actions()].reverse().find((a) => a.type === type)

async function renderScatter(props: Partial<Parameters<typeof TestScatter>[0]> = {}, { announcer = true } = {}) {
  const ui = <TestScatter data={data} description="Six tests." {...props} />
  const result = render(announcer ? <ChartAnnouncerProvider>{ui}</ChartAnnouncerProvider> : ui)
  await waitFor(() => expect(result.container.querySelector('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready'))
  await waitFor(() => expect(engine.handlers.has('brushEnd')).toBe(true))
  return result
}

const assertive = (container: HTMLElement) => container.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''

describe('TestScatter', () => {
  const realResizeObserver = globalThis.ResizeObserver
  beforeEach(() => {
    engine.load.mockReset()
    engine.load.mockResolvedValue({ init: engine.init })
    engine.init.mockClear()
    engine.handlers.clear()
    for (const fn of Object.values(engine.instance)) fn.mockClear()
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  })
  afterEach(() => {
    globalThis.ResizeObserver = realResizeObserver
  })

  it('loads the scatter engine lazily and hands it the scatter option (no large mode, no string formatter)', async () => {
    const { container } = await renderScatter()
    expect(engine.load).toHaveBeenCalledWith('scatter')
    const [option, opts] = engine.instance.setOption.mock.calls[0] as unknown as [
      { series: { type: string; large: boolean }[]; xAxis: { type: string }; tooltip: { formatter: unknown } },
      unknown,
    ]
    expect(opts).toEqual({ notMerge: true })
    expect(option.series[0]).toMatchObject({ type: 'scatter', large: false })
    expect(option.xAxis.type).toBe('log')
    expect(typeof option.tooltip.formatter).toBe('function')
    // The canvas draws no name at all: no hostile label reaches the DOM from the plot.
    expect(container.querySelector('img')).toBeNull()
  })

  it('a dragged rectangle selects OUR filter of its data range, inclusive, and announces the count', async () => {
    const onSelectionChange = vi.fn()
    const { container } = await renderScatter({ onSelectionChange })
    act(() => engine.handlers.get('brushEnd')?.({ areas: [{ brushType: 'rect', coordRange: [[100, 900], [0, 30]] }] }))
    expect(onSelectionChange).toHaveBeenLastCalledWith([1, 2, 3], 'brush')
    expect(assertive(container)).toBe('3 tests selected')
    expect(screen.getByRole('button', { name: CLEAR_SELECTION_LABEL })).toBeInTheDocument()
  })

  it('a cleared brush reports no selection', async () => {
    const onSelectionChange = vi.fn()
    await renderScatter({ onSelectionChange })
    act(() => engine.handlers.get('brushEnd')?.({ areas: [{ brushType: 'rect', coordRange: [[1, 10], [0, 1]] }] }))
    act(() => engine.handlers.get('brushEnd')?.({ areas: [] }))
    expect(onSelectionChange).toHaveBeenLastCalledWith(null, 'brush')
    expect(screen.queryByRole('button', { name: CLEAR_SELECTION_LABEL })).toBeNull()
  })

  it('"Select slow and flaky" brushes EXACTLY the salient quadrant (the keyboard path) and lists the same tests', async () => {
    const onSelectionChange = vi.fn()
    await renderScatter({ onSelectionChange })
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    // Strictly above both medians: not the test ON the x median, not a 0% test.
    expect(onSelectionChange).toHaveBeenLastCalledWith([1, 5], 'quadrant')
    expect(lastAction('brush')).toEqual({
      type: 'brush',
      areas: [{ brushType: 'rect', xAxisIndex: 0, yAxisIndex: 0, coordRange: [[400, 3000], [5, 60]] }],
    })
    // The brushEnd a programmatic brush may raise is the same rectangle: not reported twice.
    onSelectionChange.mockClear()
    act(() => engine.handlers.get('brushEnd')?.({ areas: [{ brushType: 'rect', coordRange: [[400, 3000], [5, 60]] }] }))
    expect(onSelectionChange).not.toHaveBeenCalled()
  })

  it('says so when no test is slow and flaky, and clears any rectangle', async () => {
    const onSelectionChange = vi.fn()
    const healthy = { ...data, points: data.points.map((p) => ({ ...p, y: 0 })), medians: { x: 100, y: 0 } }
    const { container } = await renderScatter({ data: healthy, onSelectionChange })
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    expect(onSelectionChange).toHaveBeenLastCalledWith(null, 'quadrant')
    expect(lastAction('brush')).toEqual({ type: 'brush', areas: [] })
    expect(container.querySelector('[data-scatter-notice]')?.textContent).toBe(NO_SALIENT_MESSAGE)
    expect(assertive(container)).toBe(NO_SALIENT_MESSAGE)
  })

  it('"Clear selection" removes the rectangle and the selection', async () => {
    const onSelectionChange = vi.fn()
    await renderScatter({ onSelectionChange })
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    fireEvent.click(screen.getByRole('button', { name: CLEAR_SELECTION_LABEL }))
    expect(onSelectionChange).toHaveBeenLastCalledWith(null, 'clear')
    expect(lastAction('brush')).toEqual({ type: 'brush', areas: [] })
  })

  it('"Drag to select" takes and releases the brush cursor, pressed state in step', async () => {
    await renderScatter()
    const toggle = screen.getByRole('button', { name: DRAG_SELECT_LABEL })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(lastAction('takeGlobalCursor')).toEqual({
      type: 'takeGlobalCursor',
      key: 'brush',
      brushOption: { brushType: 'rect', brushMode: 'single' },
    })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(lastAction('takeGlobalCursor')).toEqual({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: false } })
  })

  it('drag mode re-applies the option with a tooltip that does not take the pointer, then takes the brush again', async () => {
    await renderScatter()
    engine.instance.setOption.mockClear()
    engine.instance.dispatchAction.mockClear()
    fireEvent.click(screen.getByRole('button', { name: DRAG_SELECT_LABEL }))
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const applied = lastOption() as { tooltip: { enterable: boolean } }
    expect(applied.tooltip.enterable).toBe(false)
    expect(lastAction('takeGlobalCursor')).toEqual({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'rect', brushMode: 'single' } })
    fireEvent.click(screen.getByRole('button', { name: DRAG_SELECT_LABEL }))
    await waitFor(() =>
      expect((lastOption() as { tooltip: { enterable: boolean } }).tooltip.enterable).toBe(true),
    )
  })

  it('a re-applied option (theme switch) gets the drag mode and the rectangle back', async () => {
    const { rerender } = await renderScatter()
    fireEvent.click(screen.getByRole('button', { name: DRAG_SELECT_LABEL }))
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    engine.instance.dispatchAction.mockClear()
    // A new option object for the same data (what a token change does).
    rerender(
      <ChartAnnouncerProvider>
        <TestScatter data={data} description="Six tests." animate />
      </ChartAnnouncerProvider>,
    )
    await waitFor(() => expect(lastAction('brush')).toBeDefined())
    expect(lastAction('takeGlobalCursor')).toMatchObject({ brushOption: { brushType: 'rect' } })
    expect(lastAction('brush')).toMatchObject({ areas: [{ coordRange: [[400, 3000], [5, 60]] }] })
  })

  it('new data clears the selection and its rectangle', async () => {
    const { rerender } = await renderScatter()
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    expect(screen.getByRole('button', { name: CLEAR_SELECTION_LABEL })).toBeInTheDocument()
    engine.instance.dispatchAction.mockClear()
    rerender(
      <ChartAnnouncerProvider>
        <TestScatter data={{ ...data, points: data.points.slice(0, 3) }} description="Three tests." />
      </ChartAnnouncerProvider>,
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR_SELECTION_LABEL })).toBeNull())
    expect(lastAction('brush')).toBeUndefined()
  })

  it('arrow keys walk the tests fastest to slowest through ECharts actions and the page announcer', async () => {
    const { container } = await renderScatter()
    const plot = container.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
    expect(plot).toHaveAttribute('tabindex', '0')
    expect(plot).toHaveAttribute('aria-label', 'Six tests.')
    fireEvent.keyDown(plot, { key: 'ArrowRight' })
    // Fastest first: fp-fast-ok (x 10) is index 0.
    expect(lastAction('highlight')).toEqual({ type: 'highlight', seriesIndex: 0, dataIndex: 0 })
    expect(lastAction('showTip')).toEqual({ type: 'showTip', seriesIndex: 0, dataIndex: 0 })
    expect(assertive(container)).toContain('fp-fast-ok. p95 duration (ms): 10 ms')
    fireEvent.keyDown(plot, { key: 'ArrowRight' })
    expect(lastAction('downplay')).toEqual({ type: 'downplay', seriesIndex: 0, dataIndex: 0 })
    expect(lastAction('highlight')).toEqual({ type: 'highlight', seriesIndex: 0, dataIndex: 4 })
    fireEvent.keyDown(plot, { key: 'End' })
    expect(lastAction('highlight')).toEqual({ type: 'highlight', seriesIndex: 0, dataIndex: 5 })
    fireEvent.keyDown(plot, { key: 'Escape' })
    expect(lastAction('hideTip')).toEqual({ type: 'hideTip' })
    // No live region of its own: the page announcer is the one voice.
    expect(container.querySelector('[aria-live]:not([data-chart-announcer])')).toBeNull()
  })

  it('says the hostile name of a focused test as text, never markup', async () => {
    const { container } = await renderScatter()
    const plot = container.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
    fireEvent.keyDown(plot, { key: 'End' })
    fireEvent.keyDown(plot, { key: 'ArrowLeft' })
    expect(assertive(container)).toContain(HOSTILE)
    expect(container.querySelector('img')).toBeNull()
  })

  describe('point activation (marks)', () => {
    it('without onMarkActivate: Enter does nothing, no buttons, no activation hint', async () => {
      const { container } = await renderScatter()
      const plot = container.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
      fireEvent.keyDown(plot, { key: 'ArrowRight' })
      fireEvent.keyDown(plot, { key: 'Enter' })
      expect(container.querySelector('[data-mark-actions]')).toBeNull()
      expect(container.querySelector('[data-chart-keyboard-hint]')?.textContent).not.toContain('Enter')
    })

    it('Enter on the focused test, its readout button and a click all open the test (rows)', async () => {
      const onMarkActivate = vi.fn()
      const { container } = await renderScatter({ onMarkActivate, markIntents: () => ['rows'] })
      const plot = container.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
      expect(container.querySelector('[data-chart-keyboard-hint]')?.textContent).toContain('Enter opens the focused test')
      fireEvent.keyDown(plot, { key: 'End' })
      fireEvent.keyDown(plot, { key: 'Enter' })
      const expected = { dimension: 'test', value: 'fp-slow-flaky-2', label: 'fp-slow-flaky-2', y: 5, n: 10 }
      expect(onMarkActivate).toHaveBeenLastCalledWith(expected, 'rows')

      const button = within(container.querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' })
      fireEvent.click(button)
      expect(onMarkActivate).toHaveBeenCalledTimes(2)

      act(() => engine.handlers.get('click')?.({ seriesIndex: 0, dataIndex: 1, event: { event: { shiftKey: false } } }))
      expect(onMarkActivate).toHaveBeenLastCalledWith(
        { dimension: 'test', value: 'fp-slow-flaky', label: HOSTILE, y: 25, n: 40 },
        'rows',
      )
    })

    it('the readout "View rows" hands focus to the plot first, so closing the panel it opens returns there (F-24)', async () => {
      // The real panel: it remembers the element focused when it opened and
      // gives focus back only if that element is still in the page. The
      // readout button is not (the plot clears its point when focus leaves).
      function Host() {
        const [open, setOpen] = useState(false)
        return (
          <>
            <TestScatter data={data} description="Six tests." onMarkActivate={() => setOpen(true)} markIntents={() => ['rows']} />
            <SidePanel open={open} onClose={() => setOpen(false)} title="Rows" closeLabel="Close executions">
              <p>rows</p>
            </SidePanel>
          </>
        )
      }
      const { container } = render(
        <ChartAnnouncerProvider>
          <Host />
        </ChartAnnouncerProvider>,
      )
      await waitFor(() => expect(container.querySelector('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready'))
      const plot = container.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
      plot.focus()
      fireEvent.keyDown(plot, { key: 'ArrowRight' })
      const button = within(container.querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' })
      button.focus()
      fireEvent.click(button)
      const close = screen.getByRole('button', { name: 'Close executions' })
      expect(document.activeElement).toBe(close)
      expect(container.querySelector('[data-mark-actions]')).toBeNull()
      fireEvent.click(close)
      expect(document.activeElement).toBe(plot)
    })

    it('a touch tap selects nothing and activates nothing; a click on no point is ignored', async () => {
      const onMarkActivate = vi.fn()
      await renderScatter({ onMarkActivate })
      act(() => engine.handlers.get('click')?.({ seriesIndex: 0, dataIndex: 1, event: { event: { pointerType: 'touch' } } }))
      act(() => engine.handlers.get('click')?.({ seriesIndex: 1, dataIndex: 1 }))
      act(() => engine.handlers.get('click')?.({ dataIndex: 99 }))
      act(() => engine.handlers.get('click')?.(null))
      expect(onMarkActivate).not.toHaveBeenCalled()
    })

    it('a click with no handler is ignored, and a click with a modifier uses the filter intent when offered', async () => {
      const onMarkActivate = vi.fn()
      await renderScatter({ onMarkActivate, markIntents: () => ['rows', 'filter'] })
      act(() => engine.handlers.get('click')?.({ dataIndex: 0, event: { event: { ctrlKey: true } } }))
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: 'fp-fast-ok' }), 'filter')
      act(() => engine.handlers.get('click')?.({ dataIndex: 0, event: { event: 'not an event' } }))
      expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: 'fp-fast-ok' }), 'rows')
    })
  })

  it('the key names every quadrant with its shape and count, the medians and the rule', async () => {
    const { container } = await renderScatter()
    const key = container.querySelector('[data-scatter-key]') as HTMLElement
    expect(within(key).getByText('Slow and flaky (2)')).toBeInTheDocument()
    expect(within(key).getByText('Fast and flaky (1)')).toBeInTheDocument()
    expect(within(key).getByText('Slow and stable (1)')).toBeInTheDocument()
    expect(within(key).getByText('Fast and stable (2)')).toBeInTheDocument()
    expect(within(key).getByText('Medians')).toBeInTheDocument()
    const shapes = ['slow-flaky', 'fast-flaky', 'slow-stable', 'fast-stable'].map(
      (q) => key.querySelector(`[data-quadrant="${q}"] svg > *`)?.tagName,
    )
    expect(shapes).toEqual(['polygon', 'polygon', 'rect', 'circle'])
  })

  it('draw errors: Try again starts over; a stale build offers the reload', async () => {
    engine.load.mockRejectedValueOnce(new Error('boom'))
    const { container, unmount } = render(<TestScatter data={data} description="Six tests." />)
    const retry = await screen.findByRole('button', { name: 'Try again' })
    expect(container.querySelector('[data-chart-draw-error="error"]')).not.toBeNull()
    fireEvent.click(retry)
    await waitFor(() => expect(container.querySelector('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready'))
    unmount()

    engine.load.mockRejectedValueOnce(new StaleBuildError(new Error('404')))
    const stale = render(<TestScatter data={data} description="Six tests." />)
    await waitFor(() => expect(stale.container.querySelector('[data-chart-draw-error="stale-build"]')).not.toBeNull())
  })

  it('works with no page announcer (nothing is said, nothing throws)', async () => {
    await renderScatter({}, { announcer: false })
    fireEvent.click(screen.getByRole('button', { name: SELECT_SALIENT_LABEL }))
    expect(screen.getByRole('button', { name: CLEAR_SELECTION_LABEL })).toBeInTheDocument()
  })
})

describe('helpers', () => {
  it('sameRect compares ranges, and "none" only equals "none"', () => {
    const a = { x: [1, 2] as const, y: [3, 4] as const }
    expect(sameRect(a, { x: [1, 2], y: [3, 4] })).toBe(true)
    expect(sameRect(a, { x: [1, 2], y: [3, 5] })).toBe(false)
    expect(sameRect(a, { x: [0, 2], y: [3, 4] })).toBe(false)
    expect(sameRect(null, null)).toBe(true)
    expect(sameRect(a, null)).toBe(false)
  })
  it('scatterMark is the test, keyed by fingerprint; null for no point', () => {
    expect(scatterMark(data, 0)).toEqual({ dimension: 'test', value: 'fp-fast-ok', label: 'fp-fast-ok', y: 0, n: 10 })
    expect(scatterMark(data, 42)).toBeNull()
  })
})
