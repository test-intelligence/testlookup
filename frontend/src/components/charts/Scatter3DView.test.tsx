/**
 * The 3D scatter view (VIZ-508) with the engine loader, the WebGL probe and
 * the tokens mocked: no WebGL in jsdom, so what is tested is the view's
 * contract with its engine — when it loads, what it hands over, what it
 * frees, and what it tells its host.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PointsChart } from '@/lib/viz/contracts'
import type { ChartTokens } from './tokens'
import type { MarkActivateHandler } from './marks'
import type { Scatter3DCallbacks, Scatter3DColors, Scatter3DHandle, Scatter3DLayout } from './scatter3d.model'

const webgl = vi.hoisted(() => ({ available: true }))
vi.mock('./webglSupport', () => ({ hasWebGL2: () => webgl.available }))

const engine = vi.hoisted(() => {
  const handle = {
    update: vi.fn(),
    setColors: vi.fn(),
    resize: vi.fn(),
    pick: vi.fn((): number | null => null),
    resetView: vi.fn(),
    dispose: vi.fn(),
  }
  const calls: { layout: unknown; colors: unknown; callbacks: unknown }[] = []
  const mountScatter3D = vi.fn((_host: HTMLElement, layout: unknown, colors: unknown, callbacks: unknown) => {
    calls.push({ layout, colors, callbacks })
    return handle
  })
  return { handle, calls, mountScatter3D, load: vi.fn() }
})
vi.mock('./engines/three/load', () => ({ loadScatter3D: engine.load }))

const theme = vi.hoisted(() => ({ tokens: null as unknown }))
vi.mock('./tokens', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./tokens')>()),
  useChartTokens: () => theme.tokens,
}))

import Scatter3DView from './Scatter3DView'
import { StaleBuildError, STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { EXPORT_NOTE, RESET_VIEW_LABEL, scatter3DColors } from './scatter3d.model'

const HOSTILE = '<img src=x onerror="window.__xss=1">'

const CHART: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [
    { id: 'fp-a', label: 'a', x: 12, y: 0, size: 10, n: 9 },
    { id: 'fp-b', label: HOSTILE, x: 900, y: 25, size: 40, n: 39 },
  ],
  medians: { x: 456, y: 2.5 },
}

const tokensIn = (failed: string): ChartTokens =>
  ({
    theme: failed,
    series: ['teal'],
    seq: [],
    div: [],
    status: { passed: 'green', failed, broken: 'orange', skipped: 'olive', unknown: 'gray' },
    flaky: 'purple',
    grid: 'silver',
    axis: 'black',
    card: 'white',
    border: 'silver',
    text: 'black',
    textMuted: 'gray',
  }) as ChartTokens

const lastCallbacks = () => engine.calls[engine.calls.length - 1].callbacks as Scatter3DCallbacks
const root = () => document.querySelector('[data-scatter-3d]') as HTMLElement
const plot = () => document.querySelector('[data-scatter-3d-plot]') as HTMLElement

function renderView(onUnavailable = vi.fn(), onMarkActivate?: MarkActivateHandler) {
  const result = render(<Scatter3DView data={CHART} height={320} onUnavailable={onUnavailable} onMarkActivate={onMarkActivate} />)
  return { ...result, onUnavailable }
}

const ready = () => waitFor(() => expect(root()).toHaveAttribute('data-chart-status', 'ready'))

beforeEach(() => {
  webgl.available = true
  theme.tokens = tokensIn('red')
  engine.calls.length = 0
  engine.mountScatter3D.mockClear()
  for (const fn of Object.values(engine.handle)) fn.mockClear()
  engine.handle.pick.mockReturnValue(null)
  engine.load.mockReset()
  engine.load.mockResolvedValue({ mountScatter3D: engine.mountScatter3D })
})

afterEach(() => vi.useRealTimers())

describe('Scatter3DView (VIZ-508): loading', () => {
  it('no WebGL 2: reports it, and the engine chunk is never asked for', async () => {
    webgl.available = false
    const { onUnavailable } = renderView()
    await waitFor(() => expect(onUnavailable).toHaveBeenCalledWith('no-webgl'))
    expect(engine.load).not.toHaveBeenCalled()
    expect(engine.mountScatter3D).not.toHaveBeenCalled()
  })

  it('a stale build says so, with Reload', async () => {
    engine.load.mockRejectedValue(new StaleBuildError(new Error('Failed to fetch dynamically imported module')))
    renderView()
    await waitFor(() => expect(root()).toHaveAttribute('data-chart-status', 'stale-build'))
    expect(screen.getByText(STALE_BUILD_MESSAGE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: STALE_BUILD_ACTION })).toBeInTheDocument()
  })

  it('any other load failure is the draw error, and Try again loads again', async () => {
    engine.load.mockRejectedValueOnce(new Error('boom'))
    renderView()
    await waitFor(() => expect(root()).toHaveAttribute('data-chart-status', 'error'))
    expect(screen.getByText(CHART_DRAW_ERROR)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await ready()
    expect(engine.load).toHaveBeenCalledTimes(2)
  })

  it('a mount that throws (the browser refused a context) is "no WebGL"', async () => {
    engine.mountScatter3D.mockImplementationOnce(() => {
      throw new Error('Error creating WebGL context.')
    })
    const { onUnavailable } = renderView()
    await waitFor(() => expect(onUnavailable).toHaveBeenCalledWith('no-webgl'))
    expect(root()).toHaveAttribute('data-chart-status', 'loading')
  })
})

describe('Scatter3DView (VIZ-508): drawn', () => {
  it('ready: mounted once on its own host with the layout and the quadrant colour tokens; the key and the export note shown', async () => {
    renderView()
    await ready()
    expect(engine.mountScatter3D).toHaveBeenCalledTimes(1)
    expect(engine.mountScatter3D.mock.calls[0][0]).toBe(plot())
    const { layout, colors } = engine.calls[0] as { layout: Scatter3DLayout; colors: Scatter3DColors }
    expect(layout.positions).toHaveLength(6)
    expect(colors).toEqual(scatter3DColors(tokensIn('red')))
    expect(colors.quadrants['slow-flaky']).toBe('red')
    expect(root()).toHaveAttribute('data-chart-engine', 'three')
    expect(document.querySelector('[data-scatter-key]')).not.toBeNull()
    expect(screen.getByText(new RegExp(EXPORT_NOTE))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: RESET_VIEW_LABEL }))
    expect(engine.handle.resetView).toHaveBeenCalledTimes(1)
  })

  it('a theme switch re-colours the drawn view in place', async () => {
    const { rerender, onUnavailable } = renderView()
    await ready()
    theme.tokens = tokensIn('crimson')
    rerender(<Scatter3DView data={CHART} height={320} onUnavailable={onUnavailable} />)
    await waitFor(() => expect(engine.handle.setColors).toHaveBeenLastCalledWith(scatter3DColors(tokensIn('crimson'))))
    expect((engine.handle.setColors.mock.lastCall?.[0] as Scatter3DColors).quadrants['slow-flaky']).toBe('crimson')
    expect(engine.mountScatter3D).toHaveBeenCalledTimes(1)
  })

  it('the camera azimuth is written on the root for the reader of the DOM (and the e2e drag test)', async () => {
    renderView()
    await ready()
    act(() => lastCallbacks().onCameraChange?.(42))
    expect(root()).toHaveAttribute('data-scatter-3d-azimuth', '42')
  })

  it('a lost context tells the host', async () => {
    const { onUnavailable } = renderView()
    await ready()
    act(() => lastCallbacks().onContextLost())
    expect(onUnavailable).toHaveBeenCalledWith('context-lost')
  })
})

describe('Scatter3DView (VIZ-508): freeing the GPU', () => {
  it('unmount disposes exactly once', async () => {
    const { unmount } = renderView()
    await ready()
    unmount()
    expect(engine.handle.dispose).toHaveBeenCalledTimes(1)
  })

  it('a load that resolves after unmount mounts nothing (so there is nothing to dispose)', async () => {
    let resolve!: (value: unknown) => void
    engine.load.mockReturnValue(new Promise((r) => (resolve = r)))
    const { unmount } = renderView()
    await waitFor(() => expect(engine.load).toHaveBeenCalled())
    unmount()
    await act(async () => resolve({ mountScatter3D: engine.mountScatter3D }))
    expect(engine.mountScatter3D).not.toHaveBeenCalled()
    expect(engine.handle.dispose).not.toHaveBeenCalled()
  })

  it('20 toggles in and out of 3D: 20 mounts, 20 disposes', async () => {
    for (let i = 0; i < 20; i++) {
      const { unmount } = renderView()
      await ready()
      unmount()
    }
    expect(engine.mountScatter3D).toHaveBeenCalledTimes(20)
    expect(engine.handle.dispose).toHaveBeenCalledTimes(20)
  })
})

describe('Scatter3DView (VIZ-508): the pointer', () => {
  it('hovering a point shows its tooltip as React text: a hostile name is characters, never markup', async () => {
    renderView()
    await ready()
    engine.handle.pick.mockReturnValue(1)
    fireEvent.pointerMove(plot(), { clientX: 30, clientY: 40 })
    await waitFor(() => expect(screen.getByText(HOSTILE)).toBeInTheDocument())
    expect(document.querySelector('img')).toBeNull()
    expect(engine.handle.pick).toHaveBeenCalled()
    fireEvent.pointerLeave(plot())
    await waitFor(() => expect(screen.queryByText(HOSTILE)).toBeNull())
  })

  it('a click on a point (no drag) opens its rows; a drag does not', async () => {
    const onMarkActivate = vi.fn<MarkActivateHandler>()
    renderView(vi.fn(), onMarkActivate)
    await ready()
    engine.handle.pick.mockReturnValue(0)
    fireEvent.pointerDown(plot(), { clientX: 30, clientY: 40 })
    fireEvent.pointerUp(plot(), { clientX: 31, clientY: 40 })
    expect(onMarkActivate).toHaveBeenCalledTimes(1)
    expect(onMarkActivate.mock.calls[0][0]).toMatchObject({ dimension: 'test', value: 'fp-a' })
    fireEvent.pointerDown(plot(), { clientX: 30, clientY: 40 })
    fireEvent.pointerUp(plot(), { clientX: 90, clientY: 40 })
    expect(onMarkActivate).toHaveBeenCalledTimes(1)
  })
})

// The handle type is what the engine returns; the mock must keep its shape.
const _shape: Scatter3DHandle = engine.handle
void _shape
