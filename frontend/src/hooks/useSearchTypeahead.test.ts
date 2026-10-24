/**
 * W12 SRCH-03 — the typeahead's read, through the REAL search service, http
 * helpers and response interceptor (a stubbed axios adapter): the debounce,
 * the size, no `record`, and a failed read that raises NO global toast (the
 * list says "Could not load matches." itself).
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))

const { api } = await import('@/services/api')
const { TYPEAHEAD_DEBOUNCE_MS, useSearchTypeahead } = await import('./useSearchTypeahead')

const asked: { url?: string; params?: Record<string, unknown>; suppressToast?: boolean }[] = []
let fail: number | null = null

function stubAdapter() {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    asked.push({ url: config.url, params: config.params as Record<string, unknown>, suppressToast: config.suppressToast })
    if (fail !== null) {
      const response = { status: fail, statusText: 'Error', data: { detail: 'Internal Server Error' }, headers: {}, config } as AxiosResponse
      throw new AxiosError(`Request failed with status code ${fail}`, 'ERR_BAD_RESPONSE', config, {}, response)
    }
    return {
      status: 200, statusText: 'OK', headers: {}, config,
      data: { items: [{ title: 'old' }], total: 1, query: 'x', search_type: 'keyword', entity_counts: {}, page: 1, size: 5, pages: 1 },
    } as AxiosResponse
  }
}

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
}

beforeEach(() => {
  vi.useFakeTimers()
  toastError.mockReset()
  asked.length = 0
  fail = null
  stubAdapter()
})
afterEach(() => {
  vi.useRealTimers()
  api.defaults.adapter = undefined
})

describe('useSearchTypeahead (W12 SRCH-03)', () => {
  it('settles 250 ms after the last change, then reads once (size 15, no record); no key while typing', async () => {
    const { result, rerender } = renderHook(({ q }) => useSearchTypeahead(q, 'p1', true), { wrapper, initialProps: { q: 'ch' } })
    expect(result.current.settling).toBe(true)
    expect(result.current.debounced).toBeNull()
    await advance(TYPEAHEAD_DEBOUNCE_MS - 10)
    rerender({ q: 'che' })
    await advance(TYPEAHEAD_DEBOUNCE_MS - 10)
    expect(asked).toEqual([])
    await advance(20)
    expect(result.current.debounced).toBe('che')
    expect(asked).toHaveLength(1)
    expect(asked[0].url).toBe('/api/v1/search/global')
    expect(asked[0].params).toEqual({ q: 'che', project_id: 'p1', size: 15, entity_types: undefined })
    expect(asked[0].params).not.toHaveProperty('record')
  })

  it('a changed text drops the previous answer until it settles again', async () => {
    const { result, rerender } = renderHook(({ q }) => useSearchTypeahead(q, null, true), { wrapper, initialProps: { q: 'check' } })
    await advance(TYPEAHEAD_DEBOUNCE_MS + 10)
    expect(result.current.data?.items).toHaveLength(1)
    expect(asked[0].params).not.toHaveProperty('project_id')
    rerender({ q: 'checkout' })
    await advance(50)
    expect(result.current.debounced).toBeNull()
    expect(result.current.data).toBeUndefined()
    expect(result.current.settling).toBe(true)
  })

  it('under 2 characters, or closed: no key, no request', async () => {
    const short = renderHook(() => useSearchTypeahead(' c ', 'p1', true), { wrapper })
    const closed = renderHook(() => useSearchTypeahead('checkout', 'p1', false), { wrapper })
    await advance(1_000)
    expect(short.result.current.debounced).toBeNull()
    expect(closed.result.current.debounced).toBeNull()
    expect(asked).toEqual([])
  })

  it('a failed read (500) is the hook\'s error and raises NO global toast', async () => {
    fail = 500
    const { result } = renderHook(() => useSearchTypeahead('checkout', 'p1', true), { wrapper })
    await advance(TYPEAHEAD_DEBOUNCE_MS + 10)
    await advance(10)
    expect(asked).toHaveLength(1)
    expect(asked[0].suppressToast).toBe(true)
    expect(result.current.error).toBeDefined()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('the interceptor still toasts an ordinary global search (the opt-out does not leak)', async () => {
    fail = 500
    const { searchService } = await import('@/services/searchService')
    await expect(searchService.globalSearch({ q: 'checkout' })).rejects.toBeDefined()
    expect(toastError).toHaveBeenCalledTimes(1)
  })
})
