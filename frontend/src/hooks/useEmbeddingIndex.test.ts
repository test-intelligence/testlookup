/**
 * `useEmbeddingIndex` (W12 SRCH-02, contract §1.4 / §4.2): Settings › AI loads with `embedding_index` inside
 * `GET /settings/ai`, so the hook asks nothing on load; it polls `GET /settings/ai/embedding-index` every 5 s
 * ONLY while a re-embed is building (also when the page LOADS mid-build), and stops at the first answer that
 * is not building.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

const getEmbeddingIndex = vi.fn()
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: { getEmbeddingIndex: () => getEmbeddingIndex() },
}))

import type { EmbeddingIndex } from '@/services/appSettingsService'
import { EMBEDDING_INDEX_POLL_MS, embeddingIndexRefreshInterval, useEmbeddingIndex } from './useEmbeddingIndex'

const MINILM = { provider: 'default', model: 'all-MiniLM-L6-v2', prefix: '', key: 'default', tag: '' }
const NOMIC = { provider: 'ollama', model: 'nomic-embed-text:v1.5', prefix: '', key: 'ollama:nomic-embed-text:v1.5', tag: 'e7d5a41f5' }
const idle = (active = MINILM): EmbeddingIndex => ({ active, configured: active, in_sync: true, status: 'idle' })
const building = (processed = 0): EmbeddingIndex => ({
  active: MINILM, configured: NOMIC, in_sync: false, status: 'building', target: NOMIC, processed, total: 2290,
})

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

beforeEach(() => {
  getEmbeddingIndex.mockReset()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  vi.useRealTimers()
})

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

describe('useEmbeddingIndex', () => {
  it('polls every 5 s only while building', () => {
    expect(EMBEDDING_INDEX_POLL_MS).toBe(5000)
    expect(embeddingIndexRefreshInterval(building())).toBe(5000)
    expect(embeddingIndexRefreshInterval(idle())).toBe(0)
    expect(embeddingIndexRefreshInterval({ ...idle(), status: 'failed' })).toBe(0)
    expect(embeddingIndexRefreshInterval(null)).toBe(0)
    expect(embeddingIndexRefreshInterval(undefined)).toBe(0)
    // A stalled build has no worker moving it: nothing to poll for until Re-embed now resumes it.
    expect(embeddingIndexRefreshInterval({ ...building(), stalled: true })).toBe(0)
    expect(embeddingIndexRefreshInterval({ ...building(), stalled: false })).toBe(5000)
    expect(embeddingIndexRefreshInterval({ ...building(), stalled: null })).toBe(5000)
  })

  it.each([
    ['idle', idle()],
    ['failed', { ...building(), status: 'failed' } as EmbeddingIndex],
    ['stalled', { ...building(), stalled: true } as EmbeddingIndex],
    ['unreadable (null)', null],
  ])('asks nothing on load or later while %s, and returns what the page passed in', async (_label, initial) => {
    const { result } = renderHook(() => useEmbeddingIndex(initial), { wrapper })
    expect(result.current.index).toEqual(initial)
    await tick(12_000)
    expect(getEmbeddingIndex).not.toHaveBeenCalled()
  })

  it('a page loaded mid-build polls at 5 s and stops at the switch', async () => {
    getEmbeddingIndex.mockResolvedValueOnce(building(1800)).mockResolvedValueOnce(idle(NOMIC)).mockResolvedValue(idle(NOMIC))
    const { result } = renderHook(() => useEmbeddingIndex(building(1200)), { wrapper })
    expect(getEmbeddingIndex).not.toHaveBeenCalled()

    await tick(5_100)
    expect(getEmbeddingIndex).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.index?.processed).toBe(1800))

    await tick(5_100)
    await waitFor(() => expect(result.current.index?.status).toBe('idle'))
    expect(result.current.index?.active.provider).toBe('ollama')
    const polls = getEmbeddingIndex.mock.calls.length

    await tick(20_000)
    expect(getEmbeddingIndex.mock.calls.length).toBe(polls)
  })

  it('a fresh initial wins over an answer cached on an earlier visit, and its build is polled', async () => {
    const cache = new Map()
    const shared = ({ children }: { children: ReactNode }) =>
      createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
    const earlier = renderHook(() => useEmbeddingIndex(idle()), { wrapper: shared })
    await act(async () => { await earlier.result.current.show(idle()) })
    earlier.unmount()

    getEmbeddingIndex.mockResolvedValue(building(1500))
    const { result } = renderHook(() => useEmbeddingIndex(building(1200)), { wrapper: shared })
    await waitFor(() => expect(result.current.index?.status).toBe('building'))
    await tick(5_100)
    expect(getEmbeddingIndex).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.index?.processed).toBe(1500))
  })

  it('show() puts a write\'s answer on screen without a read, and a building one starts the poll', async () => {
    getEmbeddingIndex.mockResolvedValue(idle(NOMIC))
    const { result } = renderHook(() => useEmbeddingIndex(idle()), { wrapper })

    await act(async () => { await result.current.show(building(0)) })
    expect(result.current.index?.status).toBe('building')
    expect(getEmbeddingIndex).not.toHaveBeenCalled()

    await tick(5_100)
    expect(getEmbeddingIndex).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.index?.status).toBe('idle'))
  })
})
