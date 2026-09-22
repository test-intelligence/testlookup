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
