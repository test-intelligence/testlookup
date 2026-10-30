/**
 * W23d AGT-14: the HTTP-exchanges service and its hook through the REAL SWR.
 *
 * The request path is the backend contract (the SPA wire-contract guard reads it), so it is pinned here in
 * full; the read passes `suppressToast: true` (the panel renders a failed read itself). The hook asks NOTHING
 * until enabled (the `HTTP exchanges` tab is open), never polls, reads the answer through `parseExchangeList`
 * (a malformed answer is `data: null` WITH an error, never "none captured") and re-reads only on `mutate()`.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { getHttpExchanges } from './httpExchangesService'
import { MALFORMED_HTTP_EXCHANGES, httpExchangesKey, useHttpExchanges } from '@/hooks/useHttpExchanges'
import { emptyExchangeList, exchangeList } from '@/test/payloadMetricsMocks'

const RUN_ID = 'run-sentinel-checkout'
const TEST_ID = 'test-sentinel-lamp'
const PATH = '/api/v1/runs/run-sentinel-checkout/tests/test-sentinel-lamp/http-exchanges'
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

beforeEach(() => {
  apiMock.get.mockReset().mockResolvedValue({ data: exchangeList() })
})

describe('httpExchangesService', () => {
  it('GETs the literal path, with no global toast, and returns the raw answer', async () => {
    const answer = exchangeList()
    apiMock.get.mockResolvedValue({ data: answer })
    await expect(getHttpExchanges(RUN_ID, TEST_ID)).resolves.toBe(answer)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.get).toHaveBeenCalledWith(PATH, { suppressToast: true })
  })

  it('a refused read rejects (the hook turns it into the panel error)', async () => {
    apiMock.get.mockRejectedValue(Object.assign(new Error('Request failed with status code 503'), { response: { status: 503 } }))
    await expect(getHttpExchanges(RUN_ID, TEST_ID)).rejects.toThrow('503')
  })
})

describe('useHttpExchanges', () => {
  it('issues nothing while disabled (the tab is closed), nor without both ids', async () => {
    const { result } = renderHook(() => useHttpExchanges(RUN_ID, TEST_ID, false), { wrapper })
    renderHook(() => useHttpExchanges(RUN_ID, undefined, true), { wrapper })
    renderHook(() => useHttpExchanges(null, TEST_ID, true), { wrapper })
    await pause(30)
    expect(apiMock.get).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
    expect(result.current.error).toBeUndefined()
    expect(httpExchangesKey(RUN_ID, TEST_ID, false)).toBeNull()
    expect(httpExchangesKey(RUN_ID, TEST_ID, true)).toBe(PATH)
  })

  it('once enabled, reads ONCE and answers the parsed list; no polling afterwards', async () => {
    const { result } = renderHook(() => useHttpExchanges(RUN_ID, TEST_ID, true), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual(exchangeList()))
    expect(result.current.error).toBeUndefined()
    expect(result.current.isLoading).toBe(false)
    await pause(60)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.get).toHaveBeenCalledWith(PATH, { suppressToast: true })
  })

  it('an empty answer is a list with total 0, not an error', async () => {
    apiMock.get.mockResolvedValue({ data: emptyExchangeList() })
    const { result } = renderHook(() => useHttpExchanges(RUN_ID, TEST_ID, true), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual(emptyExchangeList()))
    expect(result.current.error).toBeUndefined()
  })

  it('a malformed answer (no items array) is data null WITH an error — never "none captured"', async () => {
    apiMock.get.mockResolvedValue({ data: '<html>proxy</html>' })
    const { result } = renderHook(() => useHttpExchanges(RUN_ID, TEST_ID, true), { wrapper })
    await waitFor(() => expect(result.current.data).toBeNull())
    expect((result.current.error as Error).message).toBe(MALFORMED_HTTP_EXCHANGES)
  })

  it('a failed read is an error, not retried on its own; mutate() is the explicit Retry', async () => {
    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('Request failed with status code 503'), { response: { status: 503 } }))
    const { result } = renderHook(() => useHttpExchanges(RUN_ID, TEST_ID, true), { wrapper })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.data).toBeUndefined()
    await pause(60)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    await act(async () => { await result.current.mutate() })
    await waitFor(() => expect(result.current.data).toEqual(exchangeList()))
    expect(result.current.error).toBeUndefined()
    expect(apiMock.get).toHaveBeenCalledTimes(2)
  })
})
