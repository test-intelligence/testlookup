/**
 * Wave 3 (FK0 M0b): `useEChart`'s `events` and `getInstance`. No events = no
 * listener at all (every pre-Wave-3 chart); handlers are read through a ref,
 * so a new handler object every render re-binds nothing.
 */
import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartInstance } from './registry'
import { useEChart, type EChartEvents } from './useEChart'

type Listener = (params: unknown) => void

const engine = vi.hoisted(() => {
  const listeners = new Map<string, Set<(params: unknown) => void>>()
  let disposed = false
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(() => {
      disposed = true
    }),
    isDisposed: () => disposed,
    on: vi.fn((name: string, fn: (params: unknown) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name)?.add(fn)
    }),
    off: vi.fn((name: string, fn: (params: unknown) => void) => listeners.get(name)?.delete(fn)),
  }
  return {
    instance,
    listeners,
    reset: () => {
      disposed = false
      listeners.clear()
    },
    init: vi.fn(() => instance),
    load: vi.fn(),
  }
})
vi.mock('./registry', () => ({ loadChartEngine: engine.load }))

class NoopResizeObserver {
  observe() {}
  disconnect() {}
}

const OPTION = { series: [] }
let latest: ReturnType<typeof useEChart> | null = null

const expose = (chart: ReturnType<typeof useEChart>) => {
  latest = chart
}

function Probe({ events }: { events?: EChartEvents }) {
  const chart = useEChart('heatmap', OPTION, events)
  expose(chart)
  const { containerRef, status } = chart
  return <div ref={containerRef} data-status={status} />
}

const emit = (name: string, params: unknown) => {
  for (const fn of engine.listeners.get(name) ?? []) (fn as Listener)(params)
}

describe('useEChart events', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', NoopResizeObserver)
    engine.reset()
    engine.instance.on.mockClear()
    engine.instance.off.mockClear()
    engine.instance.dispose.mockClear()
    engine.load.mockReset().mockResolvedValue({ init: engine.init })
    latest = null
  })

  it('binds nothing when no events are given', async () => {
    const view = render(<Probe />)
    await waitFor(() => expect(view.container.firstElementChild?.getAttribute('data-status')).toBe('ready'))
    expect(engine.instance.on).not.toHaveBeenCalled()
  })

  it('binds each named event once the instance is ready, and calls the LATEST handler', async () => {
    const first = vi.fn()
    const second = vi.fn()
    const view = render(<Probe events={{ click: first }} />)
    await waitFor(() => expect(engine.instance.on).toHaveBeenCalledTimes(1))
    emit('click', { dataIndex: 3 })
    expect(first).toHaveBeenCalledWith({ dataIndex: 3 })
    // A new handler object with the same names: no re-binding, the new handler runs.
    await act(async () => view.rerender(<Probe events={{ click: second }} />))
    expect(engine.instance.on).toHaveBeenCalledTimes(1)
    emit('click', { dataIndex: 4 })
    expect(second).toHaveBeenCalledWith({ dataIndex: 4 })
    expect(first).toHaveBeenCalledTimes(1)
  })

  it('re-binds when the set of names changes, and unbinds the old ones', async () => {
    const view = render(<Probe events={{ click: vi.fn() }} />)
    await waitFor(() => expect(engine.instance.on).toHaveBeenCalledTimes(1))
    const brushEnd = vi.fn()
    await act(async () => view.rerender(<Probe events={{ click: vi.fn(), brushEnd }} />))
    expect(engine.instance.off).toHaveBeenCalledWith('click', expect.any(Function))
    expect(engine.listeners.get('click')?.size).toBe(1)
    emit('brushEnd', { areas: [] })
    expect(brushEnd).toHaveBeenCalledWith({ areas: [] })
  })

  it('does not call off on a disposed instance at unmount', async () => {
    const view = render(<Probe events={{ click: vi.fn() }} />)
    await waitFor(() => expect(engine.instance.on).toHaveBeenCalled())
    view.unmount()
    expect(engine.instance.dispose).toHaveBeenCalled()
    expect(engine.instance.off).not.toHaveBeenCalled()
  })

  it('getInstance is the live instance once ready, null after unmount', async () => {
    const view = render(<Probe />)
    await waitFor(() => expect(latest?.status).toBe('ready'))
    const getInstance = latest?.getInstance as () => ChartInstance | null
    expect(getInstance()).toBe(engine.instance)
    view.unmount()
    expect(getInstance()).toBeNull()
  })
})
