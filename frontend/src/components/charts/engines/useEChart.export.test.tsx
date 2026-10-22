/**
 * TRND-11: `useEChart`'s 4th parameter. The export option is registered for
 * the chart's container once the instance is ready (never before), read
 * through a ref (a new function each render does not re-register, and the
 * LATEST builder is the one called), and gone after a failure or an unmount.
 * Without the parameter nothing is registered: every other chart is as it was.
 */
import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartTokens } from '../tokens'
import * as registry from './echarts/exportRegistry'
import { exportOptionOf } from './echarts/exportRegistry'
import { useEChart, type ExportOptionBuilder } from './useEChart'

const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./registry', () => ({ loadChartEngine: engine.load }))

class NoopResizeObserver {
  observe() {}
  disconnect() {}
}

function Probe({ option, exportOption }: { option: object; exportOption?: ExportOptionBuilder }) {
  const { containerRef, status } = useEChart('heatmap', option, undefined, exportOption)
  return <div ref={containerRef} data-status={status} />
}

const TOKENS = { card: 'ivory', text: 'navy' } as unknown as ChartTokens

describe('useEChart registers the chart’s export option (TRND-11)', () => {
  let view: ReturnType<typeof render>
  const container = () => view.container.firstElementChild as HTMLElement
  const status = () => container().getAttribute('data-status')

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', NoopResizeObserver)
    engine.instance.setOption.mockReset()
    engine.instance.dispose.mockReset()
    engine.init.mockClear()
    engine.load.mockReset().mockResolvedValue({ init: engine.init })
  })

  it('registers on ready, for the container, building with the given tokens; gone after unmount', async () => {
    let release: (value: unknown) => void = () => {}
    engine.load.mockReturnValue(new Promise((resolve) => (release = resolve)))
    const build = vi.fn((tokens: ChartTokens) => ({ series: [], backgroundColor: tokens.card }))
    view = render(<Probe option={{ series: [] }} exportOption={build} />)
    // Not before the instance exists.
    expect(exportOptionOf(container())).toBeNull()
    await act(async () => release({ init: engine.init }))
    await waitFor(() => expect(status()).toBe('ready'))
    const entry = exportOptionOf(container())
    expect(entry?.type).toBe('heatmap')
    expect(entry?.build(TOKENS)).toEqual({ series: [], backgroundColor: 'ivory' })
    expect(build).toHaveBeenCalledWith(TOKENS)
    const el = container()
    view.unmount()
    expect(exportOptionOf(el)).toBeNull()
  })

  it('a re-render with a new function does not re-register, and the latest builder is the one used', async () => {
    const register = vi.spyOn(registry, 'registerExportOption')
    const first = vi.fn(() => ({ first: true }))
    view = render(<Probe option={{ series: [] }} exportOption={first} />)
    await waitFor(() => expect(status()).toBe('ready'))
    // The registration is a passive effect of the 'ready' render: under CI load the DOM can read 'ready' before it
    // has run (it failed in W29b's PR CI at "expected 0 to be greater than 0"), so wait for the registration itself.
    await waitFor(() => expect(register.mock.calls.length).toBeGreaterThan(0))
    const calls = register.mock.calls.length
    const second = vi.fn(() => ({ second: true }))
    await act(async () => view.rerender(<Probe option={{ series: [] }} exportOption={second} />))
    expect(register.mock.calls.length).toBe(calls)
    expect(exportOptionOf(container())?.build(TOKENS)).toEqual({ second: true })
    expect(first).not.toHaveBeenCalled()
    register.mockRestore()
  })

  it('a failed option takes the registration with it', async () => {
    view = render(<Probe option={{ series: [] }} exportOption={() => ({})} />)
    await waitFor(() => expect(status()).toBe('ready'))
    expect(exportOptionOf(container())).not.toBeNull()
    await act(async () => view.rerender(<Probe option={{ series: [{ label: { formatter: () => 'x' } }] }} exportOption={() => ({})} />))
    await waitFor(() => expect(status()).toBe('error'))
    expect(exportOptionOf(container())).toBeNull()
  })

  it('without the parameter nothing is registered', async () => {
    view = render(<Probe option={{ series: [] }} />)
    await waitFor(() => expect(status()).toBe('ready'))
    expect(exportOptionOf(container())).toBeNull()
  })
})
