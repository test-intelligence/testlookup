import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import HeatmapChart from './HeatmapChart'
import { useEChart } from './engines/useEChart'
import type { ChartEngineType } from './engines/registry'
import { StaleBuildError } from './engines/lazyChartEngine'
import type { NumericMatrix } from './engines/echarts/heatmapOption'

// jsdom has no canvas, so the engine is mocked at the registry: the test
// asserts what the component hands ECharts (init, options, resize, dispose).
const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

// A ResizeObserver whose callback the test can fire.
const observers: { cb: () => void; disconnect: ReturnType<typeof vi.fn> }[] = []
class TestResizeObserver {
  disconnect = vi.fn()
  constructor(private cb: () => void) {
    observers.push(this as unknown as { cb: () => void; disconnect: ReturnType<typeof vi.fn> })
  }
  observe() {}
  unobserve() {}
  fire() {
    this.cb()
  }
}

const data: NumericMatrix = {
  kind: 'matrix',
  value_type: 'count',
  x_labels: ['a', 'b'],
  y_labels: ['s'],
  cells: [
    { x: 0, y: 0, value: 1, n: 1 },
    { x: 1, y: 0, value: 2, n: 2 },
  ],
}

describe('HeatmapChart', () => {
  const realResizeObserver = globalThis.ResizeObserver
  beforeEach(() => {
    observers.length = 0
    engine.load.mockReset()
    engine.init.mockClear()
    engine.instance.setOption.mockClear()
    engine.instance.resize.mockClear()
    engine.instance.dispose.mockClear()
    globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver
  })
  afterEach(() => {
    globalThis.ResizeObserver = realResizeObserver
  })

  it('loads the heatmap engine lazily, inits on canvas and applies the option', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const { container } = render(<HeatmapChart data={data} description="Two cells." />)
    expect(engine.load).toHaveBeenCalledWith('heatmap')
    await waitFor(() => expect(engine.init).toHaveBeenCalledTimes(1))
    const [el, theme, opts] = engine.init.mock.calls[0] as unknown as [HTMLElement, unknown, unknown]
    expect(el).toBe(container.querySelector('[data-chart-type="heatmap"]'))
    expect(theme).toBeNull()
    expect(opts).toEqual({ renderer: 'canvas' })

    const [option, setOpts] = engine.instance.setOption.mock.calls[0] as unknown as [
      { series: { type: string }[]; tooltip: { formatter: unknown }; animation: boolean },
      unknown,
    ]
    expect(setOpts).toEqual({ notMerge: true })
    expect(option.series[0].type).toBe('heatmap')
    expect(option.animation).toBe(false)
    // No string formatter ever reaches ECharts.
    expect(typeof option.tooltip.formatter).toBe('function')
    expect((option.tooltip.formatter as (p: unknown) => unknown)({ value: [0, 0, 1] })).toBeInstanceOf(HTMLElement)
    await waitFor(() => expect(el).toHaveAttribute('data-chart-status', 'ready'))
  })

  it('resizes with its container and disposes the instance on unmount', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const view = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(observers).toHaveLength(1))
    act(() => (observers[0] as unknown as TestResizeObserver).fire())
    expect(engine.instance.resize).toHaveBeenCalledTimes(1)
    view.unmount()
    expect(engine.instance.dispose).toHaveBeenCalledTimes(1)
    expect(observers[0].disconnect).toHaveBeenCalled()
  })

  it('replaces the option in place when the data changes', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const view = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const next = { ...data, cells: [{ x: 0, y: 0, value: 9, n: 9 }] }
    view.rerender(<HeatmapChart data={next} description="One cell." />)
    const calls = engine.instance.setOption.mock.calls
    const last = calls[calls.length - 1] as unknown as [{ series: { data: unknown[] }[] }]
    expect(last[0].series[0].data).toEqual([[0, 0, 9]])
    expect(engine.init).toHaveBeenCalledTimes(1)
  })

  it('shows "A new version is available — Reload" when the engine chunk is gone', async () => {
    engine.load.mockRejectedValue(new StaleBuildError(new TypeError('Failed to fetch dynamically imported module')))
    const reload = vi.fn()
    const location = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { ...location, reload } })
    try {
      render(<HeatmapChart data={data} description="Two cells." />)
      const alert = await waitFor(() => {
        const node = document.querySelector('[data-chart-draw-error="stale-build"]')
        if (!node) throw new Error('no stale-build message yet')
        return node
      })
      expect(alert).toHaveTextContent('A new version is available')
      expect(alert).not.toHaveAttribute('role')
      fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
      expect(reload).toHaveBeenCalledTimes(1)
      expect(engine.init).not.toHaveBeenCalled()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: location })
    }
  })

  it('shows a plain error (no Reload) when the engine fails for another reason', async () => {
    engine.load.mockRejectedValue(new Error('boom'))
    render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(document.querySelector('[data-chart-draw-error="error"]')).toHaveTextContent('This chart could not be drawn.'))
    expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull()
  })

  it('arrow keys highlight a cell through ECharts actions and announce the SAME tooltip content', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    engine.instance.dispatchAction.mockClear()
    const { container } = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = container.querySelector('[data-chart-keyboard]') as HTMLElement
    expect(chart).toHaveAttribute('tabindex', '0')
    expect(chart).toHaveAttribute('role', 'group')
    expect(chart.getAttribute('aria-label')).toContain('Two cells.')
    chart.focus()

    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'highlight', seriesIndex: 0, dataIndex: 0 })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'showTip', seriesIndex: 0, dataIndex: 0 })
    // The announcement is the formatter's own content, as text.
    const [option] = engine.instance.setOption.mock.calls[0] as unknown as [{ tooltip: { formatter: (p: unknown) => HTMLElement } }]
    const node = option.tooltip.formatter({ value: [0, 0, 1] })
    const parts = Array.from(node.querySelectorAll('.chart-tooltip-title, .chart-tooltip-label, .chart-tooltip-value'), (el) => el.textContent)
    const announcement = container.querySelector('[data-chart-announcement]') as HTMLElement
    expect(announcement).toHaveAttribute('aria-live', 'polite')
    expect(announcement.textContent).toBe('s. a: 1. Samples: 1')
    for (const part of parts) expect(announcement.textContent).toContain(part)

    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'downplay', seriesIndex: 0, dataIndex: 0 })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'highlight', seriesIndex: 0, dataIndex: 1 })
    expect(announcement.textContent).toBe('s. b: 2. Samples: 2')

    fireEvent.keyDown(chart, { key: 'Escape' })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'downplay', seriesIndex: 0, dataIndex: 1 })
    expect(engine.instance.dispatchAction).toHaveBeenLastCalledWith({ type: 'hideTip' })
    expect(announcement.textContent).toBe('')
    expect(document.activeElement).toBe(chart)
  })

  it('never animates under prefers-reduced-motion, even when asked to', async () => {
    const realMatchMedia = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia
    try {
      engine.load.mockResolvedValue({ init: engine.init })
      render(<HeatmapChart data={data} description="Two cells." animate />)
      await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
      const [option] = engine.instance.setOption.mock.calls[0] as unknown as [{ animation: boolean }]
      expect(option.animation).toBe(false)
    } finally {
      window.matchMedia = realMatchMedia
    }
  })

  it('an init that throws is the error state — not an unhandled rejection stuck on loading', async () => {
    engine.load.mockResolvedValue({
      init: () => {
        throw new Error('init exploded')
      },
    })
    const { container } = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(container.querySelector('[data-chart-draw-error]')).not.toBeNull())
    expect(container).toHaveTextContent('This chart could not be drawn.')
  })

  it('a setOption that throws on new data is the error state; "Try again" re-inits', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const view = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    engine.instance.setOption.mockImplementationOnce(() => {
      throw new Error('setOption exploded')
    })
    view.rerender(<HeatmapChart data={{ ...data, cells: [{ x: 0, y: 0, value: 3, n: 3 }] }} description="One cell." />)
    await waitFor(() => expect(view.container.querySelector('[data-chart-draw-error]')).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(engine.init).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(view.container.querySelector('[data-chart-status="ready"]')).not.toBeNull())
  })

  it('a failed engine load offers "Try again", which loads again', async () => {
    engine.load.mockRejectedValueOnce(new Error('boom')).mockResolvedValue({ init: engine.init })
    render(<HeatmapChart data={data} description="Two cells." />)
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(engine.init).toHaveBeenCalledTimes(1))
    expect(engine.load).toHaveBeenCalledTimes(2)
  })

  it('useEChart: a chart-type change goes back to loading until the new engine is ready', async () => {
    let resolveSecond: (value: unknown) => void = () => {}
    engine.load.mockImplementation((type: string) =>
      type === 'heatmap' ? Promise.resolve({ init: engine.init }) : new Promise((r) => (resolveSecond = r)),
    )
    const { result, rerender } = renderHook(({ type }: { type: string }) => useEChart(type as ChartEngineType, {}), {
      initialProps: { type: 'heatmap' },
    })
    // The container ref is unattached in renderHook: give it an element.
    result.current.containerRef.current = document.createElement('div')
    rerender({ type: 'heatmap' })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    rerender({ type: 'other' })
    expect(result.current.status).toBe('loading')
    await act(async () => resolveSecond({ init: engine.init }))
    await waitFor(() => expect(result.current.status).toBe('ready'))
  })

  it('keyboard hint: "Arrow keys move, Escape clears, Tab leaves" — a real element the chart is described by', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const { container } = render(<HeatmapChart data={data} description="Two cells." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = container.querySelector('[data-chart-keyboard]') as HTMLElement
    // Named once (our description); the hint is a visible-on-focus element, not only an aria-label.
    expect(chart).toHaveAttribute('aria-label', 'Two cells.')
    const hint = document.getElementById(chart.getAttribute('aria-describedby') ?? '')
    expect(hint).toHaveAttribute('data-chart-keyboard-hint')
    expect(hint).toHaveTextContent('Arrow keys move, Escape clears, Tab leaves')
    expect(hint?.className).toMatch(/group-focus-visible:/)
    expect(chart.className).toMatch(/\bgroup\b/)
  })

  it('Escape stops propagating only when it cleared a highlight (so a SidePanel\'s Escape still closes it)', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    const outer = vi.fn()
    const { container } = render(
      <div onKeyDown={(event) => outer(event.key)}>
        <HeatmapChart data={data} description="Two cells." />
      </div>,
    )
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = container.querySelector('[data-chart-keyboard]') as HTMLElement
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    outer.mockClear()
    fireEvent.keyDown(chart, { key: 'Escape' }) // clears the highlight: consumed
    expect(outer).not.toHaveBeenCalled()
    fireEvent.keyDown(chart, { key: 'Escape' }) // nothing to clear: the panel gets it
    expect(outer).toHaveBeenCalledWith('Escape')
  })

  it('a no-data cell is highlighted in the no-data series, and announced as "No data"', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    engine.instance.dispatchAction.mockClear()
    const withGap: NumericMatrix = { ...data, cells: [data.cells[0], { x: 1, y: 0, value: null, n: 0 }] }
    const { container } = render(<HeatmapChart data={withGap} description="Two cells." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = container.querySelector('[data-chart-keyboard]') as HTMLElement
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(engine.instance.dispatchAction).toHaveBeenCalledWith({ type: 'highlight', seriesIndex: 1, dataIndex: 0 })
    // …and series 1, item 0 really is that cell, drawn hatched.
    const calls = engine.instance.setOption.mock.calls
    const [drawn] = calls[calls.length - 1] as unknown as [{ series: { id: string; data: unknown[]; itemStyle: { decal?: unknown } }[] }]
    expect(drawn.series[1]).toMatchObject({ id: 'no-data', data: [[1, 0, 0]] })
    expect(drawn.series[1].itemStyle.decal).toBeDefined()
    expect(container.querySelector('[data-chart-announcement]')).toHaveTextContent('b: No data')
  })

  it('never inits after unmounting mid-load', async () => {
    let resolve: (value: unknown) => void = () => {}
    engine.load.mockReturnValue(new Promise((r) => (resolve = r)))
    const view = render(<HeatmapChart data={data} description="Two cells." />)
    view.unmount()
    await act(async () => resolve({ init: engine.init }))
    expect(engine.init).not.toHaveBeenCalled()
  })
})
