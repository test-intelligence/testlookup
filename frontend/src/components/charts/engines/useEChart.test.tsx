import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { domTooltipFormatter } from '../tooltip'
import { useEChart, type EChartStatus } from './useEChart'

const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./registry', () => ({ loadChartEngine: engine.load }))

class NoopResizeObserver {
  observe() {}
  disconnect() {}
}

function Probe({ option }: { option: object }) {
  const { containerRef, status } = useEChart('heatmap', option)
  return <div ref={containerRef} data-status={status satisfies EChartStatus} />
}

let view: ReturnType<typeof render>
const status = () => view.container.firstElementChild?.getAttribute('data-status')

describe('useEChart refuses an option carrying an unapproved formatter (ADR decision 4)', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', NoopResizeObserver)
    engine.instance.setOption.mockReset()
    engine.instance.dispose.mockReset()
    engine.init.mockClear()
    engine.load.mockReset().mockResolvedValue({ init: engine.init })
  })

  it('draws an option whose formatter came from domTooltipFormatter', async () => {
    view = render(<Probe option={{ tooltip: { formatter: domTooltipFormatter(() => ({ rows: [] })) } }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    expect(engine.instance.setOption).toHaveBeenCalledTimes(1)
  })

  it('never hands ECharts a string formatter on mount: error state, engine not even initialised', async () => {
    view = render(<Probe option={{ tooltip: { formatter: '<img src=x onerror=alert(1)>{b}' } }} />)
    await waitFor(() => expect(status()).toBe('error'))
    expect(engine.init).not.toHaveBeenCalled()
    expect(engine.instance.setOption).not.toHaveBeenCalled()
  })

  it('refuses a hand-written formatter arriving in a later option, and disposes the instance', async () => {
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    await act(async () => {
      view.rerender(<Probe option={{ series: [{ label: { formatter: () => '<b>x</b>' } }] }} />)
    })
    await waitFor(() => expect(status()).toBe('error'))
    expect(engine.instance.setOption).toHaveBeenCalledTimes(1)
    expect(engine.instance.dispose).toHaveBeenCalled()
  })
})

/**
 * VIZ-106 ("no layout thrash: one observer per frame, requestAnimationFrame-
 * batched"): a burst of ResizeObserver callbacks — a window drag, a rotation,
 * the navigation drawer sliding in — re-lays out the canvas ONCE, in the next
 * frame, at the final size.
 */
describe('useEChart resizes at most once per animation frame', () => {
  const observed = { callbacks: [] as Array<() => void> }
  class RecordingResizeObserver {
    constructor(cb: () => void) {
      observed.callbacks.push(cb)
    }
    observe() {}
    disconnect() {}
  }
  const frames = new Map<number, FrameRequestCallback>()
  let nextFrame = 0
  const runFrames = () => {
    const due = [...frames.values()]
    frames.clear()
    for (const cb of due) cb(0)
  }
  const burst = (n: number) => {
    for (let i = 0; i < n; i++) for (const cb of observed.callbacks) cb()
  }

  beforeEach(() => {
    observed.callbacks = []
    frames.clear()
    vi.stubGlobal('ResizeObserver', RecordingResizeObserver)
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.set(++nextFrame, cb)
      return nextFrame
    })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
    engine.instance.resize.mockReset()
    engine.instance.dispose.mockReset()
    engine.load.mockReset().mockResolvedValue({ init: engine.init })
  })

  it('five callbacks in one frame: no resize until the frame, then exactly one', async () => {
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    expect(observed.callbacks).toHaveLength(1)

    burst(5)
    expect(engine.instance.resize).not.toHaveBeenCalled()
    expect(frames.size).toBe(1)
    runFrames()
    expect(engine.instance.resize).toHaveBeenCalledTimes(1)
  })

  it('the next burst gets its own frame: batched, never stuck', async () => {
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    burst(3)
    runFrames()
    burst(4)
    runFrames()
    expect(engine.instance.resize).toHaveBeenCalledTimes(2)
  })

  it('unmounting with a frame pending cancels it: no resize on a disposed instance', async () => {
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    burst(2)
    view.unmount()
    expect(frames.size).toBe(0)
    runFrames()
    expect(engine.instance.resize).not.toHaveBeenCalled()
    expect(engine.instance.dispose).toHaveBeenCalled()
  })

  it('a frame pending when a later option fails never resizes the disposed instance', async () => {
    // The refused option disposes the instance but leaves the observer and its
    // pending frame in place (the mount effect does not re-run): the frame must
    // see that the instance it was queued for is gone.
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    burst(2)
    expect(frames.size).toBe(1)
    await act(async () => {
      view.rerender(<Probe option={{ tooltip: { formatter: 'x' } }} />)
    })
    await waitFor(() => expect(status()).toBe('error'))
    expect(engine.instance.dispose).toHaveBeenCalledTimes(1)
    expect(frames.size).toBe(1)
    runFrames()
    expect(engine.instance.resize).not.toHaveBeenCalled()
  })
})
