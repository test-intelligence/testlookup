/**
 * W23b FAIL-12: the bisect service and its hook.
 *
 * The request path is the backend contract (the SPA wire-contract guard reads it), so it is pinned here in
 * full with its params. The SWR key is the request URL: `null` (no request) without a run or a fingerprint,
 * the fingerprint URL-encoded. The hook parses the answer (a malformed one is `data: null` WITH an error),
 * never polls, reads a 404 once (no automatic retry) and reads again on `retry()`.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { getBisect } from './bisectService'
import { MALFORMED_BISECT, bisectKey, useBisectNarrowing } from '@/hooks/useBisectNarrowing'
import { fetchedCompare, narrowing } from '@/test/bisectDriftMocks'

const RUN = '00000000-0000-4000-8000-00000000b0a1'
const FP = 'fp-sentinel-checkout'

// A 10 ms error-retry interval: were the hook to retry a failed read, it would within the 100 ms waited below.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    SWRConfig,
    { value: { provider: () => new Map(), dedupingInterval: 0, errorRetryInterval: 10 } },
    children,
  )
}

describe('bisectService.getBisect', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
  })

  it('reads the literal path with the fingerprint as a query param, and returns the raw answer', async () => {
    const answer = narrowing({ run_id: RUN })
    apiMock.get.mockResolvedValue({ data: answer })
    await expect(getBisect(RUN, FP)).resolves.toBe(answer)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/runs/${RUN}/bisect`, { params: { fingerprint: FP } })
  })

  it('never writes', async () => {
    apiMock.get.mockResolvedValue({ data: {} })
    await getBisect(RUN, FP)
    expect(apiMock.post).not.toHaveBeenCalled()
    expect(apiMock.patch).not.toHaveBeenCalled()
    expect(apiMock.delete).not.toHaveBeenCalled()
  })
})

describe('bisectKey', () => {
  it('is the request URL', () => {
    expect(bisectKey(RUN, FP)).toBe(`/api/v1/runs/${RUN}/bisect?fingerprint=fp-sentinel-checkout`)
  })

  it('URL-encodes the fingerprint', () => {
    expect(bisectKey(RUN, 'a b&c=d/e?f#g')).toBe(`/api/v1/runs/${RUN}/bisect?fingerprint=a%20b%26c%3Dd%2Fe%3Ff%23g`)
  })

  it('is null without a run or a fingerprint (no request)', () => {
    expect(bisectKey(null, FP)).toBeNull()
    expect(bisectKey(RUN, null)).toBeNull()
    expect(bisectKey('', FP)).toBeNull()
    expect(bisectKey(RUN, '')).toBeNull()
    expect(bisectKey(null, null)).toBeNull()
  })
})

describe('useBisectNarrowing', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
  })

  it('asks nothing without a run or a fingerprint', async () => {
    const { result } = renderHook(() => useBisectNarrowing(null, FP), { wrapper })
    const { result: noFp } = renderHook(() => useBisectNarrowing(RUN, null), { wrapper })
    await act(async () => {})
    expect(apiMock.get).not.toHaveBeenCalled()
    expect(result.current.data).toBeNull()
    expect(noFp.current.data).toBeNull()
    expect(result.current.error).toBeUndefined()
  })

  it('parses the answer', async () => {
    apiMock.get.mockResolvedValue({ data: narrowing({ run_id: RUN, compare: fetchedCompare() }) })
    const { result } = renderHook(() => useBisectNarrowing(RUN, FP), { wrapper })
    await waitFor(() => expect(result.current.data).not.toBeNull())
    expect(result.current.data?.state).toBe('narrowed')
    expect(result.current.data?.compare.state).toBe('fetched')
    expect(result.current.data?.compare.commits).toHaveLength(3)
    expect(result.current.error).toBeUndefined()
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/runs/${RUN}/bisect`, { params: { fingerprint: FP } })
  })

  it('reads a malformed answer as data: null WITH an error (the block says it could not load)', async () => {
    apiMock.get.mockResolvedValue({ data: { run_id: RUN, fingerprint: FP, state: 'bisecting' } })
    const { result } = renderHook(() => useBisectNarrowing(RUN, FP), { wrapper })
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error))
    expect((result.current.error as Error).message).toBe(MALFORMED_BISECT)
    expect(result.current.data).toBeNull()
  })

  it('reads a 404 once (no automatic retry), and again on retry()', async () => {
    const notInRun = Object.assign(new Error('Request failed with status code 404'), {
      response: { status: 404, data: { detail: { code: 'fingerprint_not_in_run', message: 'That test is not part of this run.' } } },
    })
    apiMock.get.mockRejectedValue(notInRun)
    const { result } = renderHook(() => useBisectNarrowing(RUN, FP), { wrapper })
    await waitFor(() => expect(result.current.error).toBe(notInRun))
    expect(result.current.data).toBeNull()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100))
    })
    expect(apiMock.get).toHaveBeenCalledTimes(1)

    apiMock.get.mockResolvedValue({ data: narrowing({ run_id: RUN }) })
    await act(async () => {
      await result.current.retry()
    })
    expect(apiMock.get).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(result.current.data?.state).toBe('narrowed'))
  })
})
